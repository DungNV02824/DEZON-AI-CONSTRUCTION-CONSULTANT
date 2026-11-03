"""Ghi dict trung gian (crawler / API / snapshot) vào Dezon Structured Data.

Quy tắc (đặc tả 2.10, 11.2):
- Mỗi trang/bản ghi nguồn -> đúng 1 bản ghi `data_sources` (khóa theo `url`), có `fetched_at`, `raw_hash`,
  `status` mặc định theo `default_status_for(source_type)`.
- Chỉ cập nhật thực thể khi nội dung đổi (hoặc thực thể chưa tồn tại). `data_sources.raw_hash` của trang lưu
  "<hash trang>:<hash bản ghi đã parse>" (`ingest_page`): trang không đổi nhưng bộ parse/làm sạch đổi (vd làm sạch đơn
  giá) vẫn ghi lại thực thể; trang không đổi và bản ghi parse không đổi -> bỏ qua.
- `contractors.field_sources = {field: data_source_id}` cho từng trường được ghi.
- Ưu tiên nguồn (`SOURCE_PRIORITY`): nguồn ưu tiên thấp hơn KHÔNG ghi đè trường đang có giá trị từ nguồn
  ưu tiên cao hơn; chỉ được điền trường đang trống.
- Giá trị `None` từ một nguồn chỉ xóa trường khi chính nguồn đó trước đây đã cung cấp trường này
  (nguồn rút lại dữ liệu); không xóa dữ liệu của nguồn khác.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Contractor,
    ContractorCapability,
    DataSource,
    Project,
    ProjectContractor,
    ProjectImage,
)
from app.evidence import SOURCE_PRIORITY, SourceType, default_status_for

log = logging.getLogger(__name__)

Action = Literal["created", "updated", "unchanged", "skipped"]

CONTRACTOR_FIELDS = [
    "name", "badge", "province", "founded_year", "years_experience", "staff_workers", "staff_office",
    "rough_price_min", "rough_price_max", "finish_price_min", "finish_price_max", "typical_project_value",
    "dezon_project_count", "has_warranty_info", "warranty_text", "description", "avatar_url", "profile_url",
]
PROJECT_FIELDS = [
    "name", "building_type", "province", "district", "floors", "land_area_m2", "floor_area_m2",
    "start_date", "end_date", "status", "description", "project_url",
]
BUILDING_TYPES = {"nha_pho", "biet_thu", "can_ho", "van_phong", "khach_san", "khac"}
WORK_SCOPES = {"xay_tho", "hoan_thien", "tron_goi", "noi_that"}


@dataclass
class UpsertOutcome:
    action: Action
    entity_type: str
    entity_id: int | None = None
    source_id: int | None = None
    slug: str | None = None
    changed_fields: list[str] = field(default_factory=list)
    skipped_fields: list[str] = field(default_factory=list)  # bị giữ lại do nguồn ưu tiên cao hơn
    detail: str | None = None


@dataclass
class IngestStats:
    pages: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    disallowed: int = 0
    changed_entities: list[tuple[str, int]] = field(default_factory=list)

    def add(self, outcome: UpsertOutcome) -> None:
        setattr(self, outcome.action, getattr(self, outcome.action) + 1)
        if outcome.action in ("created", "updated") and outcome.entity_id is not None:
            self.changed_entities.append((outcome.entity_type, outcome.entity_id))

    def as_dict(self) -> dict[str, Any]:
        return {"pages": self.pages, "created": self.created, "updated": self.updated,
                "unchanged": self.unchanged, "skipped": self.skipped, "errors": self.errors,
                "disallowed": self.disallowed}


# ------------------------------------------------------------------ tiện ích
def _now() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime) -> datetime:
    """datetime không có múi giờ -> coi là UTC (để so sánh được)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def priority_of(source_type: str | SourceType | None) -> int:
    """Số nhỏ = ưu tiên cao. Không rõ nguồn -> thấp nhất."""
    if source_type is None:
        return 99
    return SOURCE_PRIORITY.get(SourceType(source_type), 99)


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return Decimal(str(a)) == Decimal(str(b))
    if isinstance(a, datetime) and isinstance(b, datetime):
        return a == b
    if isinstance(a, date) and isinstance(b, str):
        return a.isoformat() == b
    return a == b


def _coerce(field_name: str, value: Any) -> Any:
    if value is None:
        return None
    if field_name in ("start_date", "end_date") and isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    if field_name == "building_type" and value not in BUILDING_TYPES:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _ds_cache(db: Session) -> dict[int, DataSource]:
    cache = db.info.setdefault("_ingest_ds_cache", {})
    return cache


def get_source(db: Session, source_id: int | None) -> DataSource | None:
    if source_id is None:
        return None
    cache = _ds_cache(db)
    ds = cache.get(source_id)
    if ds is None:
        ds = db.get(DataSource, source_id)
        if ds is not None:
            cache[source_id] = ds
    return ds


def _source_priority_by_id(db: Session, source_id: int | None) -> int:
    ds = get_source(db, source_id)
    return priority_of(ds.source_type) if ds is not None else 99


# ------------------------------------------------------------------ data_sources
HASH_SEP = ":"


def records_hash(records: list[dict[str, Any]]) -> str:
    """Hash của các bản ghi đã parse (JSON sắp xếp khóa) — đổi khi bộ parse/làm sạch cho kết quả khác."""
    data = json.dumps(records, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def find_data_source(db: Session, *, url: str | None, label: str, source_type: str | SourceType) -> DataSource | None:
    if url:
        return db.scalar(select(DataSource).where(DataSource.url == url))
    return db.scalar(select(DataSource).where(DataSource.label == label, DataSource.url.is_(None),
                                              DataSource.source_type == SourceType(source_type).value))


def reset_verification(ds: DataSource, approved: bool | None = None) -> None:
    """Nội dung nguồn đã đổi -> trạng thái về mặc định theo loại nguồn, bỏ xác minh cũ."""
    ds.status = default_status_for(ds.source_type, approved).value
    ds.verified_by = None
    ds.verified_at = None


def upsert_data_source(db: Session, *, url: str | None, label: str, source_type: str | SourceType,
                       raw_hash: str | None, source_updated_at: datetime | None = None,
                       fetched_at: datetime | None = None, approved: bool | None = None,
                       keep_verification: bool = False,
                       ) -> tuple[DataSource, Literal["created", "changed", "unchanged"]]:
    """Tạo/cập nhật bản ghi nguồn theo `url` (hoặc theo `label` khi không có url).

    - hash không đổi: chỉ cập nhật `fetched_at` (thời điểm kiểm tra lại gần nhất, không lùi về trước), trả
      "unchanged".
    - hash đổi: cập nhật hash, nhãn, ngày nguồn; trạng thái về mặc định theo `source_type`
      (nội dung đã thay đổi nên xác minh cũ không còn áp dụng), trả "changed". `keep_verification=True`: giữ trạng
      thái (người gọi tự quyết định sau khi biết dữ liệu có thật sự đổi — xem `ingest_page`).
    """
    st = SourceType(source_type)
    ds = find_data_source(db, url=url, label=label, source_type=st)
    fetched_at = fetched_at or _now()
    if ds is None:
        ds = DataSource(url=url, label=label, source_type=st.value,
                        status=default_status_for(st, approved).value, fetched_at=fetched_at,
                        source_updated_at=source_updated_at, raw_hash=raw_hash)
        db.add(ds)
        db.flush()
        _ds_cache(db)[ds.id] = ds
        return ds, "created"
    _ds_cache(db)[ds.id] = ds
    if raw_hash is not None and ds.raw_hash == raw_hash:
        if ds.fetched_at is None or aware(fetched_at) > aware(ds.fetched_at):
            ds.fetched_at = fetched_at
        return ds, "unchanged"
    # Một URL chỉ có 1 bản ghi nguồn: chỉ nâng loại nguồn lên mức ưu tiên cao hơn, không hạ.
    if priority_of(st) <= priority_of(ds.source_type):
        ds.source_type = st.value
    ds.raw_hash = raw_hash
    ds.label = label
    ds.fetched_at = fetched_at
    if source_updated_at is not None:
        ds.source_updated_at = source_updated_at
    if not keep_verification:
        reset_verification(ds, approved)
    db.flush()
    return ds, "changed"


# ------------------------------------------------------------------ contractors
def find_contractor(db: Session, *, external_id: str | None = None, slug: str | None = None) -> Contractor | None:
    if external_id:
        c = db.scalar(select(Contractor).where(Contractor.external_id == external_id))
        if c is not None:
            return c
    if slug:
        return db.scalar(select(Contractor).where(Contractor.slug == slug))
    return None


def _apply_contractor_fields(db: Session, obj: Contractor, data: dict[str, Any], source: DataSource,
                             outcome: UpsertOutcome, *, primary: bool = True) -> None:
    """`primary=False` (thẻ trong trang danh sách): chỉ điền trường đang trống hoặc trường do chính nguồn này
    cung cấp; không giành trường đang thuộc nguồn khác (vd trang hồ sơ cùng mức ưu tiên)."""
    fs: dict[str, Any] = dict(obj.field_sources or {})
    my_prio = priority_of(source.source_type)
    for f in CONTRACTOR_FIELDS:
        if f not in data:
            continue
        new = _coerce(f, data[f])
        cur = getattr(obj, f)
        owner_id = fs.get(f)
        owner_prio = _source_priority_by_id(db, owner_id) if owner_id is not None else 99
        if new is None:
            # chỉ nguồn đã cung cấp trường này mới được rút lại
            if owner_id == source.id and cur is not None and f not in ("name", "profile_url"):
                setattr(obj, f, None)
                fs.pop(f, None)
                outcome.changed_fields.append(f)
            continue
        if cur is not None and owner_id is not None and owner_id != source.id                 and (owner_prio < my_prio or (not primary and owner_prio <= my_prio)):
            if not _same(cur, new):
                outcome.skipped_fields.append(f)
            continue
        if cur is not None and owner_id is None and not primary and f not in ("name", "profile_url"):
            continue  # giá trị không rõ nguồn: thẻ danh sách không ghi đè
        if not _same(cur, new):
            setattr(obj, f, new)
            outcome.changed_fields.append(f)
        if fs.get(f) != source.id:
            fs[f] = source.id
            if f not in outcome.changed_fields:
                outcome.changed_fields.append(f"field_sources.{f}")
    obj.field_sources = fs  # gán dict mới để SQLAlchemy ghi nhận thay đổi JSONB


def sync_capabilities(db: Session, contractor: Contractor, building_types: list[str] | None,
                      provinces: list[str | None] | None, source: DataSource,
                      work_scopes: list[str] | None = None) -> bool:
    """Thay năng lực do nguồn có ưu tiên <= nguồn hiện tại cung cấp bằng tập mới.

    Năng lực từ nguồn ưu tiên cao hơn được giữ nguyên. Không có loại công trình -> không đụng tới.
    """
    bts = [b for b in (building_types or []) if b in BUILDING_TYPES]
    if not bts:
        return False
    provs = [p for p in (provinces or []) if p] or [contractor.province]
    scopes = [s for s in (work_scopes or []) if s in WORK_SCOPES] or [None]
    my_prio = priority_of(source.source_type)
    keep: list[ContractorCapability] = []
    drop: list[ContractorCapability] = []
    for cap in list(contractor.capabilities):
        if cap.source_id is not None and cap.source_id != source.id \
                and _source_priority_by_id(db, cap.source_id) < my_prio:
            keep.append(cap)
        else:
            drop.append(cap)
    wanted = {(b, s, p) for b in bts for s in scopes for p in provs}
    existing = {(c.building_type, c.work_scope, c.province) for c in drop}
    if existing == wanted and all(c.source_id == source.id for c in drop):
        return False
    kept_keys = {(c.building_type, c.work_scope, c.province) for c in keep}
    for cap in drop:
        contractor.capabilities.remove(cap)
    for b, s, p in sorted(wanted, key=lambda x: (x[0], x[1] or "", x[2] or "")):
        if (b, s, p) in kept_keys:
            continue
        contractor.capabilities.append(ContractorCapability(building_type=b, work_scope=s, province=p,
                                                            source_id=source.id))
    db.flush()
    return True


def upsert_contractor(db: Session, data: dict[str, Any], source: DataSource, *, primary: bool = True
                      ) -> UpsertOutcome:
    """Tạo/cập nhật nhà thầu từ dict trung gian.

    `primary=True` (trang hồ sơ / bản ghi API) -> nguồn này thành `contractors.source_id`.
    Thẻ trong trang danh sách dùng `primary=False`.
    """
    slug = data.get("slug")
    outcome = UpsertOutcome(action="unchanged", entity_type="contractor", source_id=source.id, slug=slug)
    obj = find_contractor(db, external_id=data.get("external_id"), slug=slug)
    if obj is None:
        if not (slug and data.get("name") and data.get("profile_url")):
            outcome.action, outcome.detail = "skipped", "Thiếu slug/tên/profile_url để tạo nhà thầu"
            return outcome
        obj = Contractor(slug=slug, external_id=data.get("external_id"), name=data["name"],
                         profile_url=data["profile_url"], field_sources={}, source_id=source.id)
        db.add(obj)
        db.flush()
        outcome.action = "created"
    elif data.get("external_id") and not obj.external_id:
        clash = find_contractor(db, external_id=data["external_id"])
        if clash is None:
            obj.external_id = data["external_id"]
    _apply_contractor_fields(db, obj, data, source, outcome, primary=primary)
    if primary and (obj.source_id is None or obj.source_id == source.id
                    or priority_of(source.source_type) <= _source_priority_by_id(db, obj.source_id)):
        if obj.source_id != source.id:
            obj.source_id = source.id
            outcome.changed_fields.append("source_id")
    # Thẻ danh sách (primary=False) chỉ tạo năng lực khi nhà thầu chưa có, để không giành qua lại với hồ sơ.
    if ("building_types" in data or "service_provinces" in data) and (primary or not obj.capabilities):
        provinces = data.get("service_provinces") or ([data.get("province")] if data.get("province") else None)
        if sync_capabilities(db, obj, data.get("building_types"), provinces, source, data.get("work_scopes")):
            outcome.changed_fields.append("capabilities")
    if outcome.action != "created" and any(not f.startswith("field_sources.") for f in outcome.changed_fields):
        outcome.action = "updated"
    if outcome.action in ("created", "updated"):
        obj.updated_at = _now()
    db.flush()
    outcome.entity_id = obj.id
    return outcome


# ------------------------------------------------------------------ projects
def find_project(db: Session, *, external_id: str | None = None, slug: str | None = None,
                 project_url: str | None = None) -> Project | None:
    if external_id:
        p = db.scalar(select(Project).where(Project.external_id == external_id))
        if p is not None:
            return p
    if slug:
        p = db.scalar(select(Project).where(Project.slug == slug))
        if p is not None:
            return p
    if project_url:
        return db.scalar(select(Project).where(Project.project_url == project_url))
    return None


def sync_project_images(db: Session, project: Project, images: list[dict[str, Any]]) -> bool:
    """Thay toàn bộ ảnh của dự án khi danh sách URL thay đổi."""
    wanted = [(img["url"], img.get("caption"), int(img.get("sort_order", i)))
              for i, img in enumerate(images) if img.get("url")]
    current = [(i.url, i.caption, i.sort_order) for i in project.images]
    if current == wanted:
        return False
    for img in list(project.images):
        project.images.remove(img)
    db.flush()
    for url, caption, order in wanted:
        project.images.append(ProjectImage(url=url, caption=caption, sort_order=order))
    db.flush()
    return True


def link_project_contractor(db: Session, project: Project, contractor_ref: dict[str, Any],
                            source: DataSource, *, create_missing: bool = False) -> bool:
    """Tạo liên kết dự án – nhà thầu. Nhà thầu chưa có trong DB: tạo bản ghi tối thiểu nếu
    `create_missing`, ngược lại bỏ qua (sẽ liên kết ở lần đồng bộ sau khi hồ sơ đã được nạp)."""
    slug = contractor_ref.get("slug")
    role = contractor_ref.get("role") or "thi_cong"
    contractor = find_contractor(db, external_id=contractor_ref.get("external_id") or slug, slug=slug)
    if contractor is None:
        if not (create_missing and slug and contractor_ref.get("name") and contractor_ref.get("profile_url")):
            return False
        upsert_contractor(db, {"slug": slug, "external_id": slug, "name": contractor_ref["name"],
                               "profile_url": contractor_ref["profile_url"]}, source, primary=False)
        contractor = find_contractor(db, slug=slug)
    exists = db.get(ProjectContractor, (project.id, contractor.id, role))
    if exists:
        return False
    db.add(ProjectContractor(project_id=project.id, contractor_id=contractor.id, role=role))
    db.flush()
    return True


def upsert_project(db: Session, data: dict[str, Any], source: DataSource, *,
                   create_missing_contractors: bool = False) -> UpsertOutcome:
    """Tạo/cập nhật dự án + ảnh + liên kết nhà thầu.

    `projects` không có field_sources: ưu tiên nguồn xét theo `projects.source_id`. Nguồn ưu tiên thấp hơn
    nguồn hiện tại chỉ được điền trường trống, không thay ảnh.
    """
    slug = data.get("slug")
    outcome = UpsertOutcome(action="unchanged", entity_type="project", source_id=source.id, slug=slug)
    obj = find_project(db, external_id=data.get("external_id"), slug=slug, project_url=data.get("project_url"))
    if obj is None:
        if not (slug and data.get("name") and data.get("project_url")):
            outcome.action, outcome.detail = "skipped", "Thiếu slug/tên/project_url để tạo dự án"
            return outcome
        obj = Project(slug=slug, external_id=data.get("external_id"), name=data["name"],
                      project_url=data["project_url"], source_id=source.id)
        db.add(obj)
        db.flush()
        outcome.action = "created"
    protected = (obj.source_id is not None and obj.source_id != source.id
                 and _source_priority_by_id(db, obj.source_id) < priority_of(source.source_type))
    for f in PROJECT_FIELDS:
        if f not in data:
            continue
        new = _coerce(f, data[f])
        cur = getattr(obj, f)
        if new is None:
            continue  # projects không lưu nguồn từng trường -> không xóa dữ liệu đang có
        if protected and cur is not None:
            if not _same(cur, new):
                outcome.skipped_fields.append(f)
            continue
        if not _same(cur, new):
            setattr(obj, f, new)
            outcome.changed_fields.append(f)
    if not protected and obj.source_id != source.id:
        obj.source_id = source.id
        outcome.changed_fields.append("source_id")
    if data.get("images") and (not protected or not obj.images):
        if sync_project_images(db, obj, data["images"]):
            outcome.changed_fields.append("images")
    for ref in data.get("contractors") or []:
        if link_project_contractor(db, obj, ref, source, create_missing=create_missing_contractors):
            outcome.changed_fields.append("contractors")
    if outcome.action != "created" and outcome.changed_fields:
        outcome.action = "updated"
    if outcome.action in ("created", "updated"):
        obj.updated_at = _now()
    db.flush()
    outcome.entity_id = obj.id
    return outcome


# ------------------------------------------------------------------ một trang -> nhiều bản ghi
def default_label(record: dict[str, Any], url: str | None) -> str:
    name = record.get("name") or record.get("slug") or (url or "")
    if record.get("kind") == "project":
        return f"Dự án {name} trên Dezon"
    return f"Hồ sơ nhà thầu {name}"


def ingest_page(db: Session, records: list[dict[str, Any]], *, url: str | None, label: str | None = None,
                source_type: str | SourceType = SourceType.crawl_public, raw_hash: str | None,
                source_updated_at: datetime | None = None, fetched_at: datetime | None = None,
                primary: bool = True, create_missing_contractors: bool = False) -> list[UpsertOutcome]:
    """Ghi các bản ghi lấy từ cùng một trang/nguồn.

    Khóa so sánh = `raw_hash` (hash trang) + hash các bản ghi đã parse. Trùng lần trước -> mọi bản ghi đã tồn tại
    được đánh dấu "unchanged" mà không ghi DB. Trang không đổi nhưng bản ghi parse khác (bộ parse/làm sạch mới, hoặc
    khóa cũ chỉ có hash trang) -> vẫn ghi lại; xác minh của nguồn chỉ bị bỏ khi thực thể thật sự đổi.
    """
    if not records:
        return []
    label = label or default_label(records[0], url)
    if source_updated_at is None:
        source_updated_at = records[0].get("source_updated_at")
    content_key = f"{raw_hash}{HASH_SEP}{records_hash(records)}" if raw_hash is not None else None
    prev = find_data_source(db, url=url, label=label, source_type=source_type)
    prev_key = prev.raw_hash if prev is not None else None
    reparsed = (content_key is not None and prev_key is not None and prev_key != content_key
                and prev_key.split(HASH_SEP, 1)[0] == raw_hash)
    ds, state = upsert_data_source(db, url=url, label=label, source_type=source_type, raw_hash=content_key,
                                   source_updated_at=source_updated_at, fetched_at=fetched_at,
                                   keep_verification=reparsed)
    outcomes: list[UpsertOutcome] = []
    for rec in records:
        kind = rec.get("kind", "contractor")
        if state == "unchanged":
            existing = (find_contractor(db, external_id=rec.get("external_id"), slug=rec.get("slug"))
                        if kind == "contractor" else
                        find_project(db, external_id=rec.get("external_id"), slug=rec.get("slug"),
                                     project_url=rec.get("project_url")))
            if existing is not None:
                outcomes.append(UpsertOutcome(action="unchanged", entity_type=kind, entity_id=existing.id,
                                              source_id=ds.id, slug=rec.get("slug")))
                continue
        try:
            with db.begin_nested():
                if kind == "project":
                    out = upsert_project(db, rec, ds, create_missing_contractors=create_missing_contractors)
                else:
                    out = upsert_contractor(db, rec, ds, primary=primary)
        except Exception as exc:  # noqa: BLE001 — một bản ghi lỗi không làm hỏng cả trang
            log.exception("Lỗi ghi bản ghi %s từ %s", rec.get("slug"), url)
            out = UpsertOutcome(action="skipped", entity_type=kind, source_id=ds.id, slug=rec.get("slug"),
                                detail=f"{exc.__class__.__name__}: {exc}")
        outcomes.append(out)
    if reparsed and any(o.action in ("created", "updated") for o in outcomes):
        reset_verification(ds)  # dữ liệu hiển thị đổi (dù trang không đổi) -> xác minh cũ không còn áp dụng
        db.flush()
    return outcomes
