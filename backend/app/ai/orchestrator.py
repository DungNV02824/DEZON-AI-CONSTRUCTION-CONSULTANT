"""AI Orchestrator — vòng lặp một lượt chat (đặc tả mục 9.1).

`handle_message(db, session_id, content, provider=None) -> TurnResult`

1. Lưu tin nhắn khách; nạp slots + stage + trạng thái phiên (`chat_sessions.slots["_state"]`, xem `app.ai.state`).
2. `slot_filling.extract` (expected_slots = slot câu hỏi trước) -> cập nhật slots; ghi analytics "slots_completed".
3. LUỒNG CHÍNH DO CODE ĐẢM BẢO (không phụ thuộc LLM nhớ gọi tool):
   - thiếu slot bắt buộc -> hỏi đúng slot thiếu (dialog.next_question) + quick replies;
   - đủ slot, chưa xác nhận -> tóm tắt + summary_confirm, không tính;
   - đã xác nhận, chưa có khái toán cho slot hiện tại -> orchestrator TỰ gọi calculate_estimate;
   - ngân sách < cost_min -> tự gọi suggest_budget_adjustments; sau khái toán -> tự gọi search_contractors
     (kèm estimate_session_id) và hỏi ngân sách nếu chưa có (dialog.budget_question).
   Tool tự gọi được đưa vào hội thoại như lượt tool-call để LLM giải thích kết quả.
4. Gọi LLM (system prompt + khối trạng thái + lịch sử rút gọn + tools), tối đa `max_tool_rounds` vòng tool.
5. Văn bản LLM qua `validator.run_guarded` (sinh lại 1 lần, vẫn sai -> câu dự phòng từ dữ liệu tool); vi phạm ghi
   `guardrail_violations`. Câu hỏi/tóm tắt do code dựng được nối vào cuối.
6. UI block dựng từ kết quả tool/code (`app.ai.blocks`), lưu cùng tin nhắn assistant; cập nhật stage.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import blocks as ui
from app.ai import fallback, slot_filling, validator
from app.ai.dialog import GREETING_EXPECTED_SLOTS, budget_question, build_summary, next_question
from app.ai.normalize import detect_intent, fold
from app.ai.providers import LLMError, get_provider
from app.ai.providers.base import LLMProvider, Message, ToolCall
from app.ai.slot_filling import SlotUpdate
from app.ai.state import (
    NON_ESTIMATE_SLOTS,
    ContractorRef,
    SessionState,
    estimate_slots_hash,
    public_slots,
    split_raw,
    to_raw,
)
from app.ai.tools import ToolContext, ToolOutcome, execute_tool, tool_specs
from app.config import get_settings
from app.db.models import AnalyticsEvent, ChatMessage, ChatSession
from app.schemas.estimate import BudgetAdjustmentResult, EstimateResult
from app.schemas.matching import ContractorProfile
from app.schemas.slots import Slots, missing_required
from app.schemas.ui_blocks import QuickRepliesBlock, SummaryConfirmBlock

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "system_vi.md"
HISTORY_LIMIT = 10  # số tin user/assistant gần nhất gửi LLM
PREVIOUS_TOOL_LIMIT = 40  # số tin tool gần nhất xét làm "kết quả lượt trước" cho validator

BUDGET_TAIL = ("Anh/chị cho em biết ngân sách dự kiến để em đánh giá mức phù hợp ngân sách của từng nhà thầu "
               "chính xác hơn nhé.")
LLM_UNAVAILABLE_TEXT = ("Dạ, hệ thống tư vấn đang bận, em chưa trả lời được câu này. Anh/chị vui lòng thử lại sau "
                        "ít phút giúp em nhé.")
EDIT_PROMPT = ("Dạ, anh/chị muốn sửa thông tin nào ạ? Anh/chị cứ nhắn thông tin mới (loại công trình, khu vực, diện "
               "tích, số tầng, phạm vi hoặc mức hoàn thiện), em sẽ cập nhật lại bản tóm tắt.")
_QUOTE_INTENT_RE = re.compile(r"\bbao gia\b")
_NOT_QUOTE_RE = re.compile(r"khong (?:phai|duoc xem la|thay the) (?:la )?bao gia")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
_ABBR_END_RE = re.compile(r"(?:^|\s)(?:TP|Tp|tp|Q|P|TX|TT|v\.v|St|NXB)\.$")

STAGES = ("collect", "estimate", "match", "present")


class SessionNotFoundError(LookupError):
    pass


@dataclass
class TurnResult:
    text: str
    ui_blocks: list[BaseModel]
    stage: str
    slots: dict[str, Any]
    missing_required: list[str]
    message_id: int
    used_fallback: bool = False
    violations: list[validator.Violation] = field(default_factory=list)
    tool_results: list[tuple[str, Any]] = field(default_factory=list)  # (tên tool, kết quả) thành công trong lượt
    tool_calls: list[str] = field(default_factory=list)  # tên tool đã chạy (cả tool code tự gọi)


@dataclass
class CodePlan:
    """Phần do code quyết định trong lượt."""
    tail: str | None = None  # câu hỏi / tóm tắt nối vào cuối câu trả lời
    summary: SummaryConfirmBlock | None = None
    quick: QuickRepliesBlock | None = None
    expected: list[str] = field(default_factory=list)
    is_collect: bool = False  # tail là câu hỏi slot / tóm tắt


@lru_cache
def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


# ============================================================================================
# Luồng chính do code đảm bảo
# ============================================================================================
def _collect_plan(slots: Slots) -> CodePlan | None:
    if missing_required(slots):
        q = next_question(slots)
        if q is not None:
            return CodePlan(tail=q.text, quick=q.quick_replies, expected=list(q.slots), is_collect=True)
    if not missing_required(slots) and not slots.confirmed:
        text, block = build_summary(slots)
        return CodePlan(tail=text, summary=block, expected=[], is_collect=True)
    return None


def _forced(ctx: ToolContext, outcomes: list[ToolOutcome], name: str, args: dict | None = None) -> ToolOutcome:
    o = execute_tool(ctx, name, args or {}, forced=True, call_id=f"auto_{len(outcomes) + 1}_{uuid.uuid4().hex[:8]}")
    outcomes.append(o)
    return o


def _pre_llm(ctx: ToolContext, outcomes: list[ToolOutcome]) -> CodePlan:
    plan = _collect_plan(ctx.slots)
    if plan is not None:
        return plan
    plan = CodePlan()
    s, st = ctx.slots, ctx.state
    new_estimate = False
    if st.estimate is None or st.estimate.slots_hash != estimate_slots_hash(s):
        o = _forced(ctx, outcomes, "calculate_estimate")
        new_estimate = not o.is_error
    est = st.estimate
    if est is None:
        return plan
    budget = s.budget_vnd
    if budget and est.cost_min and budget < est.cost_min and st.budget_checked_for != f"{est.id}:{budget}":
        _forced(ctx, outcomes, "suggest_budget_adjustments")
    matched_now = False
    match_key = f"{est.id}:{budget or 0}"
    if st.matched_for != match_key:
        o = _forced(ctx, outcomes, "search_contractors")
        if not o.is_error:
            st.matched_for = match_key
            matched_now = True
    bq = budget_question(s) if (new_estimate or matched_now) else None
    if bq is not None:
        plan.tail, plan.quick, plan.expected = BUDGET_TAIL, bq.quick_replies, list(bq.slots)
    elif matched_now and st.contractors:
        plan.quick = QuickRepliesBlock(options=list(ui.NEXT_ACTION_REPLIES))
    return plan


def _next_step(slots: Slots, plan: CodePlan, outcomes: list[ToolOutcome]) -> str:
    missing = missing_required(slots)
    if missing:
        labels = ", ".join(fallback.SLOT_LABEL_VI.get(k, k) for k in missing)
        return f"Khách còn thiếu: {labels}. Hệ thống sẽ tự hỏi; em chỉ ghi nhận ngắn hoặc trả lời câu hỏi của khách."
    if not slots.confirmed:
        return ("Đã đủ thông tin bắt buộc. Hệ thống tự hiển thị bản tóm tắt; chờ khách xác nhận, KHÔNG gọi "
                "calculate_estimate.")
    done = {o.name for o in outcomes if o.forced and not o.is_error}
    steps: list[str] = []
    if "calculate_estimate" in done:
        steps.append("Giải thích ngắn khái toán vừa tính: gồm gì theo phạm vi, chưa gồm gì (exclusions), yếu tố làm "
                     "tăng chi phí (cost_drivers); nhắc đây là khái toán sơ bộ, không phải báo giá.")
    if "suggest_budget_adjustments" in done:
        steps.append("Ngân sách thấp hơn khái toán: trình bày các phương án điều chỉnh (chi phí lấy từ kết quả tool).")
    if "search_contractors" in done:
        steps.append("Giới thiệu ngắn các nhà thầu đề xuất kèm lý do (reasons); mời xem hồ sơ, so sánh hoặc yêu cầu "
                     "báo giá.")
    if not steps:
        steps.append("Trả lời câu hỏi của khách bằng tool phù hợp (compare_contractors, get_contractor_profile, "
                     "get_contractor_overview, get_contractor_projects, get_reference_rates, search_knowledge, "
                     "create_quote_request).")
    return " ".join(steps)


def _protect_profile(prev: Slots, u: SlotUpdate, content: str) -> SlotUpdate:
    """Câu HỎI khi khách đã cung cấp đủ thông tin (vd "nhà thầu nào thi công biệt thự tốt?", "đơn giá xây thô bao
    nhiêu?") không được ghi đè thông tin công trình; chỉ nhận ngân sách/thời gian. Muốn đổi thì khách nói rõ ("đổi
    sang ...", "sửa lại ...") -> detect_intent = edit."""
    if missing_required(prev) or detect_intent(content) != "question":
        return u
    kept = [c for c in u.changed if c in NON_ESTIMATE_SLOTS]
    if len(kept) == len(u.changed):
        return u
    slots = prev.model_copy(update={k: getattr(u.slots, k) for k in kept})
    return u.model_copy(update={"slots": slots, "changed": kept, "intent": "question",
                                "missing_required": missing_required(slots)})


def _drop_questions(text: str) -> str:
    """Bỏ câu hỏi LLM tự đặt ở lượt thu thập: câu hỏi slot/tóm tắt do code dựng là câu hỏi duy nhất (không hỏi lại
    slot đã có, không hỏi trùng)."""
    lines = []
    for line in (text or "").splitlines():
        sentences: list[str] = []
        for piece in _SENTENCE_SPLIT_RE.split(line.strip()):
            if sentences and _ABBR_END_RE.search(sentences[-1]):  # "TP. Hồ Chí Minh" không phải hai câu
                sentences[-1] = f"{sentences[-1]} {piece}"
            else:
                sentences.append(piece)
        kept = [x for x in sentences if x and not x.rstrip(" )»\"'”").endswith("?")]
        joined = " ".join(kept).strip()
        if joined or not line.strip():
            lines.append(joined)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _stage(slots: Slots, state: SessionState) -> str:
    if missing_required(slots):
        return "collect"
    if not slots.confirmed:
        return "estimate"
    if state.presented and state.contractors:
        return "present"
    if state.contractors and state.matched_for:
        return "match"
    return "estimate"


# ============================================================================================
# Lịch sử, ngữ cảnh
# ============================================================================================
def _history(db: Session, sid: uuid.UUID) -> list[ChatMessage]:
    rows = db.scalars(select(ChatMessage).where(ChatMessage.session_id == sid,
                                                ChatMessage.role.in_(("user", "assistant")))
                      .order_by(ChatMessage.id.desc()).limit(HISTORY_LIMIT)).all()
    return list(reversed(rows))


def _all_user_texts(db: Session, sid: uuid.UUID) -> list[str]:
    return [c or "" for c in db.scalars(select(ChatMessage.content).where(
        ChatMessage.session_id == sid, ChatMessage.role == "user").order_by(ChatMessage.id)).all()]


def _previous_results(db: Session, sid: uuid.UUID, before_id: int) -> list[tuple[str, Any]]:
    """Kết quả tool gần nhất của từng tool ở các lượt trước (để LLM nhắc lại con số đã hiển thị).

    Bọc trong {"previous": ...} và đặt tên "context:<tool>" để câu dự phòng không lặp lại kết quả cũ.
    """
    rows = db.scalars(select(ChatMessage).where(ChatMessage.session_id == sid, ChatMessage.role == "tool",
                                                ChatMessage.id < before_id)
                      .order_by(ChatMessage.id.desc()).limit(PREVIOUS_TOOL_LIMIT)).all()
    out: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        payload = r.tool_payload if isinstance(r.tool_payload, dict) else {}
        name = r.tool_name or ""
        if (not name or name in seen or name in validator.DESCRIPTION_ONLY_TOOLS or payload.get("is_error")
                or payload.get("result") is None):
            continue
        seen.add(name)
        out.append((f"context:{name}", {"previous": payload["result"]}))
    return out


def _state_block(slots: Slots, state: SessionState, u: SlotUpdate, plan: CodePlan,
                 outcomes: list[ToolOutcome], stage: str) -> dict[str, Any]:
    est = state.estimate
    return {
        "stage": stage,
        "slots": slots.model_dump(mode="json", exclude_none=True, exclude={"asked"}),
        "missing_required": missing_required(slots),
        "intent": u.intent,
        "thong_tin_moi": u.changed,
        "khai_toan_hien_tai": {"estimate_session_id": est.id, "cost": est.cost_display} if est else None,
        "nha_thau_da_de_xuat": [{"id": c.id, "name": c.name} for c in state.contractors],
        "cong_cu_he_thong_da_chay": [o.name for o in outcomes if o.forced],
        "buoc_tiep_theo": _next_step(slots, plan, outcomes),
        "he_thong_se_tu_them": plan.tail,
    }


def _system_with_state(block: dict[str, Any]) -> str:
    return (system_prompt() + "\n\n## TRẠNG THÁI PHIÊN (do hệ thống tính, không phải lời khách)\n"
            + json.dumps(block, ensure_ascii=False, indent=1))


# ============================================================================================
# Bảo đảm nội dung bắt buộc trong văn bản
# ============================================================================================
def _guarantees(text: str, outcomes: list[ToolOutcome]) -> list[str]:
    extra: list[str] = []
    for o in outcomes:
        if o.is_error:
            continue
        if o.name == "calculate_estimate" and isinstance(o.result, EstimateResult):
            r = o.result
            if r.cost.has_value and r.cost.display not in text:
                extra.append(f"Chi phí sơ bộ dự kiến: {r.cost.display}.")
            if not _NOT_QUOTE_RE.search(fold(text)):
                extra.append(fallback.ESTIMATE_NOTE)
        if (o.name == "suggest_budget_adjustments" and isinstance(o.result, BudgetAdjustmentResult)
                and not any(opt.fits_budget for opt in o.result.options)):
            extra.append(f"Theo đơn giá hiện có, chưa có phương án nào đưa chi phí về trong ngân sách "
                         f"{o.result.target_budget.display} của anh/chị.")
    # khử trùng lặp, giữ thứ tự
    return list(dict.fromkeys(extra))


def _quote_form(content: str, state: SessionState, outcomes: list[ToolOutcome]):
    if not _QUOTE_INTENT_RE.search(fold(content)):
        return None
    if any(o.name == "create_quote_request" and not o.is_error for o in outcomes):
        return None
    contractors = list(state.contractors)
    if not contractors:
        for o in outcomes:
            if o.name == "get_contractor_profile" and isinstance(o.result, ContractorProfile):
                contractors.append(ContractorRef(id=o.result.card.id, name=o.result.card.name,
                                                 slug=o.result.card.slug))
    return ui.quote_form(contractors, state.estimate.id if state.estimate else None)


# ============================================================================================
# Vòng lặp chính
# ============================================================================================
def _as_uuid(session_id: uuid.UUID | str) -> uuid.UUID:
    if isinstance(session_id, uuid.UUID):
        return session_id
    try:
        return uuid.UUID(str(session_id))
    except ValueError as exc:
        raise SessionNotFoundError(str(session_id)) from exc


def handle_message(db: Session, session_id: uuid.UUID | str, content: str,
                   provider: LLMProvider | None = None) -> TurnResult:
    settings = get_settings()
    sid = _as_uuid(session_id)
    sess = db.get(ChatSession, sid)
    if sess is None:
        raise SessionNotFoundError(str(sid))
    prev_slots, state = split_raw(sess.slots)
    history = _history(db, sid)

    # 1. Lưu tin nhắn khách (commit sớm: lỗi về sau không làm mất tin nhắn).
    user_msg = ChatMessage(session_id=sid, role="user", content=content)
    db.add(user_msg)
    db.commit()
    provider = provider or get_provider(settings)
    user_texts = _all_user_texts(db, sid)

    # 2. Slot filling.
    state.user_turns += 1
    expected = state.expected_slots if state.expected_slots is not None else list(GREETING_EXPECTED_SLOTS)
    u = _protect_profile(prev_slots, slot_filling.extract(content, prev_slots, provider, expected_slots=expected),
                         content)
    slots = u.slots
    est_changed = [c for c in u.changed if c not in NON_ESTIMATE_SLOTS]
    if prev_slots.confirmed and not slots.confirmed and u.changed and not est_changed:
        slots = slots.model_copy(update={"confirmed": True})  # chỉ đổi ngân sách/thời gian: không xác nhận lại
    if est_changed and state.estimate is not None:
        state.estimate = None
        state.budget_checked_for = state.matched_for = None
        state.presented = False
    if not missing_required(slots) and not state.slots_completed_logged:
        db.add(AnalyticsEvent(session_id=sid, event="slots_completed", props={"turns": state.user_turns}))
        db.commit()  # tool lỗi hệ thống sẽ rollback phiên DB — không được làm mất sự kiện này
        state.slots_completed_logged = True

    # 3. Luồng chính do code đảm bảo (tool tự gọi trước LLM).
    ctx = ToolContext(db=db, chat_session_id=sid, slots=slots, state=state, user_texts=user_texts)
    outcomes: list[ToolOutcome] = []
    plan = _pre_llm(ctx, outcomes)
    if u.intent == "edit" and not u.changed and plan.summary is not None and plan.tail:
        plan.tail = f"{EDIT_PROMPT}\n\n{plan.tail}"
    stage_now = _stage(ctx.slots, state)

    # 4. Gọi LLM.
    state_block = _state_block(ctx.slots, state, u, plan, outcomes, stage_now)
    system = _system_with_state(state_block)
    llm_ctx = {**state_block, "last_user_message": content, "retry": False}
    specs = tool_specs()
    messages: list[Message] = [Message(role=m.role, content=m.content or "") for m in history]
    messages.append(Message(role="user", content=content))
    forced = [o for o in outcomes if o.forced]
    if forced:
        messages.append(Message(role="assistant", content="",
                                tool_calls=[ToolCall(id=o.call_id, name=o.name, arguments=o.arguments)
                                            for o in forced]))
        for o in forced:
            messages.append(Message(role="tool", content=o.llm_content(), tool_call_id=o.call_id, name=o.name))

    draft, draft_raw, llm_failed = "", None, False
    try:
        rounds = 0
        while True:
            resp = provider.chat(system, messages, specs, llm_ctx)
            if resp.tool_calls and rounds < settings.max_tool_rounds:
                rounds += 1
                messages.append(Message(role="assistant", content=resp.text or "", tool_calls=list(resp.tool_calls),
                                        raw=getattr(resp, "raw", None)))
                for tc in resp.tool_calls:
                    o = execute_tool(ctx, tc.name, tc.arguments, call_id=tc.id)
                    outcomes.append(o)
                    messages.append(Message(role="tool", content=o.llm_content(), tool_call_id=tc.id, name=tc.name))
                continue
            draft = resp.text or ""
            draft_raw = getattr(resp, "raw", None) if not resp.tool_calls else None
            break
    except LLMError as exc:
        log.warning("LLM lỗi (%s): %s — trả lời dự phòng", exc.kind, exc)
        llm_failed = True
    except Exception:  # noqa: BLE001 — lỗi provider bất ngờ cũng không được làm hỏng lượt chat
        log.exception("Provider %s lỗi bất ngờ — trả lời dự phòng", getattr(provider, "name", "?"))
        llm_failed = True

    # Tool update_project_requirements có thể đổi slot giữa lượt -> dựng lại câu hỏi/tóm tắt.
    if any(o.name == "update_project_requirements" and not o.is_error for o in outcomes):
        replan = _collect_plan(ctx.slots)
        if replan is not None:
            plan = replan
        elif plan.is_collect:
            plan = CodePlan()

    # 5. Validator + sinh lại + dự phòng.
    turn_named = [(o.name, o.result) for o in outcomes if not o.is_error and o.result is not None]
    v_results: list[Any] = [*turn_named, ("session_context", {"slots": public_slots(ctx.slots)}),
                            *_previous_results(db, sid, user_msg.id)]
    violations: list[validator.Violation] = []
    used_fallback = False
    if llm_failed:
        used_fallback = True
        llm_part = fallback.build_fallback_text(turn_named) if turn_named else ""
        if not turn_named and not plan.tail:
            llm_part = LLM_UNAVAILABLE_TEXT
    else:
        retry_failed = False

        def generate(retry_msg: str | None) -> str:
            nonlocal retry_failed
            if retry_msg is None:
                return draft
            msgs = [*messages, Message(role="assistant", content=draft or "…", raw=draft_raw),
                    Message(role="user", content=retry_msg)]
            try:
                return provider.chat(system, msgs, specs, {**llm_ctx, "retry": True}).text or ""
            except Exception:  # noqa: BLE001
                log.warning("LLM lỗi khi sinh lại — dùng câu dự phòng", exc_info=True)
                retry_failed = True
                return fallback.build_fallback_text(turn_named) if turn_named else ""

        def on_violation(vs: list[validator.Violation], answer: str, attempt: int) -> None:
            validator.record_violations(db, sid, vs, answer, attempt)
            db.commit()

        guarded = validator.run_guarded(generate, v_results, user_texts, on_violation=on_violation)
        violations = guarded.violations
        used_fallback = guarded.used_fallback or retry_failed
        llm_part = guarded.text
        if guarded.used_fallback and llm_part == fallback.GENERIC_APOLOGY and plan.tail:
            llm_part = ""  # câu hỏi/tóm tắt do code dựng đã đủ
        elif plan.is_collect and plan.tail and u.intent != "question":
            llm_part = _drop_questions(llm_part)

    parts = [llm_part, *_guarantees(llm_part, outcomes), plan.tail or ""]
    text = "\n\n".join(p.strip() for p in parts if p and p.strip()) or fallback.GENERIC_APOLOGY

    # 6. UI block + lưu.
    est_id = state.estimate.id if state.estimate else None
    blocks = ui.build_ui_blocks(outcomes, estimate_session_id=est_id, summary=plan.summary,
                                quick_replies=plan.quick, quote_request=_quote_form(content, state, outcomes))
    for o in outcomes:
        db.add(ChatMessage(session_id=sid, role="tool", tool_name=o.name, tool_payload=o.payload))
    assistant = ChatMessage(session_id=sid, role="assistant", content=text,
                            ui_blocks=[b.model_dump(mode="json") for b in blocks])
    db.add(assistant)
    state.expected_slots = plan.expected
    stage = _stage(ctx.slots, state)
    sess = db.get(ChatSession, sid)
    sess.slots = to_raw(ctx.slots, state)
    sess.stage = stage
    sess.updated_at = datetime.now(UTC)
    db.flush()
    message_id = assistant.id
    db.commit()
    return TurnResult(text=text, ui_blocks=blocks, stage=stage, slots=public_slots(ctx.slots),
                      missing_required=missing_required(ctx.slots), message_id=message_id,
                      used_fallback=used_fallback, violations=violations, tool_results=turn_named,
                      tool_calls=[o.name for o in outcomes])


__all__ = ["STAGES", "SessionNotFoundError", "TurnResult", "handle_message", "system_prompt"]
