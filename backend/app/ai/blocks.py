"""Dựng UI block từ KẾT QUẢ TOOL / CODE (đặc tả mục 2.1, 8.1) — không bao giờ từ văn bản LLM."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.ai.state import ContractorRef
from app.ai.tools import ToolOutcome
from app.evidence import EvidenceValue, SourceRef, VerificationStatus, make_evidence
from app.formatting import vi_number
from app.schemas.common import BUILDING_TYPE_VI
from app.schemas.estimate import BudgetAdjustmentResult, EstimateResult
from app.schemas.matching import (
    ComparedContractor,
    ComparisonMatrix,
    ComparisonRow,
    ContractorOverview,
    ContractorProfile,
    MatchResult,
)
from app.schemas.ui_blocks import (
    BudgetOptionsBlock,
    ComparisonTableBlock,
    ContractorDetailBlock,
    ContractorListBlock,
    EstimateTableBlock,
    QuickRepliesBlock,
    QuickReply,
    QuoteFormContractor,
    QuoteRequestFormBlock,
    SourceBadge,
    SourceBadgesBlock,
    SummaryConfirmBlock,
)
from app.services.estimate_service import CUSTOMER_BUDGET_LABEL

MAX_BADGES = 8
MAX_QUOTE_CONTRACTORS = 5
# Nguồn không phải dữ liệu Dezon (không hiển thị chip nguồn).
_SKIP_SOURCE_LABELS = {CUSTOMER_BUDGET_LABEL}

NEXT_ACTION_REPLIES = [
    QuickReply(label="So sánh các nhà thầu", value="So sánh các nhà thầu"),
    QuickReply(label="Yêu cầu báo giá", value="Tôi muốn yêu cầu báo giá"),
]


def _ok(outcomes: Iterable[ToolOutcome], name: str) -> list[ToolOutcome]:
    return [o for o in outcomes if o.name == name and not o.is_error and o.result is not None]


def _iter_evidence(obj: Any) -> Iterable[dict[str, Any]]:
    """Duyệt mọi EvidenceValue (dict có display/status/source) trong JSON kết quả tool."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    if isinstance(obj, dict):
        if {"display", "status", "source"} <= set(obj):
            yield obj
        for v in obj.values():
            yield from _iter_evidence(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_evidence(v)


def source_badges(results: Sequence[Any]) -> SourceBadgesBlock | None:
    """Chip nguồn của các evidence đã dùng (khử trùng lặp theo label + url + ngày)."""
    seen: set[tuple] = set()
    badges: list[SourceBadge] = []
    for res in results:
        for ev in _iter_evidence(res):
            src = ev.get("source")
            status = ev.get("status")
            if not isinstance(src, dict) or status == VerificationStatus.unavailable.value or ev.get("value") is None:
                continue
            label = src.get("label") or ""
            if not label or label in _SKIP_SOURCE_LABELS or not (src.get("url") or src.get("updated_at")):
                continue
            key = (label, src.get("url"), src.get("updated_at"))
            if key in seen:
                continue
            seen.add(key)
            badges.append(SourceBadge(label=label, status=VerificationStatus(status),
                                      source=SourceRef.model_validate(src)))
            if len(badges) >= MAX_BADGES:
                return SourceBadgesBlock(badges=badges)
    return SourceBadgesBlock(badges=badges) if badges else None


MAX_OVERVIEW_CONTRACTORS = 5
# Cột của bảng tổng quan (mục 8.4) — dùng block comparison_table (frontend đã hiển thị được): mọi số trong câu trả
# lời tổng quan truy được trên UI, nguồn hiển thị bằng chấm màu/badge thay vì lặp trong câu.
_OVERVIEW_ROWS = (("dezon_project_count", "Công trình ghi nhận trên Dezon"), ("rough_price", "Đơn giá xây thô"),
                  ("finish_price", "Đơn giá hoàn thiện"), ("warranty", "Bảo hành"))


def projects_of_type_evidence(entry: Any, bt_label: str) -> EvidenceValue:
    """"N công trình <loại> có trang dự án" của một nhà thầu trong ContractorOverview."""
    projs = list(entry.projects_of_type or [])
    n = getattr(entry, "projects_of_type_count", 0) or len(projs)
    src = next((p.source for p in projs if p.source is not None), None) or entry.card.profile_source
    return make_evidence(n, f"{vi_number(n, 0)} công trình {bt_label} có trang dự án",
                         source=src, status=None if src is None else VerificationStatus.on_dezon)


def overview_table(ov: ContractorOverview) -> ComparisonTableBlock | None:
    entries = list(ov.contractors)[:MAX_OVERVIEW_CONTRACTORS]
    if not entries:
        return None
    bt_label = BUILDING_TYPE_VI.get(str(ov.building_type), str(ov.building_type)).lower()
    rows = [ComparisonRow(key="projects_of_type", label_vi=f"Công trình {bt_label} có trang dự án",
                          cells=[projects_of_type_evidence(e, bt_label) for e in entries])]
    rows += [ComparisonRow(key=k, label_vi=label,
                           cells=[e.card.evidence.get(k) or EvidenceValue.unavailable() for e in entries])
             for k, label in _OVERVIEW_ROWS]
    return ComparisonTableBlock(data=ComparisonMatrix(
        contractors=[ComparedContractor(id=e.card.id, slug=e.card.slug, name=e.card.name,
                                        profile_url=e.card.profile_url, avatar_url=e.card.avatar_url)
                     for e in entries],
        rows=rows))


def quote_form(contractors: Sequence[ContractorRef], estimate_session_id: str | UUID | None
               ) -> QuoteRequestFormBlock | None:
    if not contractors:
        return None
    return QuoteRequestFormBlock(
        contractors=[QuoteFormContractor(id=c.id, name=c.name) for c in contractors[:MAX_QUOTE_CONTRACTORS]],
        estimate_session_id=UUID(str(estimate_session_id)) if estimate_session_id else None,
    )


def build_ui_blocks(outcomes: Sequence[ToolOutcome], *, estimate_session_id: str | UUID | None = None,
                    summary: SummaryConfirmBlock | None = None, quick_replies: QuickRepliesBlock | None = None,
                    quote_request: QuoteRequestFormBlock | None = None) -> list[BaseModel]:
    """Thứ tự: bảng dữ liệu (khái toán, phương án ngân sách, nhà thầu, so sánh, hồ sơ) → form báo giá → chip nguồn
    → tóm tắt xác nhận → nút chọn nhanh."""
    blocks: list[BaseModel] = []
    est_id = UUID(str(estimate_session_id)) if estimate_session_id else None

    if est := _ok(outcomes, "calculate_estimate"):
        res = est[-1].result
        if isinstance(res, EstimateResult):
            blocks.append(EstimateTableBlock(data=res))
    if bud := _ok(outcomes, "suggest_budget_adjustments"):
        res = bud[-1].result
        if isinstance(res, BudgetAdjustmentResult):
            blocks.append(BudgetOptionsBlock(data=res))
    if match := _ok(outcomes, "search_contractors"):
        res = match[-1].result
        if isinstance(res, MatchResult) and res.results:
            blocks.append(ContractorListBlock(data=res))
    if cmp_ := _ok(outcomes, "compare_contractors"):
        res = cmp_[-1].result
        if isinstance(res, ComparisonMatrix):
            blocks.append(ComparisonTableBlock(data=res))
    elif ov := _ok(outcomes, "get_contractor_overview"):
        res = ov[-1].result
        if isinstance(res, ContractorOverview) and (table := overview_table(res)) is not None:
            blocks.append(table)
    seen_profiles: set[int] = set()
    profiles = [o.result for o in _ok(outcomes, "get_contractor_profile")]
    profiles += [o.result.get("profile") for o in _ok(outcomes, "get_contractor_projects")
                 if isinstance(o.result, dict)]
    for prof in profiles:
        if isinstance(prof, ContractorProfile) and prof.card.id not in seen_profiles:
            seen_profiles.add(prof.card.id)
            blocks.append(ContractorDetailBlock(data=prof, estimate_session_id=est_id))
    if quote_request is not None:
        blocks.append(quote_request)
    badges = source_badges([o.result for o in outcomes if not o.is_error and o.result is not None
                            and o.name != "search_knowledge"])
    if badges is not None:
        blocks.append(badges)
    if summary is not None:
        blocks.append(summary)
    if quick_replies is not None:
        blocks.append(quick_replies)
    return blocks
