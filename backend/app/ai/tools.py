"""Định nghĩa & thực thi tool của orchestrator (đặc tả mục 9.2).

Mỗi tool có input là model Pydantic (`extra="forbid"`) -> JSON Schema gửi cho LLM; thực thi bằng các service đã có
(estimate_service, contractor_service, knowledge_service, quote_service, slot_filling). Mọi tool ghi log (logging +
chat_messages role=tool do orchestrator lưu). Lỗi -> `ToolOutcome.is_error=True` kèm thông báo tiếng Việt để LLM
xử lý, không làm hỏng lượt chat.

Guardrail bằng code trong tool:
- `calculate_estimate` bị chặn khi khách chưa xác nhận bản tóm tắt (mục 5.3).
- `create_quote_request` chỉ nhận tên/SĐT có xuất hiện trong tin nhắn của khách (chống LLM bịa thông tin liên hệ).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.ai import slot_filling
from app.ai.normalize import fold
from app.ai.providers.base import ToolSpec
from app.ai.state import ContractorRef, EstimateRef, SessionState, estimate_slots_hash
from app.db.models import AnalyticsEvent
from app.schemas.estimate import EstimateInput
from app.schemas.knowledge import USAGE_NOTE_VI
from app.schemas.matching import MatchCriteria
from app.schemas.slots import Slots, missing_required, to_estimate_input
from app.services import contractor_service, estimate_service, knowledge_service, quote_service

log = logging.getLogger(__name__)

BuildingTypeLit = Literal["nha_pho", "biet_thu", "can_ho", "van_phong", "khach_san", "khac"]
WorkScopeLit = Literal["xay_tho", "hoan_thien", "tron_goi", "noi_that"]
QualityTierLit = Literal["tiet_kiem", "trung_binh", "kha", "cao_cap"]


class ToolError(Exception):
    """Lỗi nghiệp vụ trả lại cho LLM (thông báo tiếng Việt)."""


# =============================================================================================
# Input models
# =============================================================================================
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpdateProjectRequirementsIn(_In):
    building_type: BuildingTypeLit | None = Field(default=None, description="Loại công trình")
    province: str | None = Field(default=None, description="Tỉnh/thành phố khách nói")
    district: str | None = Field(default=None, description="Quận/huyện khách nói")
    land_width_m: float | None = Field(default=None, description="Chiều ngang đất (m)")
    land_length_m: float | None = Field(default=None, description="Chiều dài đất (m)")
    land_area_m2: float | None = Field(default=None, description="Diện tích đất (m²)")
    building_area_m2: float | None = Field(default=None, description="Diện tích xây dựng mỗi tầng (m²)")
    floors: int | None = Field(default=None, description="Số tầng nổi tính cả trệt")
    has_mezzanine: bool | None = None
    basement: bool | None = None
    roof_type: Literal["roof_metal", "roof_concrete", "roof_tile_on_truss", "roof_tile_on_concrete"] | None = None
    rooftop_terrace: Literal["none", "covered", "open"] | None = None
    foundation_type: Literal["foundation_single", "foundation_strip", "foundation_pile"] | None = None
    work_scope: WorkScopeLit | None = None
    quality_tier: QualityTierLit | None = None
    budget_vnd: int | None = Field(default=None, description="Ngân sách khách nói (VND)")
    start_time: str | None = Field(default=None, description="Thời gian dự kiến khởi công, nguyên văn")


class CalculateEstimateIn(_In):
    """Bỏ trống mọi trường = tính theo thông tin khách đã xác nhận (slots). Trường truyền vào ghi đè slots
    (dùng cho câu hỏi 'nếu ... thì sao')."""
    building_type: BuildingTypeLit | None = None
    province: str | None = None
    district: str | None = None
    land_width_m: float | None = None
    land_length_m: float | None = None
    land_area_m2: float | None = None
    building_area_m2: float | None = None
    floors: int | None = Field(default=None, ge=1, le=50)
    has_mezzanine: bool | None = None
    basement: bool | None = None
    basement_depth_m: float | None = None
    roof_type: Literal["roof_metal", "roof_concrete", "roof_tile_on_truss", "roof_tile_on_concrete", "none"] | None = None
    rooftop_terrace: Literal["none", "covered", "open"] | None = None
    foundation_type: Literal["foundation_single", "foundation_strip", "foundation_pile"] | None = None
    work_scope: WorkScopeLit | None = None
    quality_tier: QualityTierLit | None = None


class SuggestBudgetAdjustmentsIn(_In):
    estimate_session_id: str | None = Field(default=None, description="Bỏ trống = khái toán hiện tại của phiên")
    target_budget_vnd: int | None = Field(default=None, gt=0, description="Bỏ trống = ngân sách khách đã nói")


class SearchContractorsIn(_In):
    province: str | None = Field(default=None, description="Bỏ trống = theo slots")
    district: str | None = None
    building_type: BuildingTypeLit | None = None
    work_scope: WorkScopeLit | None = None
    budget_vnd: int | None = Field(default=None, gt=0)
    estimate_session_id: str | None = Field(default=None, description="Bỏ trống = khái toán hiện tại")
    limit: int = Field(default=5, ge=3, le=5)


class ContractorRefIn(_In):
    contractor_id: int | None = Field(default=None, description="id nhà thầu (trong kết quả tool trước)")
    slug: str | None = None
    name: str | None = Field(default=None, description="Tên nhà thầu khách nhắc tới, vd 'Huy Hoàng'")


class GetContractorProjectsIn(ContractorRefIn):
    building_type: BuildingTypeLit | None = None
    limit: int = Field(default=10, ge=1, le=20)


class CompareContractorsIn(_In):
    contractor_ids: list[int] | None = Field(default=None, description="2–5 id; bỏ trống = các nhà thầu vừa đề xuất")
    names: list[str] | None = Field(default=None, description="Hoặc tên nhà thầu (2–5)")


class GetReferenceRatesIn(_In):
    building_type: BuildingTypeLit | None = None
    province: str | None = None
    work_scope: WorkScopeLit | None = None
    quality_tier: QualityTierLit | None = None


class SearchKnowledgeIn(_In):
    query: str = Field(min_length=1, max_length=500)
    entity_type: Literal["contractor", "project", "article"] | None = None


class CreateQuoteRequestIn(_In):
    contractor_ids: list[int] | None = Field(default=None, description="1–5 id; bỏ trống = các nhà thầu vừa đề xuất")
    estimate_session_id: str | None = Field(default=None, description="Bỏ trống = khái toán hiện tại")
    customer_name: str = Field(min_length=1, max_length=200, description="Tên khách — đúng như khách đã nói")
    customer_phone: str = Field(min_length=8, max_length=20, description="SĐT khách — đúng như khách đã nói")
    customer_email: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class GetContractorOverviewIn(_In):
    province: str | None = Field(default=None, description="Bỏ trống = theo slots")
    building_type: BuildingTypeLit | None = None


# =============================================================================================
# Context / outcome
# =============================================================================================
@dataclass
class ToolContext:
    db: Session
    chat_session_id: uuid.UUID | None
    slots: Slots
    state: SessionState
    user_texts: list[str] = field(default_factory=list)
    cache: dict[str, Any] = field(default_factory=dict)  # khử trùng lặp trong một lượt (vd tính khái toán 2 lần)
    forcing: bool = False  # tool đang được orchestrator tự gọi (luồng chính)


@dataclass
class ToolOutcome:
    name: str
    arguments: dict[str, Any]
    result: Any = None  # đối tượng gốc (pydantic/dict) — dùng cho validator, UI block, fallback
    is_error: bool = False
    error: str | None = None
    forced: bool = False
    call_id: str = ""

    @property
    def payload(self) -> dict[str, Any]:
        """Bản lưu chat_messages.tool_payload (đầy đủ)."""
        return {"arguments": self.arguments, "result": to_jsonable(self.result), "is_error": self.is_error,
                "error": self.error, "forced": self.forced}

    def llm_content(self) -> str:
        """Nội dung tool result gửi LLM: JSON gọn."""
        if self.is_error:
            return json.dumps({"is_error": True, "error": self.error}, ensure_ascii=False)
        return json.dumps({"ok": True, "result": compact_for_llm(to_jsonable(self.result))}, ensure_ascii=False)


@dataclass
class ToolDef:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], Any]

    def spec(self) -> ToolSpec:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        schema.pop("description", None)
        return ToolSpec(name=self.name, description=self.description, input_schema=schema)


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, uuid.UUID):
        return str(obj)
    return obj


_DROP_KEYS = frozenset({"avatar_url", "cover_images", "images", "breakdown", "total_images", "chips",
                        "profile_source", "group_name"})
_LIST_LIMITS = {"projects": 5, "projects_of_type": 3, "chunks": 5, "items": 20, "contractors": 10}
_MAX_STR = 500


def compact_for_llm(obj: Any, key: str = "") -> Any:
    """Rút gọn JSON cho LLM: bỏ ảnh/breakdown, cắt danh sách dài và chuỗi dài; GIỮ display/status/source/is_stale."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _DROP_KEYS:
                continue
            if k == "source" and isinstance(v, dict):
                out[k] = {kk: v.get(kk) for kk in ("label", "url", "updated_at") if v.get(kk) is not None}
                continue
            out[k] = compact_for_llm(v, k)
        return out
    if isinstance(obj, list):
        limit = _LIST_LIMITS.get(key)
        items = obj[:limit] if limit else obj
        res = [compact_for_llm(v, key) for v in items]
        if limit and len(obj) > limit:
            res.append({"_note": f"còn {len(obj) - limit} mục không hiển thị"})
        return res
    if isinstance(obj, str) and len(obj) > _MAX_STR:
        return obj[:_MAX_STR].rsplit(" ", 1)[0] + "…"
    return obj


# =============================================================================================
# Helpers
# =============================================================================================
def _uuid(v: str | None) -> uuid.UUID | None:
    if not v:
        return None
    try:
        return uuid.UUID(str(v))
    except ValueError as exc:
        raise ToolError("Mã phiên khái toán không hợp lệ") from exc


def _current_estimate_id(ctx: ToolContext) -> str | None:
    return ctx.state.estimate.id if ctx.state.estimate else None


def _resolve_contractor(ctx: ToolContext, contractor_id: int | None, slug: str | None,
                        name: str | None) -> int | str:
    if contractor_id:
        return int(contractor_id)
    if slug:
        return slug
    if not name or not name.strip():
        raise ToolError("Cần contractor_id, slug hoặc tên nhà thầu")
    fname = fold(name).strip()
    for c in ctx.state.contractors:  # ưu tiên nhà thầu vừa đề xuất trong phiên
        if fname and (fname in fold(c.name) or fold(c.name) in fname):
            return c.id
    cards, _ = contractor_service.search_contractors_simple(ctx.db, q=name.strip(), limit=5)
    if not cards:
        raise ToolError(f"Không tìm thấy nhà thầu có tên \"{name.strip()}\" trên Dezon")
    return cards[0].id


def _slot_criteria(ctx: ToolContext) -> MatchCriteria | None:
    s = ctx.slots
    if not s.province or not s.building_type:
        return None
    est = _current_estimate_id(ctx)
    return MatchCriteria(province=s.province, district=s.district, building_type=s.building_type,
                         work_scope=s.work_scope, budget_vnd=s.budget_vnd, floors=s.floors,
                         estimate_session_id=_uuid(est) if est else None)


def _cache_key(name: str, data: Any) -> str:
    return name + ":" + json.dumps(to_jsonable(data), sort_keys=True, ensure_ascii=False, default=str)


# =============================================================================================
# Handlers
# =============================================================================================
def _update_requirements(ctx: ToolContext, inp: UpdateProjectRequirementsIn) -> dict[str, Any]:
    # Đối chiếu với lời khách để LLM không đưa được số bịa vào slot (slot được validator coi là nguồn hợp lệ).
    u = slot_filling.update_slots(ctx.slots, inp.model_dump(exclude_none=True),
                                  message="\n".join(ctx.user_texts) or None)
    ctx.slots = u.slots
    return {"slots": u.slots.model_dump(mode="json", exclude={"asked"}), "missing_required": u.missing_required,
            "changed": u.changed}


def _calculate_estimate(ctx: ToolContext, inp: CalculateEstimateIn) -> Any:
    if not ctx.slots.confirmed and not ctx.forcing:
        raise ToolError("Khách chưa xác nhận bản tóm tắt thông tin. Hãy tóm tắt và chờ khách xác nhận "
                        "trước khi tính khái toán.")
    overrides = inp.model_dump(exclude_none=True)
    missing = missing_required(ctx.slots)
    if missing and not overrides:
        raise ToolError(f"Chưa đủ thông tin bắt buộc để khái toán: {', '.join(missing)}")
    base = to_estimate_input(ctx.slots).model_dump(exclude_unset=True) if not missing else {}
    if "land_area_m2" in overrides and not ({"land_width_m", "land_length_m"} & set(overrides)):
        base.pop("land_width_m", None)
        base.pop("land_length_m", None)
    try:
        est_in = EstimateInput.model_validate({**base, **overrides})
    except ValidationError as exc:
        raise ToolError("Thông tin khái toán chưa hợp lệ: "
                        + "; ".join(str(e.get("msg")) for e in exc.errors()[:3])) from exc
    from_slots = not missing and all(base.get(k) == v for k, v in overrides.items())
    key = _cache_key("calculate_estimate", est_in)
    if key in ctx.cache:
        return ctx.cache[key]
    result = estimate_service.create_estimate(ctx.db, est_in, chat_session_id=ctx.chat_session_id)
    ctx.cache[key] = result
    if from_slots:
        ctx.state.estimate = EstimateRef(id=str(result.estimate_session_id), cost_min=result.cost_min,
                                         cost_max=result.cost_max, cost_display=result.cost.display,
                                         slots_hash=estimate_slots_hash(ctx.slots))
    ctx.db.add(AnalyticsEvent(session_id=ctx.chat_session_id, event="estimate_created",
                              props={"estimate_session_id": str(result.estimate_session_id),
                                     "from_slots": from_slots, "has_cost": result.cost_min is not None}))
    return result


def _suggest_budget(ctx: ToolContext, inp: SuggestBudgetAdjustmentsIn) -> Any:
    esid = inp.estimate_session_id or _current_estimate_id(ctx)
    if not esid:
        raise ToolError("Chưa có khái toán trong phiên; cần tính khái toán trước")
    budget = inp.target_budget_vnd or ctx.slots.budget_vnd
    if not budget:
        raise ToolError("Khách chưa cho biết ngân sách")
    key = _cache_key("suggest_budget_adjustments", [esid, budget])
    if key in ctx.cache:
        return ctx.cache[key]
    try:
        result = estimate_service.suggest_budget_adjustments(ctx.db, esid, int(budget))
    except estimate_service.EstimateNotFoundError as exc:
        raise ToolError("Không tìm thấy phiên khái toán") from exc
    ctx.cache[key] = result
    ctx.state.budget_checked_for = f"{esid}:{int(budget)}"
    return result


def _search_contractors(ctx: ToolContext, inp: SearchContractorsIn) -> Any:
    s = ctx.slots
    province = inp.province or s.province
    building_type = inp.building_type or (str(s.building_type) if s.building_type else None)
    if not province or not building_type:
        raise ToolError("Cần khu vực (tỉnh/thành) và loại công trình để tìm nhà thầu")
    district = inp.district if inp.district else (s.district if not inp.province or inp.province == s.province
                                                  else None)
    esid = inp.estimate_session_id or _current_estimate_id(ctx)
    crit = MatchCriteria(province=province, district=district, building_type=building_type,
                         work_scope=inp.work_scope or s.work_scope, budget_vnd=inp.budget_vnd or s.budget_vnd,
                         floors=s.floors, estimate_session_id=_uuid(esid), limit=inp.limit)
    key = _cache_key("search_contractors", crit)
    if key in ctx.cache:
        return ctx.cache[key]
    try:
        result = contractor_service.match_contractors(ctx.db, crit, chat_session_id=ctx.chat_session_id)
    except LookupError as exc:
        raise ToolError("Chưa cấu hình trọng số matching") from exc
    ctx.cache[key] = result
    ctx.state.recommendation_log_id = result.recommendation_log_id
    ctx.state.contractors = [ContractorRef(id=r.contractor.id, name=r.contractor.name, slug=r.contractor.slug)
                             for r in result.results]
    return result


def _get_profile(ctx: ToolContext, inp: ContractorRefIn) -> Any:
    ref = _resolve_contractor(ctx, inp.contractor_id, inp.slug, inp.name)
    profile = contractor_service.get_contractor_profile(ctx.db, ref)
    if profile is None:
        raise ToolError("Không tìm thấy nhà thầu trên Dezon")
    ctx.state.presented = True
    return profile


def _get_projects(ctx: ToolContext, inp: GetContractorProjectsIn) -> dict[str, Any]:
    ref = _resolve_contractor(ctx, inp.contractor_id, inp.slug, inp.name)
    profile = contractor_service.get_contractor_profile(ctx.db, ref)
    if profile is None:
        raise ToolError("Không tìm thấy nhà thầu trên Dezon")
    projects = contractor_service.get_contractor_projects(ctx.db, profile.card.id, inp.building_type, inp.limit)
    return {"contractor": {"id": profile.card.id, "name": profile.card.name,
                           "profile_url": profile.card.profile_url}, "projects": projects}


def _compare(ctx: ToolContext, inp: CompareContractorsIn) -> Any:
    ids: list[int] = list(inp.contractor_ids or [])
    for n in inp.names or []:
        rid = _resolve_contractor(ctx, None, None, n)
        if isinstance(rid, int) and rid not in ids:
            ids.append(rid)
    if not ids:
        ids = [c.id for c in ctx.state.contractors][:5]
    ids = list(dict.fromkeys(ids))
    if len(ids) < 2:
        raise ToolError("Cần ít nhất 2 nhà thầu để so sánh; hãy tìm nhà thầu trước (search_contractors)")
    if len(ids) > 5:
        ids = ids[:5]
    try:
        result = contractor_service.compare_contractors(ctx.db, ids, _slot_criteria(ctx))
    except LookupError as exc:
        raise ToolError("Chưa cấu hình trọng số matching") from exc
    ctx.state.presented = True
    return result


def _reference_rates(ctx: ToolContext, inp: GetReferenceRatesIn) -> dict[str, Any]:
    s = ctx.slots
    bt = inp.building_type or (str(s.building_type) if s.building_type else None)
    province = inp.province or s.province
    ws = inp.work_scope or (str(s.work_scope) if s.work_scope else None)
    if not (bt and province and ws):
        raise ToolError("Cần loại công trình, khu vực và phạm vi thi công để tra đơn giá tham chiếu")
    qt = inp.quality_tier or (str(s.quality_tier) if s.quality_tier else "trung_binh")
    ev, rate = estimate_service.get_reference_rates(ctx.db, bt, province, ws, qt)
    return {"rate": ev, "rate_data": rate}


def _search_knowledge(ctx: ToolContext, inp: SearchKnowledgeIn) -> dict[str, Any]:
    chunks = knowledge_service.search_knowledge(ctx.db, inp.query, inp.entity_type, limit=5)
    return {"chunks": chunks, "usage_note": USAGE_NOTE_VI}


_DIGITS_RE = re.compile(r"\+?\d[\d\s.\-()]{7,}\d")


def _phones_in(texts: list[str]) -> set[str]:
    out: set[str] = set()
    for t in texts:
        for m in _DIGITS_RE.finditer(t or ""):
            try:
                out.add(quote_service.normalize_phone(m.group(0)))
            except ValueError:
                continue
    return out


def _create_quote(ctx: ToolContext, inp: CreateQuoteRequestIn) -> Any:
    try:
        phone = quote_service.normalize_phone(inp.customer_phone)
    except ValueError as exc:
        raise ToolError("Số điện thoại không hợp lệ (cần dạng 0xxxxxxxxx)") from exc
    if phone not in _phones_in(ctx.user_texts):
        raise ToolError("Số điện thoại phải do khách tự cung cấp trong tin nhắn; hãy hỏi khách số điện thoại")
    joined = fold(" ".join(ctx.user_texts))
    if fold(inp.customer_name).strip() not in joined:
        raise ToolError("Tên khách phải do khách tự cung cấp trong tin nhắn; hãy hỏi tên khách")
    ids = list(inp.contractor_ids or [c.id for c in ctx.state.contractors][:5])
    if not ids:
        raise ToolError("Chưa có nhà thầu nào để gửi yêu cầu báo giá; hãy tìm nhà thầu trước")
    esid = inp.estimate_session_id or _current_estimate_id(ctx)
    try:
        data = quote_service.QuoteRequestIn(contractor_ids=ids[:5], estimate_session_id=_uuid(esid),
                                            customer_name=inp.customer_name, customer_phone=phone,
                                            customer_email=inp.customer_email, note=inp.note)
    except ValidationError as exc:
        raise ToolError("Thông tin yêu cầu báo giá chưa hợp lệ: "
                        + "; ".join(str(e.get("msg")) for e in exc.errors()[:3])) from exc
    try:
        result = quote_service.create_quote_requests(ctx.db, data, chat_session_id=ctx.chat_session_id)
    except quote_service.QuoteRequestError as exc:
        raise ToolError("Không tạo được yêu cầu báo giá: nhà thầu hoặc phiên khái toán không tồn tại") from exc
    ctx.state.presented = True
    return result


def _overview(ctx: ToolContext, inp: GetContractorOverviewIn) -> Any:
    province = inp.province or ctx.slots.province
    bt = inp.building_type or (str(ctx.slots.building_type) if ctx.slots.building_type else None)
    if not province or not bt:
        raise ToolError("Cần khu vực (tỉnh/thành) và loại công trình để tổng hợp nhà thầu")
    return contractor_service.contractor_overview(ctx.db, province, bt)


# =============================================================================================
# Registry
# =============================================================================================
TOOLS: dict[str, ToolDef] = {t.name: t for t in [
    ToolDef("update_project_requirements",
            "Ghi nhận/cập nhật thông tin công trình khách vừa cung cấp (loại công trình, khu vực, diện tích, số tầng, "
            "phạm vi, mức hoàn thiện, ngân sách...). Chỉ truyền trường khách NÓI RÕ. Trả về slots hiện tại và các "
            "thông tin bắt buộc còn thiếu (missing_required).",
            UpdateProjectRequirementsIn, _update_requirements),
    ToolDef("calculate_estimate",
            "Tính khái toán chi phí xây dựng bằng công thức của Dezon (diện tích quy đổi × đơn giá tham chiếu). "
            "Chỉ gọi SAU KHI khách đã xác nhận bản tóm tắt. Bỏ trống tham số = dùng thông tin khách đã xác nhận; "
            "truyền tham số để tính phương án giả định. Trả về bảng hạng mục, chi phí (cost.display), đơn giá "
            "(rate.display, nguồn), giả định, phần chưa bao gồm, yếu tố tăng chi phí, miễn trừ.",
            CalculateEstimateIn, _calculate_estimate),
    ToolDef("suggest_budget_adjustments",
            "Khi ngân sách của khách thấp hơn khái toán: engine thử các phương án (giảm tầng, đổi mức hoàn thiện, "
            "bỏ hầm, đổi mái, xây thô trước...) và trả chi phí mới của từng phương án.",
            SuggestBudgetAdjustmentsIn, _suggest_budget),
    ToolDef("search_contractors",
            "Đề xuất 3–5 nhà thầu trên Dezon phù hợp với tiêu chí (khu vực, loại công trình, phạm vi, ngân sách, khái "
            "toán hiện tại). Trả về danh sách đã xếp hạng kèm nhãn mức phù hợp, lý do (reasons) và điểm cần lưu ý.",
            SearchContractorsIn, _search_contractors),
    ToolDef("get_contractor_profile",
            "Hồ sơ đầy đủ của một nhà thầu (năm thành lập, nhân sự, đơn giá xây thô/hoàn thiện, quy mô dự án, số công "
            "trình ghi nhận trên Dezon, bảo hành, danh sách công trình + link). Tìm theo contractor_id, slug HOẶC tên. "
            "Dùng khi khách hỏi về một nhà thầu cụ thể (vd 'đơn giá của Huy Hoàng').",
            ContractorRefIn, _get_profile),
    ToolDef("get_contractor_projects",
            "Danh sách công trình thực tế của một nhà thầu trên Dezon (tên, địa điểm, loại công trình, link dự án).",
            GetContractorProjectsIn, _get_projects),
    ToolDef("compare_contractors",
            "Lập bảng so sánh 2–5 nhà thầu (khu vực, công trình trên Dezon, kinh nghiệm, đơn giá, hồ sơ dự án, bảo "
            "hành, mức phù hợp). Ô thiếu dữ liệu là 'Chưa có dữ liệu'. Bỏ trống = so sánh các nhà thầu vừa đề xuất.",
            CompareContractorsIn, _compare),
    ToolDef("get_reference_rates",
            "Tra đơn giá tham chiếu (VND/m²) theo loại công trình, khu vực, phạm vi, mức hoàn thiện, kèm nguồn và "
            "ngày cập nhật.",
            GetReferenceRatesIn, _reference_rates),
    ToolDef("search_knowledge",
            "Tìm mô tả trong hồ sơ nhà thầu, dự án, bài viết trên Dezon (RAG). CHỈ dùng để mô tả, KHÔNG dùng làm "
            "nguồn số liệu.",
            SearchKnowledgeIn, _search_knowledge),
    ToolDef("create_quote_request",
            "Gửi yêu cầu báo giá tới nhà thầu (kèm khái toán hiện tại). Chỉ gọi khi khách đã tự cung cấp họ tên và "
            "số điện thoại trong tin nhắn; tuyệt đối không tự điền.",
            CreateQuoteRequestIn, _create_quote),
    ToolDef("get_contractor_overview",
            "Tổng quan nhà thầu theo khu vực và loại công trình (trả lời câu hỏi tự do như 'nhà thầu nào thi công "
            "biệt thự tại TP.HCM'): số nhà thầu phù hợp, ai có công trình thực tế (kèm link), số công trình ghi "
            "nhận, đơn giá tham khảo, thông tin bảo hành, hồ sơ đã xác minh. Không xếp hạng chất lượng.",
            GetContractorOverviewIn, _overview),
]}


def tool_specs() -> list[ToolSpec]:
    return [t.spec() for t in TOOLS.values()]


def _error_summary(exc: ValidationError) -> str:
    parts = []
    for e in exc.errors()[:3]:
        loc = ".".join(str(x) for x in e.get("loc", ()))
        parts.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return "Tham số không hợp lệ — " + "; ".join(parts)


def execute_tool(ctx: ToolContext, name: str, arguments: dict[str, Any] | None, *, forced: bool = False,
                 call_id: str = "") -> ToolOutcome:
    """Validate input bằng Pydantic, thực thi, trả ToolOutcome. Không ném lỗi nghiệp vụ ra ngoài."""
    args = dict(arguments or {})
    outcome = ToolOutcome(name=name, arguments=args, forced=forced, call_id=call_id)
    tool = TOOLS.get(name)
    if tool is None:
        outcome.is_error, outcome.error = True, f"Không có công cụ tên {name}"
        log.warning("tool không tồn tại: %s", name)
        return outcome
    try:
        inp = tool.input_model.model_validate(args)
    except ValidationError as exc:
        outcome.is_error, outcome.error = True, _error_summary(exc)
        log.info("tool %s: input không hợp lệ (%s)", name, outcome.error)
        return outcome
    ctx.forcing = forced
    try:
        outcome.result = tool.handler(ctx, inp)
    except ToolError as exc:
        outcome.is_error, outcome.error = True, str(exc)
    except ValueError as exc:  # vd building_type/enum sai, ngân sách ≤ 0 từ service
        outcome.is_error, outcome.error = True, f"Giá trị không hợp lệ: {exc}"
    except Exception:  # noqa: BLE001 — lỗi hệ thống của một tool không được làm hỏng lượt chat
        log.exception("tool %s lỗi hệ thống", name)
        ctx.db.rollback()
        outcome.is_error, outcome.error = True, "Lỗi hệ thống khi thực thi công cụ"
    finally:
        ctx.forcing = False
    log.info("tool %s session=%s forced=%s ok=%s", name, ctx.chat_session_id, forced, not outcome.is_error)
    return outcome
