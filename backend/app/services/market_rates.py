"""Dựng đơn giá tham chiếu (`construction_rates`) từ đơn giá nhà thầu công bố trên hồ sơ Dezon.

Dezon không công bố bảng giá; đơn giá khái toán chỉ lấy từ dữ liệu thật trên dezon.vn:
- `xay_tho`  <- `contractors.rough_price_min/max` ("Đơn giá xây thô").
- `tron_goi` <- `contractors.finish_price_min/max` ("Đơn giá hoàn thiện cơ bản" = trọn gói thô + hoàn thiện).
- `hoan_thien`, `noi_that`: không có dữ liệu -> không tạo đơn giá ("Chưa có dữ liệu").
Không phân mức chất lượng: mọi dòng là `quality_tier='trung_binh'`.

Nhà thầu thuộc loại công trình X nếu có năng lực (`contractor_capabilities`) loại X (ở bất kỳ tỉnh nào).
Tỉnh của nhà thầu = tỉnh trên hồ sơ + tỉnh năng lực; không có thì lấy tỉnh các công trình của nhà thầu.
Mỗi (loại công trình, tỉnh, phạm vi) có ≥ `MIN_CONTRACTORS_FOR_RATE` nhà thầu có giá -> một dòng đơn giá;
thêm dòng toàn quốc (`province='*'`) tổng hợp trên mọi nhà thầu thuộc loại công trình đó.
Cách tổng hợp: `app.engines.estimate.market.aggregate_price_range`.
"""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db.models import (
    ConstructionRate,
    Contractor,
    DataSource,
    EstimateSession,
    Project,
    ProjectContractor,
)
from app.engines.estimate.market import (
    MARKET_LABEL_PREFIX,
    MIN_CONTRACTORS_FOR_RATE,
    SEED_DEV_LABEL_PREFIX,
    aggregate_price_range,
    contractor_bounds,
    is_market_label,
    is_seed_dev_label,
)
from app.evidence import SourceType, VerificationStatus
from app.ingestion.parse_vi import fold_vi, normalize_province
from app.schemas.common import BUILDING_TYPE_VI, QualityTier, WorkScope

log = logging.getLogger(__name__)

__all__ = ["MARKET_LABEL_PREFIX", "NATIONWIDE", "RETIRED_TIERS", "SCOPE_PRICE_FIELDS", "SEED_DEV_LABEL_PREFIX",
           "displace_market_rate", "is_market_label", "is_seed_dev_label", "market_label", "market_source_url",
           "rate_usage", "rebuild_market_rates", "seed_dev_rates_allowed"]

NATIONWIDE = "*"
# Biến môi trường cho phép dùng đơn giá mẫu seed dev (bảng giá tự đặt). Chỉ test đặt =1 (tests/conftest.py);
# môi trường thật không bao giờ dùng các dòng này dù chúng còn hiệu lực trong DB cũ.
ALLOW_SEED_DEV_RATES_ENV = "ALLOW_SEED_DEV_RATES"
CONTRACTOR_LIST_PATH = "/nha-thau/tiem-nang/"

# Phạm vi -> (cột cận dưới, cột cận trên, tên phạm vi trong nhãn nguồn)
SCOPE_PRICE_FIELDS: dict[WorkScope, tuple[str, str, str]] = {
    WorkScope.xay_tho: ("rough_price_min", "rough_price_max", "xây thô"),
    WorkScope.tron_goi: ("finish_price_min", "finish_price_max", "trọn gói (hoàn thiện cơ bản)"),
}


def seed_dev_rates_allowed() -> bool:
    return os.environ.get(ALLOW_SEED_DEV_RATES_ENV) == "1"


def market_label(scope: WorkScope, n: int, building_type: str, province: str) -> str:
    scope_vi = SCOPE_PRICE_FIELDS[scope][2]
    bt_vi = BUILDING_TYPE_VI.get(building_type, building_type).lower()
    where = "trên toàn quốc" if province == NATIONWIDE else f"tại {province}"
    return f"{MARKET_LABEL_PREFIX}{scope_vi} do {n} nhà thầu {bt_vi} {where} công bố trên Dezon"


def market_source_url(base_url: str, building_type: str, scope: WorkScope, province: str, day: date) -> str:
    """URL nguồn của một dòng đơn giá: trang danh sách nhà thầu + fragment riêng (data_sources.url là UNIQUE).
    Vd https://dezon.vn/nha-thau/tiem-nang/#don-gia-nha_pho-xay_tho-da-nang-20261102."""
    prov = "toan-quoc" if province == NATIONWIDE else re.sub(r"[^a-z0-9]+", "-", fold_vi(province)).strip("-")
    return f"{base_url}#don-gia-{building_type}-{WorkScope(scope).value}-{prov}-{day:%Y%m%d}"


@dataclass
class _Info:
    contractor: Contractor
    provinces: set[str] = field(default_factory=set)
    building_types: set[str] = field(default_factory=set)

    @property
    def updated_at(self) -> datetime | None:
        src = self.contractor.source
        if src is not None:
            return src.source_updated_at or src.fetched_at
        return self.contractor.updated_at


def _collect(db: Session) -> list[_Info]:
    contractors = db.scalars(
        select(Contractor).options(selectinload(Contractor.capabilities), selectinload(Contractor.source))
        .where(Contractor.is_active.is_(True)).order_by(Contractor.id)
    ).all()
    project_provinces: dict[int, set[str]] = defaultdict(set)
    for cid, prov in db.execute(
            select(ProjectContractor.contractor_id, Project.province)
            .join(Project, Project.id == ProjectContractor.project_id)
            .where(Project.province.is_not(None))):
        canon = normalize_province(prov)
        if canon:
            project_provinces[cid].add(canon)
    out: list[_Info] = []
    for c in contractors:
        info = _Info(contractor=c)
        for raw in [c.province, *(cap.province for cap in c.capabilities)]:
            canon = normalize_province(raw)
            if canon:
                info.provinces.add(canon)
        if not info.provinces:
            info.provinces = set(project_provinces.get(c.id, set()))
        info.building_types = {cap.building_type for cap in c.capabilities if cap.building_type}
        out.append(info)
    return out


def _expire_old(db: Session, today: date, keep_ids: set[int]) -> int:
    """Đặt effective_to = hôm qua cho dòng đơn giá tổng hợp cũ và đơn giá mẫu seed dev còn hiệu lực
    (không xóa: estimate_sessions.rate_id tham chiếu tới)."""
    yesterday = today - timedelta(days=1)
    rows = db.scalars(
        select(ConstructionRate).join(DataSource, DataSource.id == ConstructionRate.source_id)
        .where(or_(DataSource.label.startswith(MARKET_LABEL_PREFIX, autoescape=True),
                   DataSource.label.startswith(SEED_DEV_LABEL_PREFIX, autoescape=True)),
               or_(ConstructionRate.effective_to.is_(None), ConstructionRate.effective_to >= today))
    ).all()
    n = 0
    for r in rows:
        if r.id in keep_ids:
            continue
        # Dòng khái toán đã dùng được hiệu lực từ effective_from: không ghi ngày hết hiệu lực trước ngày đó
        # (cửa sổ rỗng sẽ sai sự thật). Dòng chưa dùng thì ẩn ngay (cửa sổ rỗng, không ai tham chiếu).
        end = max(yesterday, r.effective_from) if rate_usage(db, r.id) else yesterday
        if r.effective_to == end:
            continue
        r.effective_to = end
        n += 1
    return n


def rate_usage(db: Session, rate_id: int) -> int:
    """Số khái toán đã lưu (`estimate_sessions.rate_id`) tham chiếu dòng đơn giá."""
    return int(db.scalar(select(func.count()).select_from(EstimateSession)
                         .where(EstimateSession.rate_id == rate_id)) or 0)


def _reset_verification(src: DataSource) -> None:
    src.status = VerificationStatus.on_dezon.value
    src.verified_by = None
    src.verified_at = None


def _previous_market_row(db: Session, bt: str, prov: str, scope: WorkScope, today: date) -> ConstructionRate | None:
    """Dòng tổng hợp gần nhất trước hôm nay cùng (loại công trình, tỉnh, phạm vi)."""
    return db.scalars(
        select(ConstructionRate).options(selectinload(ConstructionRate.source))
        .join(DataSource, DataSource.id == ConstructionRate.source_id)
        .where(ConstructionRate.building_type == bt, ConstructionRate.province == prov,
               ConstructionRate.work_scope == scope.value,
               ConstructionRate.quality_tier == QualityTier.trung_binh.value,
               ConstructionRate.effective_from < today,
               DataSource.label.startswith(MARKET_LABEL_PREFIX, autoescape=True))
        .order_by(ConstructionRate.effective_from.desc(), ConstructionRate.id.desc()).limit(1)
    ).first()


# Mức chất lượng "đỗ" cho dòng tổng hợp đã bị đơn giá chính thức thay thế (xem displace_market_rate). Hệ thống không
# phân mức (mọi đơn giá dùng 'trung_binh'; select_rate/rebuild chỉ đọc 'trung_binh') nên dòng đỗ ở mức khác không bao
# giờ được chọn lại, trong khi giá, nguồn và ngày hiệu lực giữ nguyên đúng như lúc khái toán dùng nó.
RETIRED_TIERS: tuple[str, ...] = tuple(t.value for t in QualityTier if t is not QualityTier.trung_binh)


def displace_market_rate(db: Session, row: ConstructionRate) -> str:
    """Nhường khóa UNIQUE (loại công trình, tỉnh, phạm vi, mức, ngày hiệu lực) của một dòng TỔNG HỢP cho đơn giá
    chính thức (CSV/admin) cùng ngày — đơn giá chính thức luôn đứng trước đơn giá tổng hợp.

    - Chưa có khái toán nào tham chiếu -> xóa dòng, trả "deleted".
    - Đã được khái toán tham chiếu -> KHÔNG đổi giá, nguồn, ngày hiệu lực/hết hiệu lực (dấu vết của khái toán đã
      lưu); chỉ chuyển dòng sang một mức chất lượng không dùng (`RETIRED_TIERS`) để nhả khóa. select_rate và
      rebuild_market_rates chỉ đọc mức 'trung_binh' nên dòng này coi như đã bị thay thế. Trả "retired".
    Dòng không phải tổng hợp -> ValueError (không được đụng tới đơn giá chính thức)."""
    label = row.source.label if row.source is not None else None
    if not is_market_label(label):
        raise ValueError("Chỉ nhường khóa cho dòng đơn giá tổng hợp từ hồ sơ nhà thầu")
    if rate_usage(db, row.id) == 0:
        db.delete(row)
        db.flush()
        return "deleted"
    taken = set(db.scalars(select(ConstructionRate.quality_tier).where(
        ConstructionRate.building_type == row.building_type, ConstructionRate.province == row.province,
        ConstructionRate.work_scope == row.work_scope,
        ConstructionRate.effective_from == row.effective_from)).all())
    free = next((t for t in RETIRED_TIERS if t not in taken and t != str(row.quality_tier)), None)
    if free is None:
        raise ValueError("Không còn chỗ để lưu dòng đơn giá tổng hợp đã bị thay thế cùng ngày")
    row.quality_tier = free
    db.flush()
    return "retired"


def rebuild_market_rates(db: Session, today: date | None = None) -> dict[str, Any]:
    """Tính lại đơn giá tổng hợp từ hồ sơ nhà thầu; trả thống kê. Không commit (người gọi commit).

    - Dòng đã được khái toán tham chiếu (`estimate_sessions.rate_id`) không bao giờ bị sửa giá/nhãn: chạy lại trong
      ngày mà số liệu đổi thì giữ nguyên dòng đó, số mới áp dụng từ lần dựng lại ngày sau (UNIQUE theo ngày).
    - Duyệt (nguồn `verified`) được giữ khi giá không đổi — cả khi chạy lại trong ngày lẫn khi sang dòng ngày mới.
    - Đã có đơn giá chính thức cùng khóa trong ngày -> không tạo dòng tổng hợp (`skipped_official`)."""
    today = today or date.today()
    now = datetime.now(UTC)
    site = get_settings().dezon_site_url.rstrip("/")
    list_url = f"{site}{CONTRACTOR_LIST_PATH}"
    infos = _collect(db)
    building_types = sorted({bt for i in infos for bt in i.building_types})

    # (building_type, province, scope) -> danh sách nhà thầu có giá
    groups: dict[tuple[str, str, WorkScope], list[tuple[_Info, tuple]]] = defaultdict(list)
    for bt in building_types:
        members = [i for i in infos if bt in i.building_types]
        for scope, (f_min, f_max, _) in SCOPE_PRICE_FIELDS.items():
            for i in members:
                rng = (getattr(i.contractor, f_min), getattr(i.contractor, f_max))
                if contractor_bounds(*rng) is None:
                    continue
                groups[(bt, NATIONWIDE, scope)].append((i, rng))
                for prov in i.provinces:
                    groups[(bt, prov, scope)].append((i, rng))

    kept: set[int] = set()
    created = updated = skipped_official = kept_in_use = 0
    rows_out: list[dict[str, Any]] = []
    for (bt, prov, scope), members in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] != NATIONWIDE,
                                                                             kv[0][1], kv[0][2].value)):
        agg = aggregate_price_range([rng for _, rng in members], min_n=MIN_CONTRACTORS_FOR_RATE)
        if agg is None:
            continue
        n = len(members)
        label = market_label(scope, n, bt, prov)
        updated_dates = [d for d in (i.updated_at for i, _ in members) if d is not None]
        src_updated = max(updated_dates) if updated_dates else now
        source_url = market_source_url(list_url, bt, scope, prov, today)
        same_day = db.scalars(
            select(ConstructionRate).options(selectinload(ConstructionRate.source))
            .where(ConstructionRate.building_type == bt, ConstructionRate.province == prov,
                   ConstructionRate.work_scope == scope.value,
                   ConstructionRate.quality_tier == QualityTier.trung_binh.value,
                   ConstructionRate.effective_from == today)
        ).first()
        if same_day is not None and not is_market_label(same_day.source.label if same_day.source else None):
            skipped_official += 1  # đã có đơn giá chính thức cùng ngày -> giữ nguyên (ràng buộc UNIQUE)
            continue
        if same_day is not None:  # chạy lại trong ngày
            row = same_day
            src = same_day.source
            same_numbers = (int(same_day.price_min), int(same_day.price_max)) == agg
            if rate_usage(db, same_day.id):
                unchanged = same_numbers and src.label == label
                if unchanged:
                    same_day.effective_to = None
                    src.fetched_at = now
                else:
                    kept_in_use += 1  # khái toán đã lưu tham chiếu dòng này -> không sửa giá/nguồn
                kept.add(row.id)
                rows_out.append({"building_type": bt, "province": prov, "work_scope": scope.value,
                                 "price_min": int(row.price_min), "price_max": int(row.price_max),
                                 "contractors": n, "kept_in_use": not unchanged})
                continue
            same_day.price_min, same_day.price_max, same_day.effective_to = agg[0], agg[1], None
            src.label, src.url, src.source_updated_at, src.fetched_at = label, source_url, src_updated, now
            src.source_type = SourceType.crawl_public.value
            if not (same_numbers and src.status == VerificationStatus.verified.value):
                _reset_verification(src)  # số liệu đổi -> duyệt cũ không còn áp dụng
            updated += 1
        else:
            prev = _previous_market_row(db, bt, prov, scope, today)
            src = db.scalar(select(DataSource).where(DataSource.url == source_url))  # nguồn mồ côi cùng URL
            if src is None:
                src = DataSource(url=source_url)
                db.add(src)
            src.source_type = SourceType.crawl_public.value
            _reset_verification(src)
            if (prev is not None and prev.source is not None
                    and prev.source.status == VerificationStatus.verified.value
                    and (int(prev.price_min), int(prev.price_max)) == agg):
                # cùng số liệu với dòng trước đã được admin duyệt -> giữ duyệt
                src.status = prev.source.status
                src.verified_by, src.verified_at = prev.source.verified_by, prev.source.verified_at
            src.label, src.source_updated_at, src.fetched_at = label, src_updated, now
            db.flush()
            row = ConstructionRate(building_type=bt, province=prov, work_scope=scope.value,
                                   quality_tier=QualityTier.trung_binh.value, price_min=agg[0],
                                   price_max=agg[1], effective_from=today, source_id=src.id)
            db.add(row)
            db.flush()
            created += 1
        kept.add(row.id)
        rows_out.append({"building_type": bt, "province": prov, "work_scope": scope.value,
                         "price_min": agg[0], "price_max": agg[1], "contractors": n})

    expired = _expire_old(db, today, kept)
    db.flush()
    stats = {"status": "ok", "created": created, "updated": updated, "expired": expired,
             "skipped_official": skipped_official, "kept_in_use": kept_in_use, "contractors": len(infos),
             "rates": rows_out}
    log.info("rebuild_market_rates: created=%s updated=%s expired=%s", created, updated, expired)
    return stats
