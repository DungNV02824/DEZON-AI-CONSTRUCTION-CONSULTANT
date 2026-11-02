"""Nghiệp vụ admin tối thiểu (đặc tả mục 11.2 bước 3–6, 14 bước 11).

- Đơn giá (`construction_rates`): CRUD; "duyệt" đơn giá = xác minh nguồn của nó (bảng không có cột approved,
  trạng thái hiển thị lấy từ data_sources.status — mục 4.4).
- Hệ số (`estimate_rules`): tạo phiên bản mới (version = max + 1 theo code), bật/tắt approved. Không sửa hệ số
  của một phiên bản đã có — khái toán cũ tham chiếu rules_version.
- Trọng số matching: tạo phiên bản mới, kích hoạt; luôn đúng 1 phiên bản active, đủ 6 khóa, tổng = 1 ± 0.001.
- Nguồn dữ liệu: liệt kê, xác minh (status → verified, verified_by, verified_at).
- Đồng bộ: xếp job RQ (`app.jobs.enqueue`); nhập CSV/XLSX trực tiếp bằng `import_table`.

Hàm ném `AdminError` (status_code 404/409/422/503) để tầng API đổi thành HTTP tương ứng.
"""
from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, selectinload

from app import jobs
from app.config import get_settings
from app.db.models import (
    ConstructionRate,
    DataSource,
    EstimateRule,
    EstimateSession,
    GuardrailViolation,
    MatchingWeights,
    RecommendationLog,
)
from app.engines.matching.scorer import CRITERIA_KEYS, CRITERIA_LABEL_VI
from app.evidence import SourceType, VerificationStatus
from app.ingestion.csv_import import MAX_PRICE_PER_M2, MIN_PRICE_PER_M2, RULE_BASES, RULE_GROUPS, import_table
from app.schemas.common import BuildingType, QualityTier, WorkScope

WEIGHT_SUM_TOLERANCE = 0.001
MAX_COEFFICIENT = Decimal("99.999")  # NUMERIC(5,3)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
UPLOAD_EXTENSIONS = (".csv", ".xlsx", ".xlsm")
IMPORT_KINDS = ("rates", "rules")
SYNC_JOBS: dict[str, str] = {"api": "api_sync", "crawl": "crawl", "reindex": "reindex_all",
                             "after_sync": "after_sync"}
SYNC_SOURCES = ("csv", *SYNC_JOBS)
_WEIGHTS_LOCK_KEY = 7_310_001  # pg_advisory_xact_lock: tuần tự hóa tạo/kích hoạt trọng số


class AdminError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _not_found(what: str) -> AdminError:
    return AdminError(404, f"Không tìm thấy {what}.")


def _now() -> datetime:
    return datetime.now(UTC)


# ================================================================================================
# Nguồn dữ liệu
# ================================================================================================
class SourceOut(BaseModel):
    id: int
    source_type: str
    url: str | None = None
    label: str
    status: str
    fetched_at: datetime | None = None
    source_updated_at: datetime | None = None
    verified_by: str | None = None
    verified_at: datetime | None = None

    @classmethod
    def of(cls, ds: DataSource) -> SourceOut:
        return cls(id=ds.id, source_type=str(ds.source_type), url=ds.url, label=ds.label, status=str(ds.status),
                   fetched_at=ds.fetched_at, source_updated_at=ds.source_updated_at,
                   verified_by=ds.verified_by, verified_at=ds.verified_at)


def list_sources(db: Session, status: VerificationStatus | None = None, source_type: SourceType | None = None,
                 limit: int = 100, offset: int = 0) -> tuple[list[SourceOut], int]:
    conds = []
    if status is not None:
        conds.append(DataSource.status == VerificationStatus(status).value)
    if source_type is not None:
        conds.append(DataSource.source_type == SourceType(source_type).value)
    total = int(db.scalar(select(func.count()).select_from(DataSource).where(*conds)) or 0)
    rows = db.scalars(select(DataSource).where(*conds).order_by(DataSource.id.desc()).limit(limit)
                      .offset(offset)).all()
    return [SourceOut.of(r) for r in rows], total


def verify_source(db: Session, source_id: int, verified_by: str) -> SourceOut:
    """Nâng trạng thái nguồn lên `verified` (mục 11.2 bước 4). Commit."""
    ds = db.get(DataSource, source_id)
    if ds is None:
        raise _not_found("nguồn dữ liệu")
    ds.status = VerificationStatus.verified.value
    ds.verified_by = (verified_by or "admin").strip()[:200] or "admin"
    ds.verified_at = _now()
    db.commit()
    return SourceOut.of(ds)


def _new_admin_source(db: Session, label: str, approved: bool) -> DataSource:
    ds = DataSource(source_type=SourceType.dezon_editorial.value, url=None, label=label,
                    status=(VerificationStatus.verified if approved else VerificationStatus.reference).value,
                    source_updated_at=_now())
    if approved:
        ds.verified_by, ds.verified_at = "admin", _now()
    db.add(ds)
    db.flush()
    return ds


# ================================================================================================
# Đơn giá
# ================================================================================================
class RateIn(BaseModel):
    building_type: BuildingType
    province: str = Field(min_length=1, max_length=200)  # '*' = toàn quốc
    work_scope: WorkScope
    quality_tier: QualityTier = QualityTier.trung_binh
    price_min: int
    price_max: int
    effective_from: date
    effective_to: date | None = None
    source_id: int | None = None  # không truyền -> tạo nguồn "dezon_editorial" do quản trị viên nhập
    approved: bool = False  # chỉ dùng khi tự tạo nguồn: True -> nguồn verified


class RateUpdate(BaseModel):
    province: str | None = Field(default=None, min_length=1, max_length=200)
    quality_tier: QualityTier | None = None
    price_min: int | None = None
    price_max: int | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    clear_effective_to: bool = False
    source_id: int | None = None


class RateOut(BaseModel):
    id: int
    building_type: str
    province: str
    work_scope: str
    quality_tier: str
    price_min: int
    price_max: int
    effective_from: date
    effective_to: date | None = None
    source_id: int
    source_label: str | None = None
    source_status: str | None = None
    approved: bool = False  # = nguồn đã xác minh

    @classmethod
    def of(cls, r: ConstructionRate) -> RateOut:
        src = r.source
        return cls(id=r.id, building_type=str(r.building_type), province=r.province, work_scope=str(r.work_scope),
                   quality_tier=str(r.quality_tier), price_min=int(r.price_min), price_max=int(r.price_max),
                   effective_from=r.effective_from, effective_to=r.effective_to, source_id=r.source_id,
                   source_label=src.label if src else None, source_status=str(src.status) if src else None,
                   approved=bool(src and str(src.status) == VerificationStatus.verified.value))


def _validate_rate(price_min: int, price_max: int, eff_from: date, eff_to: date | None) -> None:
    if price_min < MIN_PRICE_PER_M2 or price_max < MIN_PRICE_PER_M2:
        raise AdminError(422, "Đơn giá phải tính theo VND/m² và tối thiểu " + f"{MIN_PRICE_PER_M2:,}".replace(",", ".") + " VND/m².")
    if price_min > MAX_PRICE_PER_M2 or price_max > MAX_PRICE_PER_M2:
        raise AdminError(422, "Đơn giá vượt giới hạn lưu trữ.")
    if price_min > price_max:
        raise AdminError(422, "Đơn giá thấp nhất không được lớn hơn đơn giá cao nhất.")
    if eff_to is not None and eff_to < eff_from:
        raise AdminError(422, "Ngày hết hiệu lực phải sau ngày hiệu lực.")


def _rate_key_taken(db: Session, r: dict[str, Any], exclude_id: int | None = None) -> bool:
    q = select(ConstructionRate.id).where(
        ConstructionRate.building_type == r["building_type"], ConstructionRate.province == r["province"],
        ConstructionRate.work_scope == r["work_scope"], ConstructionRate.quality_tier == r["quality_tier"],
        ConstructionRate.effective_from == r["effective_from"])
    if exclude_id is not None:
        q = q.where(ConstructionRate.id != exclude_id)
    return db.scalar(q.limit(1)) is not None


def _norm_province(p: str) -> str:
    p = " ".join(p.split())
    return "*" if p == "*" else p


def list_rates(db: Session, building_type: BuildingType | None = None, province: str | None = None,
               work_scope: WorkScope | None = None, quality_tier: QualityTier | None = None,
               limit: int = 200, offset: int = 0) -> tuple[list[RateOut], int]:
    conds = []
    if building_type is not None:
        conds.append(ConstructionRate.building_type == BuildingType(building_type).value)
    if work_scope is not None:
        conds.append(ConstructionRate.work_scope == WorkScope(work_scope).value)
    if quality_tier is not None:
        conds.append(ConstructionRate.quality_tier == QualityTier(quality_tier).value)
    if province:
        conds.append(ConstructionRate.province == _norm_province(province))
    total = int(db.scalar(select(func.count()).select_from(ConstructionRate).where(*conds)) or 0)
    rows = db.scalars(select(ConstructionRate).options(selectinload(ConstructionRate.source)).where(*conds)
                      .order_by(ConstructionRate.building_type, ConstructionRate.work_scope,
                                ConstructionRate.province, ConstructionRate.quality_tier,
                                ConstructionRate.effective_from.desc(), ConstructionRate.id)
                      .limit(limit).offset(offset)).all()
    return [RateOut.of(r) for r in rows], total


def get_rate(db: Session, rate_id: int) -> RateOut:
    r = db.get(ConstructionRate, rate_id)
    if r is None:
        raise _not_found("đơn giá")
    return RateOut.of(r)


def create_rate(db: Session, data: RateIn) -> RateOut:
    _validate_rate(data.price_min, data.price_max, data.effective_from, data.effective_to)
    values = {"building_type": data.building_type.value, "province": _norm_province(data.province),
              "work_scope": data.work_scope.value, "quality_tier": data.quality_tier.value,
              "effective_from": data.effective_from}
    if _rate_key_taken(db, values):
        raise AdminError(409, "Đã có đơn giá cùng loại công trình, tỉnh, phạm vi, mức chất lượng và ngày hiệu lực.")
    if data.source_id is not None:
        if db.get(DataSource, data.source_id) is None:
            raise _not_found("nguồn dữ liệu")
        source_id = data.source_id
    else:
        source_id = _new_admin_source(
            db, f"Đơn giá do Dezon biên soạn (nhập qua trang quản trị ngày {date.today():%d/%m/%Y})",
            data.approved).id
    row = ConstructionRate(**values, price_min=data.price_min, price_max=data.price_max,
                           effective_to=data.effective_to, source_id=source_id)
    db.add(row)
    db.flush()
    db.commit()
    db.refresh(row)
    return RateOut.of(row)


def update_rate(db: Session, rate_id: int, data: RateUpdate) -> RateOut:
    r = db.get(ConstructionRate, rate_id)
    if r is None:
        raise _not_found("đơn giá")
    price_min = data.price_min if data.price_min is not None else int(r.price_min)
    price_max = data.price_max if data.price_max is not None else int(r.price_max)
    eff_from = data.effective_from or r.effective_from
    eff_to = None if data.clear_effective_to else (data.effective_to or r.effective_to)
    _validate_rate(price_min, price_max, eff_from, eff_to)
    province = _norm_province(data.province) if data.province else r.province
    tier = data.quality_tier.value if data.quality_tier else str(r.quality_tier)
    key = {"building_type": str(r.building_type), "province": province, "work_scope": str(r.work_scope),
           "quality_tier": tier, "effective_from": eff_from}
    if _rate_key_taken(db, key, exclude_id=r.id):
        raise AdminError(409, "Đã có đơn giá khác cùng khóa (loại công trình, tỉnh, phạm vi, mức, ngày hiệu lực).")
    if data.source_id is not None and db.get(DataSource, data.source_id) is None:
        raise _not_found("nguồn dữ liệu")
    used = _rate_usage(db, r.id)
    changed_core = (price_min != int(r.price_min) or price_max != int(r.price_max) or province != r.province
                    or tier != str(r.quality_tier) or eff_from != r.effective_from)
    if used and changed_core:
        # Khái toán đã lưu giữ snapshot con số, nhưng sửa giá/khóa của bản ghi đã dùng làm mất dấu vết (rate_id).
        raise AdminError(409, f"Đơn giá đã được dùng trong {used} khái toán; chỉ được đặt ngày hết hiệu lực — "
                              "hãy tạo đơn giá mới thay vì sửa giá, tỉnh, mức hoặc ngày hiệu lực.")
    r.province, r.quality_tier = province, tier
    r.price_min, r.price_max = price_min, price_max
    r.effective_from, r.effective_to = eff_from, eff_to
    if data.source_id is not None:
        r.source_id = data.source_id
    db.flush()
    db.commit()
    db.refresh(r)
    return RateOut.of(r)


def _rate_usage(db: Session, rate_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(EstimateSession)
                         .where(EstimateSession.rate_id == rate_id)) or 0)


def delete_rate(db: Session, rate_id: int) -> None:
    r = db.get(ConstructionRate, rate_id)
    if r is None:
        raise _not_found("đơn giá")
    used = _rate_usage(db, rate_id)
    if used:
        raise AdminError(409, f"Đơn giá đã được dùng trong {used} khái toán nên không xóa được; hãy đặt ngày "
                              "hết hiệu lực để ngừng áp dụng.")
    db.delete(r)
    db.commit()


def approve_rate(db: Session, rate_id: int, verified_by: str) -> dict[str, Any]:
    """Duyệt đơn giá = xác minh nguồn của nó. Trả số đơn giá cùng nguồn được duyệt theo."""
    r = db.get(ConstructionRate, rate_id)
    if r is None:
        raise _not_found("đơn giá")
    affected = int(db.scalar(select(func.count()).select_from(ConstructionRate)
                             .where(ConstructionRate.source_id == r.source_id)) or 0)
    source = verify_source(db, r.source_id, verified_by)
    db.refresh(r)
    return {"rate": RateOut.of(r), "source": source, "affected_rates": affected}


# ================================================================================================
# Hệ số khái toán
# ================================================================================================
class RuleIn(BaseModel):
    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$")
    coefficient: Decimal
    group_name: str | None = None  # bỏ trống -> lấy từ phiên bản mới nhất cùng code
    label_vi: str | None = Field(default=None, max_length=300)
    base: str | None = None
    is_default: bool | None = None
    approved: bool = False


class RuleUpdate(BaseModel):
    label_vi: str | None = Field(default=None, min_length=1, max_length=300)
    is_default: bool | None = None
    approved: bool | None = None
    coefficient: Decimal | None = None
    group_name: str | None = None
    base: str | None = None


class RuleOut(BaseModel):
    id: int
    code: str
    group_name: str
    label_vi: str
    coefficient: float
    base: str
    is_default: bool
    version: int
    approved: bool
    source_id: int | None = None
    source_label: str | None = None

    @classmethod
    def of(cls, r: EstimateRule) -> RuleOut:
        return cls(id=r.id, code=r.code, group_name=r.group_name, label_vi=r.label_vi,
                   coefficient=float(r.coefficient), base=r.base, is_default=bool(r.is_default),
                   version=r.version, approved=bool(r.approved), source_id=r.source_id,
                   source_label=r.source.label if r.source else None)


def list_rules(db: Session, code: str | None = None, approved: bool | None = None,
               latest_only: bool = False) -> list[RuleOut]:
    conds = []
    if code:
        conds.append(EstimateRule.code == code)
    if approved is not None:
        conds.append(EstimateRule.approved.is_(approved))
    rows = db.scalars(select(EstimateRule).options(selectinload(EstimateRule.source)).where(*conds)
                      .order_by(EstimateRule.group_name, EstimateRule.code, EstimateRule.version.desc())).all()
    if latest_only:
        seen: set[str] = set()
        rows = [r for r in rows if not (r.code in seen or seen.add(r.code))]
    return [RuleOut.of(r) for r in rows]


def _validate_coefficient(k: Decimal) -> Decimal:
    if not k.is_finite() or k <= 0 or k > MAX_COEFFICIENT:
        raise AdminError(422, "Hệ số phải lớn hơn 0 và không quá 99,999.")
    return k.quantize(Decimal("0.001"))


def create_rule_version(db: Session, data: RuleIn) -> RuleOut:
    """Tạo phiên bản mới cho một mã hệ số (version = max + 1). Mã mới cần đủ group_name, label_vi, base."""
    k = _validate_coefficient(data.coefficient)
    latest = db.scalar(select(EstimateRule).where(EstimateRule.code == data.code)
                       .order_by(EstimateRule.version.desc()).limit(1))
    group = data.group_name or (latest.group_name if latest else None)
    label = (data.label_vi or "").strip() or (latest.label_vi if latest else None)
    base = data.base or (latest.base if latest else None)
    if not (group and label and base):
        raise AdminError(422, "Mã hệ số mới cần đủ nhóm (group_name), nhãn (label_vi) và cơ sở tính (base).")
    if group not in RULE_GROUPS:
        raise AdminError(422, f"Nhóm hệ số không hợp lệ: {group}. Chấp nhận: {', '.join(sorted(RULE_GROUPS))}.")
    if base not in RULE_BASES:
        raise AdminError(422, f"Cơ sở tính không hợp lệ: {base}. Chấp nhận: {', '.join(sorted(RULE_BASES))}.")
    is_default = data.is_default if data.is_default is not None else bool(latest.is_default if latest else False)
    src = _new_admin_source(db, f"Hệ số khái toán do Dezon biên soạn (nhập qua trang quản trị ngày "
                                f"{date.today():%d/%m/%Y})", data.approved)
    row = EstimateRule(code=data.code, group_name=group, label_vi=label, coefficient=k, base=base,
                       is_default=is_default, version=(latest.version + 1) if latest else 1,
                       approved=data.approved, source_id=src.id)
    db.add(row)
    db.flush()
    db.commit()
    db.refresh(row)
    return RuleOut.of(row)


def update_rule(db: Session, rule_id: int, data: RuleUpdate) -> RuleOut:
    r = db.get(EstimateRule, rule_id)
    if r is None:
        raise _not_found("hệ số")
    if (data.coefficient is not None and Decimal(r.coefficient) != _validate_coefficient(data.coefficient)) \
            or (data.group_name is not None and data.group_name != r.group_name) \
            or (data.base is not None and data.base != r.base):
        raise AdminError(422, "Không sửa hệ số/nhóm/cơ sở tính của phiên bản đã có — hãy tạo phiên bản mới "
                              "(POST /api/admin/rules).")
    if data.label_vi is not None:
        r.label_vi = data.label_vi.strip()
    if data.is_default is not None:
        r.is_default = data.is_default
    if data.approved is not None:
        r.approved = data.approved
    db.commit()
    db.refresh(r)
    return RuleOut.of(r)


def delete_rule(db: Session, rule_id: int) -> None:
    r = db.get(EstimateRule, rule_id)
    if r is None:
        raise _not_found("hệ số")
    if r.approved:
        raise AdminError(409, "Hệ số đã được duyệt — hãy bỏ duyệt trước khi xóa.")
    db.delete(r)
    db.commit()


# ================================================================================================
# Trọng số matching
# ================================================================================================
class WeightsIn(BaseModel):
    weights: dict[str, float]
    note: str | None = Field(default=None, max_length=500)
    activate: bool = True


class WeightsUpdate(BaseModel):
    is_active: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class WeightsOut(BaseModel):
    version: int
    weights: dict[str, float]
    is_active: bool
    note: str | None = None
    created_at: datetime | None = None
    recommendation_count: int = 0

    @classmethod
    def of(cls, w: MatchingWeights, count: int = 0) -> WeightsOut:
        return cls(version=w.version, weights={k: float(v) for k, v in (w.weights or {}).items()},
                   is_active=bool(w.is_active), note=w.note, created_at=w.created_at, recommendation_count=count)


def validate_weights(weights: dict[str, float]) -> dict[str, float]:
    keys = set(weights)
    missing = [k for k in CRITERIA_KEYS if k not in keys]
    extra = sorted(keys - set(CRITERIA_KEYS))
    if missing or extra:
        parts = []
        if missing:
            parts.append("thiếu " + ", ".join(f"{k} ({CRITERIA_LABEL_VI[k]})" for k in missing))
        if extra:
            parts.append("khóa không hợp lệ " + ", ".join(extra))
        raise AdminError(422, f"Trọng số phải gồm đúng 6 khóa {', '.join(CRITERIA_KEYS)}: {'; '.join(parts)}.")
    out: dict[str, float] = {}
    for k in CRITERIA_KEYS:
        v = float(weights[k])
        if not math.isfinite(v) or v < 0 or v > 1:
            raise AdminError(422, f"Trọng số {k} phải nằm trong khoảng 0..1.")
        out[k] = v
    total = sum(out.values())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise AdminError(422, f"Tổng trọng số phải bằng 1 (±{WEIGHT_SUM_TOLERANCE}); hiện là {total:.4f}.")
    return out


def _lock_weights(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _WEIGHTS_LOCK_KEY})


def _weights_counts(db: Session) -> dict[int, int]:
    rows = db.execute(select(RecommendationLog.weights_version, func.count())
                      .group_by(RecommendationLog.weights_version)).all()
    return {int(v): int(n) for v, n in rows if v is not None}


def list_weights(db: Session) -> list[WeightsOut]:
    counts = _weights_counts(db)
    rows = db.scalars(select(MatchingWeights).order_by(MatchingWeights.version.desc())).all()
    return [WeightsOut.of(w, counts.get(w.version, 0)) for w in rows]


def _activate(db: Session, version: int) -> None:
    db.execute(update(MatchingWeights).where(MatchingWeights.version != version,
                                             MatchingWeights.is_active.is_(True)).values(is_active=False))
    db.execute(update(MatchingWeights).where(MatchingWeights.version == version).values(is_active=True))


def create_weights(db: Session, data: WeightsIn) -> WeightsOut:
    """Tạo phiên bản trọng số mới (version = max + 1); mặc định kích hoạt ngay (các bản khác tắt)."""
    weights = validate_weights(data.weights)
    _lock_weights(db)
    version = int(db.scalar(select(func.coalesce(func.max(MatchingWeights.version), 0))) or 0) + 1
    db.add(MatchingWeights(version=version, weights=weights, is_active=False, note=data.note))
    db.flush()
    has_active = db.scalar(select(MatchingWeights.version).where(MatchingWeights.is_active.is_(True))
                           .limit(1)) is not None
    if data.activate or not has_active:
        _activate(db, version)
    db.commit()
    row = db.get(MatchingWeights, version)
    db.refresh(row)
    return WeightsOut.of(row)


def update_weights(db: Session, version: int, data: WeightsUpdate) -> WeightsOut:
    """Kích hoạt một phiên bản / sửa ghi chú. Nội dung trọng số bất biến (so sánh A/B theo weights_version)."""
    _lock_weights(db)
    w = db.get(MatchingWeights, version)
    if w is None:
        raise _not_found("phiên bản trọng số")
    if data.is_active is False and w.is_active:
        raise AdminError(422, "Luôn phải có một phiên bản trọng số đang hoạt động — hãy kích hoạt phiên bản khác.")
    if data.note is not None:
        w.note = data.note
    db.flush()
    if data.is_active:
        _activate(db, version)
    db.commit()
    db.refresh(w)
    return WeightsOut.of(w, _weights_counts(db).get(version, 0))


def delete_weights(db: Session, version: int) -> None:
    w = db.get(MatchingWeights, version)
    if w is None:
        raise _not_found("phiên bản trọng số")
    if w.is_active:
        raise AdminError(409, "Không xóa phiên bản trọng số đang hoạt động.")
    if _weights_counts(db).get(version):
        raise AdminError(409, "Phiên bản trọng số đã được dùng trong recommendation_logs — giữ lại để so sánh A/B.")
    db.delete(w)
    db.commit()


# ================================================================================================
# Vi phạm guardrail
# ================================================================================================
class ViolationOut(BaseModel):
    id: int
    session_id: str | None = None
    rule: str
    detail: dict | list | None = None
    created_at: datetime | None = None


def list_violations(db: Session, limit: int = 50, offset: int = 0, rule: str | None = None
                    ) -> tuple[list[ViolationOut], int]:
    conds = [GuardrailViolation.rule == rule] if rule else []
    total = int(db.scalar(select(func.count()).select_from(GuardrailViolation).where(*conds)) or 0)
    rows = db.scalars(select(GuardrailViolation).where(*conds)
                      .order_by(GuardrailViolation.created_at.desc(), GuardrailViolation.id.desc())
                      .limit(limit).offset(offset)).all()
    return [ViolationOut(id=v.id, session_id=str(v.session_id) if v.session_id else None, rule=v.rule,
                         detail=v.detail, created_at=v.created_at) for v in rows], total


# ================================================================================================
# Đồng bộ / nhập file
# ================================================================================================
def check_upload(filename: str | None, content: bytes) -> str:
    name = Path(filename or "").name
    if not name:
        raise AdminError(422, "Thiếu file tải lên.")
    if not name.lower().endswith(UPLOAD_EXTENSIONS):
        raise AdminError(422, "Chỉ nhận file .csv, .xlsx hoặc .xlsm.")
    if not content:
        raise AdminError(422, "File rỗng.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise AdminError(413, f"File quá lớn (tối đa {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
    return name


def import_upload(db: Session, content: bytes, filename: str, kind: str, *, dry_run: bool = False,
                  force: bool = False) -> dict[str, Any]:
    """Nhập đơn giá/hệ số từ nội dung file; commit khi không phải dry_run. Trả ImportReport.as_dict()."""
    if kind not in IMPORT_KINDS:
        raise AdminError(422, "Loại dữ liệu nhập phải là 'rates' (đơn giá) hoặc 'rules' (hệ số).")
    name = check_upload(filename, content)
    try:
        report = import_table(db, content, name, kind, dry_run=dry_run, force=force)  # type: ignore[arg-type]
    except Exception:
        db.rollback()
        raise
    if not dry_run and report.status != "failed":
        db.commit()
    return report.as_dict()


def import_path(db: Session, path: str, kind: str, *, dry_run: bool = False, force: bool = False
                ) -> dict[str, Any]:
    p = Path(path)
    if p.suffix.lower() not in UPLOAD_EXTENSIONS:
        raise AdminError(422, "Chỉ nhận file .csv, .xlsx hoặc .xlsm.")
    if not p.is_file():
        raise AdminError(422, f"Không tìm thấy file trên máy chủ: {p.name}")
    return import_upload(db, p.read_bytes(), p.name, kind, dry_run=dry_run, force=force)


def redis_connection():
    """Kết nối Redis với timeout ngắn để báo lỗi nhanh khi hàng đợi không sẵn sàng."""
    from redis import Redis

    return Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=5)


def enqueue_sync(source: str, **kwargs: Any) -> dict[str, Any]:
    """Xếp job đồng bộ (api|crawl|reindex|after_sync). Redis không sẵn sàng -> AdminError 503."""
    if source not in SYNC_JOBS:
        raise AdminError(422, f"Nguồn đồng bộ không hợp lệ: {source}. Chấp nhận: {', '.join(SYNC_SOURCES)}.")
    from redis.exceptions import RedisError

    name = SYNC_JOBS[source]
    try:
        conn = redis_connection()
        conn.ping()
        job = jobs.enqueue(name, connection=conn, **kwargs)
    except (RedisError, OSError) as exc:
        raise AdminError(503, "Hàng đợi job (Redis) chưa sẵn sàng nên chưa kích hoạt được đồng bộ — kiểm tra "
                              f"REDIS_URL và tiến trình worker (python -m app.worker). Lỗi: {exc.__class__.__name__}"
                         ) from None
    result: dict[str, Any] = {"status": "queued", "source": source, "job": name, "job_id": getattr(job, "id", None),
                              "params": kwargs}
    if source == "api" and not get_settings().dezon_api_base_url:
        result["warning"] = "Chưa cấu hình DEZON_API_BASE_URL — job api_sync sẽ bỏ qua (status skipped)."
    return result
