"""Service nhà thầu: nạp dữ liệu từ DB, gọi Matching Engine thuần, dựng các model trả về (mục 7, 8).

Mọi số liệu trả ra đều là EvidenceValue có nguồn (contractors.field_sources -> data_sources,
fallback contractors.source_id). Trường trống -> EvidenceValue.unavailable() ("Chưa có dữ liệu").
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db.models import (
    ChatSession,
    Contractor,
    ContractorCapability,
    DataSource,
    EstimateSession,
    MatchingWeights,
    Project,
    ProjectContractor,
    ProvinceNeighbor,
    RecommendationLog,
)
from app.engines.matching.criteria import (
    CapabilityFact,
    ContractorFacts,
    FieldSource,
    ProjectFact,
    fold_vi,
    norm_place,
    profile_completeness,
)
from app.engines.matching.reasons import (
    build_cautions,
    build_evidence,
    build_reasons,
    building_types_of,
)
from app.engines.matching.scorer import (
    PRICE_KIND_VI,
    ScoredContractor,
    operates_in,
    rank_contractors,
    score_contractor,
)
from app.evidence import EvidenceValue, SourceRef, VerificationStatus, make_evidence, source_ref_from
from app.formatting import format_m2, format_price_per_m2_range, format_vnd_range, vi_number
from app.schemas.common import BUILDING_TYPE_VI, BuildingType
from app.schemas.matching import (
    ComparedContractor,
    ComparisonMatrix,
    ComparisonRow,
    ContractorCard,
    ContractorOverview,
    ContractorProfile,
    ImageRef,
    MatchCriteria,
    MatchedContractor,
    MatchResult,
    OverviewContractor,
    ProjectSummary,
)

MAX_COVER_IMAGES = 3
STATUS_RANK = {VerificationStatus.unavailable.value: 0, VerificationStatus.reference.value: 1,
               VerificationStatus.on_dezon.value: 2, VerificationStatus.verified.value: 3}


# ================================================================================================
# Nạp dữ liệu
# ================================================================================================
def _load_options():
    return (
        selectinload(Contractor.capabilities),
        selectinload(Contractor.source),
        selectinload(Contractor.project_links).selectinload(ProjectContractor.project).options(
            selectinload(Project.images),
            selectinload(Project.source),
            selectinload(Project.contractor_links).selectinload(ProjectContractor.contractor),
        ),
    )


def _load_contractors(db: Session, ids: Iterable[int], active_only: bool = False) -> list[Contractor]:
    ids = list(ids)
    if not ids:
        return []
    stmt = select(Contractor).where(Contractor.id.in_(ids)).options(*_load_options())
    if active_only:
        stmt = stmt.where(Contractor.is_active.is_(True))
    rows = {c.id: c for c in db.scalars(stmt).unique()}
    return [rows[i] for i in ids if i in rows]  # giữ thứ tự ids


def _source_ids(contractors: Iterable[Contractor]) -> set[int]:
    ids: set[int] = set()
    for c in contractors:
        if c.source_id:
            ids.add(c.source_id)
        for v in (c.field_sources or {}).values():
            try:
                ids.add(int(v))
            except (TypeError, ValueError):
                continue
        ids.update(cap.source_id for cap in c.capabilities if cap.source_id)
    return ids


def _load_sources(db: Session, contractors: Sequence[Contractor]) -> dict[int, DataSource]:
    ids = _source_ids(contractors)
    if not ids:
        return {}
    return {ds.id: ds for ds in db.scalars(select(DataSource).where(DataSource.id.in_(ids)))}


def _fs(ds: DataSource | None) -> FieldSource | None:
    if ds is None:
        return None
    return FieldSource(ref=source_ref_from(ds), status=str(ds.status))


def _f(x: Decimal | float | int | None) -> float | None:
    return float(x) if x is not None else None


def _unique_projects(c: Contractor) -> list[Project]:
    seen: dict[int, Project] = {}
    for link in c.project_links:
        if link.project is not None and link.project.id not in seen:
            seen[link.project.id] = link.project
    return list(seen.values())


def _facts(c: Contractor, ds_map: dict[int, DataSource] | None = None) -> ContractorFacts:
    ds_map = ds_map or {}
    field_sources: dict[str, FieldSource] = {}
    for k, v in (c.field_sources or {}).items():
        try:
            src = _fs(ds_map.get(int(v)))
        except (TypeError, ValueError):
            src = None
        if src:
            field_sources[k] = src
    if "capabilities" not in field_sources:
        cap_src = next((ds_map[cap.source_id] for cap in c.capabilities if cap.source_id in ds_map), None)
        if cap_src is not None:
            field_sources["capabilities"] = _fs(cap_src)
    default = _fs(ds_map.get(c.source_id)) if c.source_id else None
    if default is None and c.source is not None:
        default = _fs(c.source)
    return ContractorFacts(
        id=c.id, name=c.name, slug=c.slug, profile_url=c.profile_url, is_active=bool(c.is_active),
        province=c.province, founded_year=c.founded_year, years_experience=c.years_experience,
        staff_workers=c.staff_workers, staff_office=c.staff_office,
        rough_price_min=_f(c.rough_price_min), rough_price_max=_f(c.rough_price_max),
        finish_price_min=_f(c.finish_price_min), finish_price_max=_f(c.finish_price_max),
        typical_project_value=_f(c.typical_project_value), dezon_project_count=c.dezon_project_count,
        has_warranty_info=c.has_warranty_info, warranty_text=c.warranty_text, description=c.description,
        avatar_url=c.avatar_url,
        capabilities=[CapabilityFact(cap.building_type, cap.work_scope, cap.province, cap.district)
                      for cap in c.capabilities],
        projects=[ProjectFact(p.building_type, p.province, p.district, p.floors) for p in _unique_projects(c)],
        field_sources=field_sources, default_source=default,
    )


def _ids_in_provinces(db: Session, provinces: Iterable[str]) -> set[int]:
    """Id nhà thầu có tỉnh chính / capability / dự án thuộc tập tỉnh (so khớp đã chuẩn hóa)."""
    wanted = {norm_place(p) for p in provinces if p}
    if not wanted:
        return set()
    ids: set[int] = set()
    queries = (
        select(Contractor.id, Contractor.province),
        select(ContractorCapability.contractor_id, ContractorCapability.province).distinct(),
        select(ProjectContractor.contractor_id, Project.province)
        .join(Project, Project.id == ProjectContractor.project_id).distinct(),
    )
    for q in queries:
        for cid, prov in db.execute(q):
            if norm_place(prov) in wanted:
                ids.add(cid)
    return ids


def load_neighbors(db: Session, province: str) -> list[str]:
    p = norm_place(province)
    rows = db.execute(select(ProvinceNeighbor.province, ProvinceNeighbor.neighbor)).all()
    return sorted({n for prov, n in rows if norm_place(prov) == p})


def load_active_weights(db: Session) -> MatchingWeights:
    row = db.scalar(select(MatchingWeights).where(MatchingWeights.is_active.is_(True))
                    .order_by(MatchingWeights.version.desc()).limit(1))
    if row is None:
        raise LookupError("Chưa có bộ trọng số matching đang hoạt động (matching_weights.is_active)")
    return row


# ================================================================================================
# Thẻ nhà thầu / dự án
# ================================================================================================
def _image_refs(projects: Iterable[Project]) -> list[ImageRef]:
    return [ImageRef(url=img.url, caption=img.caption, project_slug=p.slug)
            for p in projects for img in sorted(p.images, key=lambda i: (i.sort_order or 0, i.id))]


def _cover_images(c: Contractor) -> list[ImageRef]:
    """Tối đa 3 ảnh: ưu tiên ảnh đầu của mỗi dự án, sau đó bổ sung ảnh còn lại."""
    projects = sorted(_unique_projects(c), key=lambda p: p.id)
    firsts, rest = [], []
    for p in projects:
        imgs = [ImageRef(url=i.url, caption=i.caption, project_slug=p.slug)
                for i in sorted(p.images, key=lambda i: (i.sort_order or 0, i.id))]
        firsts += imgs[:1]
        rest += imgs[1:]
    return (firsts + rest)[:MAX_COVER_IMAGES]


def _card(c: Contractor, facts: ContractorFacts) -> ContractorCard:
    return ContractorCard(
        id=c.id, slug=c.slug, name=c.name, badge=c.badge, avatar_url=c.avatar_url, profile_url=c.profile_url,
        profile_source=source_ref_from(c.source), cover_images=_cover_images(c), evidence=build_evidence(facts),
    )


def build_contractor_card(db: Session, contractor: Contractor) -> ContractorCard:
    """Thẻ nhà thầu với evidence chuẩn (province, building_types, years_experience, founded_year, staff,
    rough_price, finish_price, typical_project_value, dezon_project_count, warranty, profile_completeness)."""
    ds_map = _load_sources(db, [contractor])
    return _card(contractor, _facts(contractor, ds_map))


def _project_summaries(c: Contractor, building_type: str | None = None) -> list[ProjectSummary]:
    roles: dict[int, set[str]] = {}
    projects: dict[int, Project] = {}
    for link in c.project_links:
        p = link.project
        if p is None or (building_type and p.building_type != building_type):
            continue
        projects[p.id] = p
        if link.role:
            roles.setdefault(p.id, set()).add(link.role)
    ordered = sorted(projects.values(),
                     key=lambda p: (p.end_date is None, -(p.end_date.toordinal() if p.end_date else 0), p.id))
    out = []
    for p in ordered:
        names = sorted({lk.contractor.name for lk in p.contractor_links if lk.contractor is not None})
        out.append(ProjectSummary(
            id=p.id, slug=p.slug, name=p.name, building_type=p.building_type, province=p.province,
            district=p.district, floors=p.floors, status=p.status, start_date=p.start_date, end_date=p.end_date,
            project_url=p.project_url, role=", ".join(sorted(roles.get(p.id, set()))) or None,
            contractor_names=names, images=_image_refs([p]), source=source_ref_from(p.source),
        ))
    return out


def _get_contractor(db: Session, id_or_slug: int | str) -> Contractor | None:
    c = None
    if isinstance(id_or_slug, int) or (isinstance(id_or_slug, str) and id_or_slug.isdigit()):
        found = _load_contractors(db, [int(id_or_slug)])
        c = found[0] if found else None
    if c is None and isinstance(id_or_slug, str):
        c = db.scalar(select(Contractor).where(Contractor.slug == id_or_slug).options(*_load_options()))
    return c


def get_contractor_profile(db: Session, id_or_slug: int | str) -> ContractorProfile | None:
    """Hồ sơ mở rộng (mục 8.3): thẻ + bảng thông tin + công trình thực tế (ảnh, link) + toàn bộ ảnh."""
    c = _get_contractor(db, id_or_slug)
    if c is None:
        return None
    facts = _facts(c, _load_sources(db, [c]))
    card = _card(c, facts)
    ev = card.evidence

    def single(value, display, *fields):
        src = facts.source_for(*fields)
        if value is None:
            return EvidenceValue.unavailable()
        return make_evidence(value, display, source=src.ref if src else None,
                             status=src.status if src else VerificationStatus.reference)

    info = {
        "founded_year": ev["founded_year"],
        "staff_workers": single(c.staff_workers, f"{vi_number(c.staff_workers or 0, 0)} thợ", "staff_workers"),
        "staff_office": single(c.staff_office, f"{vi_number(c.staff_office or 0, 0)} văn phòng", "staff_office"),
        "years_experience": ev["years_experience"],
        "rough_price": ev["rough_price"],
        "finish_price": ev["finish_price"],
        "typical_project_value": ev["typical_project_value"],
        "dezon_project_count": ev["dezon_project_count"],
        "warranty": ev["warranty"],
    }
    desc = (c.description or "").strip() or None
    projects = _project_summaries(c)
    images = [img for p in projects for img in p.images]
    return ContractorProfile(card=card, description=single(desc, desc or "", "description"), info=info,
                             projects=projects, images=images, total_images=len(images))


def get_contractor_projects(db: Session, contractor_id: int, building_type: str | None = None,
                            limit: int = 10) -> list[ProjectSummary]:
    found = _load_contractors(db, [contractor_id])
    if not found:
        return []
    bt = str(building_type) if building_type else None
    return _project_summaries(found[0], bt)[:max(limit, 0)]


# ================================================================================================
# Matching
# ================================================================================================
def _criteria_from_estimate(db: Session, criteria: MatchCriteria) -> tuple[MatchCriteria, list[str], bool]:
    """Bổ sung converted_area_m2 / floors / work_scope / district từ estimate_sessions (nếu có)."""
    if not criteria.estimate_session_id:
        return criteria, [], False
    es = db.get(EstimateSession, criteria.estimate_session_id)
    if es is None:
        return criteria, ["Không tìm thấy phiên khái toán đã chọn — chưa so được chi phí theo ngân sách."], False
    inp = es.input or {}
    update: dict = {}
    if criteria.converted_area_m2 is None and es.converted_area_m2 is not None:
        update["converted_area_m2"] = float(es.converted_area_m2)
    if criteria.floors is None and inp.get("floors"):
        update["floors"] = int(inp["floors"])
    if criteria.work_scope is None and inp.get("work_scope"):
        update["work_scope"] = inp["work_scope"]
    if criteria.district is None and inp.get("district"):
        update["district"] = inp["district"]
    return MatchCriteria.model_validate({**criteria.model_dump(), **update}), [], True


def _estimated_cost_evidence(s: ScoredContractor, area: float | None) -> EvidenceValue:
    if s.estimated_cost is None or not s.price_kind or not area:
        return EvidenceValue.unavailable()
    f = s.facts
    src = f.source_for(f"{s.price_kind}_price_min", f"{s.price_kind}_price_max")
    ref = SourceRef(
        label=(f"Ước tính = diện tích quy đổi {format_m2(area)} × đơn giá {PRICE_KIND_VI[s.price_kind]} "
               f"trên hồ sơ {f.name}"),
        url=(src.ref.url if src and src.ref else None) or f.profile_url or None,
        updated_at=src.ref.updated_at if src and src.ref else None,
    )
    return make_evidence(list(s.estimated_cost), format_vnd_range(*s.estimated_cost), source=ref,
                         status=src.status if src else VerificationStatus.reference)


def match_contractors(db: Session, criteria: MatchCriteria,
                      chat_session_id: uuid.UUID | str | None = None) -> MatchResult:
    """Chạy Matching Engine, ghi recommendation_logs (criteria, results, weights_version) và commit."""
    weights = load_active_weights(db)
    crit, notes, es_ok = _criteria_from_estimate(db, criteria)
    neighbors = load_neighbors(db, crit.province)
    ids = _ids_in_provinces(db, [crit.province, *neighbors])
    contractors = _load_contractors(db, sorted(ids), active_only=True)
    ds_map = _load_sources(db, contractors)
    by_id = {c.id: c for c in contractors}
    facts = [_facts(c, ds_map) for c in contractors]
    ranking = rank_contractors(facts, crit, weights.weights, neighbors, crit.converted_area_m2, crit.limit)

    results: list[MatchedContractor] = []
    for rank, s in enumerate(ranking.ranked, start=1):
        card = _card(by_id[s.facts.id], s.facts)
        results.append(MatchedContractor(
            rank=rank, score=s.score, fit_label=s.fit_label, breakdown=s.breakdown,
            reasons=build_reasons(s, card.evidence, crit), cautions=build_cautions(s, card.evidence, crit),
            estimated_cost=_estimated_cost_evidence(s, crit.converted_area_m2),
            relaxed_location=s.relaxed_location, contractor=card,
        ))
    notes = notes + ranking.notes
    if not crit.budget_vnd:
        notes.append("Anh/chị chưa cung cấp ngân sách nên tiêu chí ngân sách được tính trung tính.")

    chat_id = None
    if chat_session_id:
        try:
            cid = uuid.UUID(str(chat_session_id))
            chat_id = cid if db.get(ChatSession, cid) is not None else None
        except ValueError:
            chat_id = None
    log = RecommendationLog(
        chat_session_id=chat_id,
        estimate_session_id=crit.estimate_session_id if es_ok else None,
        weights_version=weights.version,
        criteria=crit.model_dump(mode="json"),
        results=[{
            "contractor_id": r.contractor.id, "rank": r.rank, "score": r.score, "fit_label": r.fit_label,
            "relaxed_location": r.relaxed_location,
            "breakdown": [b.model_dump(mode="json") for b in r.breakdown],
            "reasons": [x.model_dump(mode="json") for x in r.reasons],
            "cautions": [x.model_dump(mode="json") for x in r.cautions],
            "estimated_cost": r.estimated_cost.value,
        } for r in results],
    )
    db.add(log)
    db.flush()
    log_id = log.id
    db.commit()
    return MatchResult(recommendation_log_id=log_id, weights_version=weights.version, criteria=crit,
                       total_candidates=ranking.total_candidates, relaxed_to_neighbors=ranking.relaxed_to_neighbors,
                       results=results, notes=notes)


def record_click(db: Session, log_id: int, contractor_id: int) -> bool:
    """Ghi nhận khách bấm vào nhà thầu được đề xuất (không trùng). False nếu log/nhà thầu không hợp lệ."""
    log = db.get(RecommendationLog, log_id)
    if log is None:
        return False
    if contractor_id not in {r.get("contractor_id") for r in (log.results or [])}:
        return False
    clicked = list(log.clicked_contractor_ids or [])
    if contractor_id not in clicked:
        log.clicked_contractor_ids = [*clicked, contractor_id]
        db.commit()
    return True


# ================================================================================================
# Tìm kiếm đơn giản
# ================================================================================================
def _like_escape(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _vi_translate_maps() -> tuple[str, str]:
    """Cặp chuỗi cho SQL translate(): ký tự tiếng Việt có dấu (thường + hoa) và A–Z -> chữ thường không dấu.
    Dùng thay extension unaccent (chưa có trong migration) để "kien phong" tìm ra "Kiến Phong"."""
    src: list[str] = []
    dst: list[str] = []
    for base, accented in {
        "a": "àáạảãâầấậẩẫăằắặẳẵ", "e": "èéẹẻẽêềếệểễ", "i": "ìíịỉĩ", "o": "òóọỏõôồốộổỗơờớợởỡ",
        "u": "ùúụủũưừứựửữ", "y": "ỳýỵỷỹ", "d": "đ",
    }.items():
        for ch in accented:
            src += [ch, ch.upper()]
            dst += [base, base]
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        src.append(ch)
        dst.append(ch.lower())
    return "".join(src), "".join(dst)


_VI_SRC, _VI_DST = _vi_translate_maps()


def _fold_sql(col):
    return func.translate(col, _VI_SRC, _VI_DST)


def search_contractors_simple(db: Session, province: str | None = None, building_type: str | None = None,
                              q: str | None = None, limit: int = 20) -> tuple[list[ContractorCard], int]:
    """Tìm nhà thầu đang hoạt động theo tỉnh / loại công trình / tên (ILIKE hoặc pg_trgm)."""
    conds = [Contractor.is_active.is_(True)]
    if province:
        conds.append(Contractor.id.in_(sorted(_ids_in_provinces(db, [province]))))
    if building_type:
        bt = str(building_type)
        if bt not in BuildingType.__members__:
            # Cột building_type là ENUM Postgres: giá trị lạ sẽ làm hỏng transaction -> báo lỗi rõ ràng.
            raise ValueError(f"Loại công trình không hợp lệ: {bt}")
        conds.append(or_(
            exists().where(ContractorCapability.contractor_id == Contractor.id,
                           ContractorCapability.building_type == bt),
            exists().where(ProjectContractor.contractor_id == Contractor.id,
                           ProjectContractor.project_id == Project.id, Project.building_type == bt),
        ))
    if q and q.strip():
        term = unicodedata.normalize("NFC", re.sub(r"\s+", " ", q.strip()))
        folded = fold_vi(term)  # khách gõ không dấu / có dấu đều khớp
        slug_term = re.sub(r"[^0-9a-z]+", "-", folded).strip("-") or term
        conds.append(or_(Contractor.name.ilike(f"%{_like_escape(term)}%", escape="\\"),
                         _fold_sql(Contractor.name).like(f"%{_like_escape(folded)}%", escape="\\"),
                         Contractor.slug.ilike(f"%{_like_escape(slug_term)}%", escape="\\"),
                         func.similarity(Contractor.name, term) > 0.3))
    total = db.scalar(select(func.count()).select_from(Contractor).where(*conds)) or 0
    ids = db.scalars(select(Contractor.id).where(*conds).order_by(
        Contractor.dezon_project_count.desc().nulls_last(),
        Contractor.profile_completeness.desc().nulls_last(), Contractor.name, Contractor.id,
    ).limit(max(limit, 0))).all()
    contractors = _load_contractors(db, ids)
    ds_map = _load_sources(db, contractors)
    return [_card(c, _facts(c, ds_map)) for c in contractors], int(total)


# ================================================================================================
# So sánh
# ================================================================================================
def _weakest_status(statuses: Iterable[str]) -> str:
    sts = [s for s in statuses if s]
    if not sts:
        return VerificationStatus.reference.value
    return min(sts, key=lambda s: STATUS_RANK.get(s, 0))


def _latest(dates: Iterable[date | None]) -> date | None:
    ds = [d for d in dates if d]
    return max(ds) if ds else None


def _projects_cell(c: Contractor) -> EvidenceValue:
    projects = _unique_projects(c)
    if not projects:
        return EvidenceValue.unavailable()
    refs = [source_ref_from(p.source) for p in projects]
    ref = SourceRef(label=f"Danh sách dự án của {c.name} trên Dezon", url=c.profile_url,
                    updated_at=_latest(r.updated_at for r in refs if r))
    n = len(projects)
    n_img = sum(1 for p in projects if p.images)
    display = f"{vi_number(n, 0)} dự án có hồ sơ trên Dezon"
    if n_img:
        display += f" ({vi_number(n_img, 0)} có ảnh)"
    return make_evidence({"projects": n, "with_images": n_img}, display, source=ref,
                         status=_weakest_status(str(p.source.status) for p in projects if p.source))


def _fit_cell(s: ScoredContractor, version: int) -> EvidenceValue:
    ref = SourceRef(label=f"Đánh giá theo tiêu chí của anh/chị — Dezon Matching v{version}", url=None,
                    updated_at=date.today())
    return make_evidence({"score": s.score, "fit_label": s.fit_label}, f"{s.fit_label} · {s.score}/100",
                         source=ref, status=VerificationStatus.reference)


def _criteria_for_estimate_id(db: Session, estimate_session_id,
                              budget_vnd: int | None = None) -> MatchCriteria | None:
    """Tiêu chí chấm "Mức phù hợp" cho bảng so sánh — PHẢI giống tiêu chí đã chấm thẻ contractor_list.

    Thẻ trong chat được chấm từ slot phiên chat (tools._slot_criteria: tỉnh/quận/loại/hạng mục/ngân sách/
    số tầng + estimate_session_id). Ngân sách thường được khách nêu SAU khái toán nên không có trong
    estimate_sessions.input -> lấy từ chat_sessions.slots của phiên chat gắn với khái toán (W-F4).
    `budget_vnd` (tùy chọn, từ body) ưu tiên cao nhất.
    """
    es = db.get(EstimateSession, estimate_session_id)
    if es is None:
        return None
    inp = es.input or {}
    slots: dict = {}
    if es.chat_session_id:
        cs = db.get(ChatSession, es.chat_session_id)
        slots = dict(cs.slots or {}) if cs is not None else {}
    # Slot chat có đủ tỉnh + loại công trình -> dùng đúng bộ slot như lúc chấm thẻ; không thì dùng input khái toán.
    base = slots if slots.get("province") and slots.get("building_type") else inp
    budget = budget_vnd or slots.get("budget_vnd") or inp.get("budget_vnd")
    try:
        return MatchCriteria(province=base["province"], district=base.get("district"),
                             building_type=base["building_type"], work_scope=base.get("work_scope"),
                             floors=base.get("floors"), budget_vnd=int(budget) if budget else None,
                             estimate_session_id=es.id, converted_area_m2=float(es.converted_area_m2))
    except (KeyError, ValueError, TypeError):
        return None


COMPARISON_ROWS: tuple[tuple[str, str], ...] = (
    ("area", "Khu vực"),
    ("dezon_project_count", "Công trình trên Dezon"),
    ("experience", "Kinh nghiệm"),
    ("rough_price", "Đơn giá thô"),
    ("finish_price", "Đơn giá hoàn thiện"),
    ("projects", "Hồ sơ dự án"),
    ("warranty", "Bảo hành"),
    ("fit", "Mức phù hợp"),
)


def compare_contractors(db: Session, contractor_ids: list[int], criteria: MatchCriteria | None = None, *,
                        estimate_session_id: uuid.UUID | str | None = None,
                        budget_vnd: int | None = None) -> ComparisonMatrix:
    """Ma trận so sánh 2–5 nhà thầu (mục 8.2). Không ô trống: thiếu dữ liệu = unavailable."""
    ids = [int(i) for i in contractor_ids]
    if not 2 <= len(ids) <= 5:
        raise ValueError("Cần từ 2 đến 5 nhà thầu để so sánh")
    if len(set(ids)) != len(ids):
        raise ValueError("Danh sách nhà thầu so sánh bị trùng")
    contractors = _load_contractors(db, ids)
    if len(contractors) != len(ids):
        missing = sorted(set(ids) - {c.id for c in contractors})
        raise ValueError(f"Không tìm thấy nhà thầu: {missing}")
    ds_map = _load_sources(db, contractors)
    facts = {c.id: _facts(c, ds_map) for c in contractors}
    cards = {c.id: _card(c, facts[c.id]) for c in contractors}

    if criteria is None and estimate_session_id:
        criteria = _criteria_for_estimate_id(db, estimate_session_id, budget_vnd)
    fit: dict[int, EvidenceValue] = {}
    if criteria is not None:
        weights = load_active_weights(db)
        crit, _, _ = _criteria_from_estimate(db, criteria)
        neighbors = load_neighbors(db, crit.province)
        for cid, fc in facts.items():
            fit[cid] = _fit_cell(score_contractor(fc, crit, weights.weights, neighbors, crit.converted_area_m2),
                                 weights.version)

    def cell(c: Contractor, key: str) -> EvidenceValue:
        ev = cards[c.id].evidence
        if key == "area":
            return ev["province"]
        if key == "experience":
            return ev["years_experience"]
        if key == "projects":
            return _projects_cell(c)
        if key == "fit":
            return fit.get(c.id) or EvidenceValue.unavailable()
        return ev.get(key) or EvidenceValue.unavailable()

    rows = [ComparisonRow(key=k, label_vi=label, cells=[cell(c, k) for c in contractors])
            for k, label in COMPARISON_ROWS]
    return ComparisonMatrix(
        contractors=[ComparedContractor(id=c.id, slug=c.slug, name=c.name, profile_url=c.profile_url,
                                        avatar_url=c.avatar_url) for c in contractors],
        rows=rows,
    )


# ================================================================================================
# Câu hỏi tự do (mục 8.4)
# ================================================================================================
def contractor_overview(db: Session, province: str, building_type: str | BuildingType,
                        projects_per_contractor: int = 5) -> ContractorOverview:
    """Tổng quan: bao nhiêu nhà thầu phù hợp, ai có công trình loại đó thực tế (kèm link), số công trình
    ghi nhận, đơn giá tham khảo, thông tin bảo hành, hồ sơ đã xác minh. Không xếp hạng "tốt nhất"."""
    bt = str(building_type)
    bt_label = BUILDING_TYPE_VI.get(bt, bt).lower()
    contractors = _load_contractors(db, sorted(_ids_in_provinces(db, [province])), active_only=True)
    ds_map = _load_sources(db, contractors)
    items: list[tuple[Contractor, ContractorFacts]] = []
    for c in contractors:
        f = _facts(c, ds_map)
        if operates_in(f, province) and bt in building_types_of(f):
            items.append((c, f))

    entries: list[OverviewContractor] = []
    type_projects: dict[int, Project] = {}  # dự án đúng loại, không trùng giữa các nhà thầu
    for c, f in items:
        projs = _project_summaries(c, bt)
        type_projects.update({p.id: p for p in _unique_projects(c) if p.building_type == bt})
        entries.append(OverviewContractor(
            card=_card(c, f), has_projects_of_type=bool(projs), projects_of_type=projs[:projects_per_contractor],
            projects_of_type_count=len(projs),
            profile_verified=bool(f.default_source and f.default_source.status == VerificationStatus.verified.value),
        ))
    entries.sort(key=lambda e: (not e.has_projects_of_type,
                                -(e.card.evidence["dezon_project_count"].value or 0), e.card.name.casefold()))

    n = len(entries)
    notes: list[str] = []
    profile_srcs = [f.default_source for _, f in items if f.default_source]
    ref = SourceRef(label=f"Thống kê từ dữ liệu nhà thầu trên Dezon ({province}, {bt_label})",
                    url=f"{get_settings().dezon_site_url.rstrip('/')}/nha-thau",
                    updated_at=_latest(s.ref.updated_at for s in profile_srcs if s.ref))
    agg_status = _weakest_status(s.status for s in profile_srcs)

    def count_ev(value, display):
        return make_evidence(value, display, source=ref, status=agg_status)

    if n == 0:
        notes.append(f"Chưa ghi nhận nhà thầu {bt_label} tại {province} trên Dezon.")
        un = EvidenceValue.unavailable()
        return ContractorOverview(province=province, building_type=bt, contractor_count=un,
                                  contractors_with_projects=un, total_dezon_projects=un, rough_price_range=un,
                                  finish_price_range=un, with_warranty_info=un, verified_profiles=un,
                                  contractors=[], notes=notes)

    with_proj = sum(1 for e in entries if e.has_projects_of_type)
    counts = [f.dezon_project_count for _, f in items if f.dezon_project_count is not None]
    # Tổng mọi loại công trình của các nhà thầu trên — ghi rõ để không bị hiểu là số công trình loại được hỏi.
    total_projects = (count_ev(sum(counts), f"{vi_number(sum(counts), 0)} công trình ghi nhận trên Dezon "
                                            "(tổng mọi loại công trình của các nhà thầu trên)")
                      if counts else EvidenceValue.unavailable())
    n_type = len(type_projects)
    type_srcs = [p.source for p in type_projects.values() if p.source is not None]
    projects_of_type_count = make_evidence(
        n_type, f"{vi_number(n_type, 0)} công trình {bt_label} có trang dự án trên Dezon",
        source=SourceRef(label=f"Danh sách dự án {bt_label} của các nhà thầu tại {province} trên Dezon",
                         url=ref.url, updated_at=_latest(source_ref_from(ds).updated_at for ds in type_srcs)),
        status=_weakest_status(str(ds.status) for ds in type_srcs) if type_srcs else agg_status,
    ) if n_type else EvidenceValue.unavailable()
    if counts and len(counts) < n:
        notes.append(f"{n - len(counts)}/{n} nhà thầu chưa có số công trình ghi nhận trên Dezon.")

    def price_range(kind: str) -> EvidenceValue:
        rows = [(getattr(f, f"{kind}_price_min"), getattr(f, f"{kind}_price_max"), f)
                for _, f in items
                if getattr(f, f"{kind}_price_min") is not None or getattr(f, f"{kind}_price_max") is not None]
        if not rows:
            return EvidenceValue.unavailable()
        los = [lo if lo is not None else hi for lo, hi, _ in rows]
        his = [hi if hi is not None else lo for lo, hi, _ in rows]
        srcs = [f.source_for(f"{kind}_price_min", f"{kind}_price_max") for _, _, f in rows]
        pref = SourceRef(label=f"Tổng hợp từ hồ sơ {len(rows)}/{n} nhà thầu có công bố đơn giá trên Dezon",
                         url=ref.url, updated_at=_latest(s.ref.updated_at for s in srcs if s and s.ref))
        lo, hi = int(min(los)), int(max(his))
        return make_evidence([lo, hi], format_price_per_m2_range(lo, hi), source=pref,
                             status=_weakest_status(s.status for s in srcs if s))

    warranty = sum(1 for _, f in items if f.has_warranty_info)
    verified = sum(1 for e in entries if e.profile_verified)
    return ContractorOverview(
        province=province, building_type=bt,
        contractor_count=count_ev(n, f"{vi_number(n, 0)} nhà thầu"),
        contractors_with_projects=count_ev(
            with_proj, f"{with_proj}/{n} nhà thầu có công trình {bt_label} trong danh sách dự án trên Dezon"),
        total_dezon_projects=total_projects,
        rough_price_range=price_range("rough"),
        finish_price_range=price_range("finish"),
        with_warranty_info=count_ev(warranty, f"{warranty}/{n} nhà thầu có thông tin bảo hành"),
        verified_profiles=count_ev(verified, f"{verified}/{n} hồ sơ đã xác minh"),
        contractors=entries, notes=notes, projects_of_type_count=projects_of_type_count,
    )


# ================================================================================================
# Chỉ số dẫn xuất
# ================================================================================================
def recompute_profile_completeness(db: Session, commit: bool = False) -> int:
    """Tính lại contractors.profile_completeness (mục 7.3). Trả về số bản ghi thay đổi. Mặc định chỉ flush."""
    changed = 0
    for c in db.scalars(select(Contractor).options(selectinload(Contractor.capabilities),
                                                   selectinload(Contractor.project_links)
                                                   .selectinload(ProjectContractor.project))):
        value = Decimal(str(profile_completeness(_facts(c)))).quantize(Decimal("0.001"))
        if c.profile_completeness is None or Decimal(c.profile_completeness) != value:
            c.profile_completeness = value
            changed += 1
    db.flush()
    if commit:
        db.commit()
    return changed


def seed_hook(db: Session) -> None:
    """Được app.db.seed_hooks đăng ký: chạy sau seed cơ bản."""
    recompute_profile_completeness(db)
