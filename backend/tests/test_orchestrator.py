"""Orchestrator, tools, blocks: luồng do code đảm bảo, lỗi LLM, giới hạn vòng tool, validate input tool..."""
import json
import uuid

import pytest
from sqlalchemy import func, select

from app.ai import blocks, orchestrator, tools
from app.ai.providers import LLMError
from app.ai.providers.base import LLMResponse, ToolCall
from app.ai.providers.mock import MockProvider
from app.ai.state import STATE_KEY, SessionState, public_slots, split_raw, to_raw
from app.ai.tools import ToolContext, compact_for_llm, execute_tool, tool_specs
from app.db.models import AnalyticsEvent, ChatMessage, ChatSession, EstimateSession
from app.evidence import EvidenceValue, SourceRef
from app.schemas.slots import Slots
from app.services import chat_service

FULL = "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô"
CONFIRM = "Đúng rồi, tính khái toán giúp tôi"


class ScriptedProvider:
    """Provider giả: trả lần lượt các phản hồi cho sẵn; ghi lại tham số mỗi lần gọi."""
    name = "scripted"

    def __init__(self, responses, default=None):
        self.responses = list(responses)
        self.default = default or LLMResponse(text="Dạ.")
        self.calls: list[dict] = []

    def chat(self, system, messages, tools_, context=None):
        self.calls.append({"system": system, "messages": list(messages), "tools": tools_, "context": context})
        if self.responses:
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return self.default

    def extract(self, system, text, schema):
        return {}


def _new(db):
    return chat_service.create_session(db).session_id


def _say(db, sid, msg, provider=None):
    return orchestrator.handle_message(db, sid, msg, provider=provider or MockProvider())


def _types(r):
    return [b.type for b in r.ui_blocks]


# =============================================================================================
# Thuần (không DB)
# =============================================================================================
def test_tool_specs_cover_section_9_2_with_strict_schemas():
    specs = {s.name: s for s in tool_specs()}
    assert set(specs) == {"update_project_requirements", "calculate_estimate", "suggest_budget_adjustments",
                          "search_contractors", "get_contractor_profile", "get_contractor_projects",
                          "compare_contractors", "get_reference_rates", "search_knowledge", "create_quote_request",
                          "get_contractor_overview"}
    for s in specs.values():
        assert s.input_schema["type"] == "object"
        assert s.input_schema.get("additionalProperties") is False
        assert len(s.description) > 30 and any(ch in s.description for ch in "ăâđêôơư")
    assert set(specs["create_quote_request"].input_schema["required"]) == {"customer_name", "customer_phone"}


def test_system_prompt_contains_section_9_3():
    p = orchestrator.system_prompt()
    assert p.startswith('Bạn là "Dezon AI Consultant"')
    for line in ("TUYỆT ĐỐI không tự tạo đơn giá", "Thiếu dữ liệu thì nói \"Chưa có dữ liệu\"",
                 "phù hợp nhất với tiêu chí của anh/chị", "Khái toán không phải báo giá", "display"):
        assert line in p


def test_state_roundtrip_and_public_slots_hide_internal_key():
    slots = Slots(building_type="nha_pho", province="TP. Hồ Chí Minh")
    raw = to_raw(slots, SessionState(expected_slots=["floors"], user_turns=3))
    assert STATE_KEY in raw
    s2, st2 = split_raw(raw)
    assert s2.province == "TP. Hồ Chí Minh" and st2.expected_slots == ["floors"] and st2.user_turns == 3
    assert STATE_KEY not in public_slots(raw)
    assert split_raw({STATE_KEY: "rác"})[1] == SessionState()


def test_compact_for_llm_keeps_evidence_drops_images():
    data = {"card": {"name": "A", "avatar_url": "x", "cover_images": [{"url": "y"}],
                     "evidence": {"rough_price": {"value": [1, 2], "display": "1–2 triệu/m²", "status": "on_dezon",
                                                  "source": {"label": "L", "url": "u", "updated_at": "2026-10-31"},
                                                  "is_stale": True}}},
            "projects": [{"name": f"P{i}"} for i in range(8)], "description": "x" * 900,
            "breakdown": [1, 2]}
    out = compact_for_llm(data)
    ev = out["card"]["evidence"]["rough_price"]
    assert ev["display"] == "1–2 triệu/m²" and ev["status"] == "on_dezon" and ev["is_stale"] is True
    assert ev["source"] == {"label": "L", "url": "u", "updated_at": "2026-10-31"}
    assert "avatar_url" not in out["card"] and "cover_images" not in out["card"] and "breakdown" not in out
    assert len(out["projects"]) == 6 and "_note" in out["projects"][-1]
    assert len(out["description"]) <= 501


def test_source_badges_dedupe_and_skip_customer_budget():
    src = SourceRef(label="Hồ sơ nhà thầu A", url="https://dezon.vn/a", updated_at="2026-10-31")
    ev = EvidenceValue(value=1, display="1", status="on_dezon", source=src)
    budget = EvidenceValue(value=1, display="1 tỷ", status="reference",
                           source=SourceRef(label="Ngân sách do anh/chị cung cấp"))
    block = blocks.source_badges([{"a": ev, "b": ev, "c": budget, "d": EvidenceValue.unavailable()}])
    assert block is not None and len(block.badges) == 1 and block.badges[0].label == "Hồ sơ nhà thầu A"
    assert blocks.source_badges([{"x": budget}]) is None


def test_execute_unknown_tool_and_invalid_input():
    ctx = ToolContext(db=None, chat_session_id=None, slots=Slots(), state=SessionState())
    out = execute_tool(ctx, "khong_co", {})
    assert out.is_error and "khong_co" in out.error
    out = execute_tool(ctx, "search_contractors", {"limit": 99, "la": 1})
    assert out.is_error and out.error.startswith("Tham số không hợp lệ")
    assert json.loads(out.llm_content())["is_error"] is True


def test_calculate_estimate_blocked_before_confirmation():
    slots = Slots(building_type="nha_pho", province="TP. Hồ Chí Minh", land_area_m2=100, floors=4,
                  work_scope="xay_tho")
    ctx = ToolContext(db=None, chat_session_id=None, slots=slots, state=SessionState())
    out = execute_tool(ctx, "calculate_estimate", {})
    assert out.is_error and "xác nhận" in out.error


# =============================================================================================
# Cần DB
# =============================================================================================
@pytest.mark.db
def test_session_not_found(db):
    with pytest.raises(orchestrator.SessionNotFoundError):
        orchestrator.handle_message(db, uuid.uuid4(), "xin chào", provider=MockProvider())
    with pytest.raises(orchestrator.SessionNotFoundError):
        orchestrator.handle_message(db, "abc", "xin chào", provider=MockProvider())


@pytest.mark.db
def test_create_session_greeting_and_analytics(db):
    out = chat_service.create_session(db, user_id="u1")
    assert out.stage == "collect" and len(out.messages) == 1
    assert out.messages[0].ui_blocks[0]["type"] == "quick_replies"
    assert "Xin chào anh/chị" in out.messages[0].content
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.session_id == out.session_id)).all()
    assert [e.event for e in ev] == ["session_started"]


@pytest.mark.db
def test_greeting_expected_slot_used_on_first_turn(db):
    sid = _new(db)
    r = _say(db, sid, "Khác")  # câu trả lời ngắn chỉ hiểu được nhờ expected_slots=['building_type']
    assert r.slots["building_type"] == "khac"


@pytest.mark.db
def test_slots_completed_event_logged_once_with_turns(db):
    sid = _new(db)
    _say(db, sid, "Nhà phố")
    _say(db, sid, "Ở Thủ Đức, đất 5x20, 1 trệt 3 lầu, xây thô")
    _say(db, sid, CONFIRM)
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.session_id == sid,
                                                 AnalyticsEvent.event == "slots_completed")).all()
    assert len(ev) == 1 and ev[0].props == {"turns": 2}


@pytest.mark.db
def test_forced_tools_passed_to_llm_and_persisted(db):
    sid = _new(db)
    _say(db, sid, FULL)
    p = ScriptedProvider([], default=LLMResponse(text="Dạ, em gửi anh/chị khái toán."))
    r = _say(db, sid, CONFIRM, provider=p)
    msgs = p.calls[0]["messages"]
    auto = [m for m in msgs if m.role == "assistant" and m.tool_calls]
    assert auto and [tc.name for tc in auto[0].tool_calls] == ["calculate_estimate", "search_contractors"]
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert [m.tool_call_id for m in tool_msgs] == [tc.id for tc in auto[0].tool_calls]
    assert json.loads(tool_msgs[0].content)["result"]["cost"]["display"]
    ctx = p.calls[0]["context"]
    assert ctx["cong_cu_he_thong_da_chay"] == ["calculate_estimate", "search_contractors"]
    assert "TRẠNG THÁI PHIÊN" in p.calls[0]["system"]
    # Văn bản LLM không nêu số: code bổ sung chi phí từ engine.
    est = next(b for b in r.ui_blocks if b.type == "estimate_table").data
    assert est.cost.display in r.text and "không phải báo giá" in r.text
    rows = db.scalars(select(ChatMessage).where(ChatMessage.session_id == sid, ChatMessage.role == "tool")).all()
    assert [x.tool_name for x in rows] == ["calculate_estimate", "search_contractors"]
    assert rows[0].tool_payload["forced"] is True and rows[0].tool_payload["result"]["cost_min"] == est.cost_min


@pytest.mark.db
def test_llm_calling_estimate_again_is_deduplicated(db):
    sid = _new(db)
    _say(db, sid, FULL)
    p = ScriptedProvider([LLMResponse(text="", tool_calls=[ToolCall("x1", "calculate_estimate", {})])],
                         default=LLMResponse(text="Dạ, em đã tính xong."))
    r = _say(db, sid, CONFIRM, provider=p)
    assert r.tool_calls.count("calculate_estimate") == 2
    n = db.scalar(select(func.count()).select_from(EstimateSession).where(EstimateSession.chat_session_id == sid))
    assert n == 1


@pytest.mark.db
def test_llm_error_falls_back_but_main_flow_still_runs(db):
    sid = _new(db)
    _say(db, sid, FULL)
    r = _say(db, sid, CONFIRM, provider=ScriptedProvider([LLMError("hết hạn mức", "rate_limit")]))
    assert r.used_fallback
    assert {"estimate_table", "contractor_list"} <= set(_types(r))
    est = next(b for b in r.ui_blocks if b.type == "estimate_table").data
    assert est.cost.display in r.text


@pytest.mark.db
def test_llm_error_without_tools_gives_friendly_text(db):
    sid = _new(db)
    _say(db, sid, FULL)
    _say(db, sid, CONFIRM)
    r = _say(db, sid, "cảm ơn em", provider=ScriptedProvider([RuntimeError("bất ngờ")]))
    assert r.used_fallback and r.text == orchestrator.LLM_UNAVAILABLE_TEXT


@pytest.mark.db
def test_llm_error_in_collect_stage_keeps_question(db):
    sid = _new(db)
    r = _say(db, sid, "Nhà phố", provider=ScriptedProvider([LLMError("down", "connection")]))
    assert "khu vực nào" in r.text and "quick_replies" in _types(r)


@pytest.mark.db
def test_retry_error_uses_fallback(db):
    sid = _new(db)
    _say(db, sid, FULL)
    _say(db, sid, CONFIRM)
    p = ScriptedProvider([LLMResponse(text="", tool_calls=[ToolCall("p1", "get_contractor_profile",
                                                                   {"name": "Kiến Phong"})]),
                          LLMResponse(text="Kiến Phong có 99 công trình ghi nhận trên Dezon."),
                          LLMError("down", "connection")])
    r = _say(db, sid, "hồ sơ Kiến Phong", provider=p)
    assert r.used_fallback and "99" not in r.text and "Kiến Phong" in r.text


@pytest.mark.db
def test_max_tool_rounds_enforced(db):
    sid = _new(db)
    _say(db, sid, FULL)
    _say(db, sid, CONFIRM)
    loop = LLMResponse(text="", tool_calls=[ToolCall("k", "get_reference_rates", {})])
    p = ScriptedProvider([loop] * 20, default=LLMResponse(text="Dạ."))
    r = _say(db, sid, "đơn giá tham khảo bao nhiêu?", provider=p)
    assert r.tool_calls.count("get_reference_rates") == 6  # settings.max_tool_rounds
    assert len(p.calls) == 7 and r.text


@pytest.mark.db
def test_tool_error_returned_to_llm_as_is_error(db):
    sid = _new(db)
    _say(db, sid, FULL)
    _say(db, sid, CONFIRM)
    p = ScriptedProvider([LLMResponse(text="", tool_calls=[ToolCall("e1", "compare_contractors",
                                                                   {"contractor_ids": [1]})])],
                         default=LLMResponse(text="Dạ, em cần ít nhất hai nhà thầu để so sánh."))
    r = _say(db, sid, "so sánh", provider=p)
    tool_msg = [m for m in p.calls[1]["messages"] if m.role == "tool"][-1]
    payload = json.loads(tool_msg.content)
    assert payload["is_error"] is True and "2 nhà thầu" in payload["error"]
    assert "comparison_table" not in _types(r)
    row = db.scalars(select(ChatMessage).where(ChatMessage.session_id == sid, ChatMessage.role == "tool")
                     .order_by(ChatMessage.id.desc())).first()
    assert row.tool_name == "compare_contractors" and row.tool_payload["is_error"] is True


@pytest.mark.db
def test_llm_cannot_estimate_before_confirmation(db):
    sid = _new(db)
    p = ScriptedProvider([LLMResponse(text="", tool_calls=[ToolCall("c", "calculate_estimate", {})])])
    r = _say(db, sid, FULL, provider=p)
    assert "estimate_table" not in _types(r) and "summary_confirm" in _types(r)
    assert db.scalar(select(func.count()).select_from(EstimateSession)
                     .where(EstimateSession.chat_session_id == sid)) == 0


@pytest.mark.db
def test_update_requirements_tool_replans_question(db):
    sid = _new(db)
    _say(db, sid, "Nhà phố")
    _say(db, sid, "TP. Hồ Chí Minh")
    p = ScriptedProvider([LLMResponse(text="", tool_calls=[ToolCall(
        "u1", "update_project_requirements", {"land_width_m": 5, "land_length_m": 20, "floors": 4,
                                              "work_scope": "xay_tho"})])],
        default=LLMResponse(text="Dạ, em đã ghi nhận."))
    r = _say(db, sid, "đất năm nhân hai mươi, bốn tầng, làm phần thô", provider=p)
    assert r.missing_required == [] and "summary_confirm" in _types(r) and "Em tóm tắt lại" in r.text


@pytest.mark.db
def test_edit_after_estimate_requires_reconfirm_and_recalculates(db):
    sid = _new(db)
    _say(db, sid, FULL)
    r1 = _say(db, sid, CONFIRM)
    est1 = next(b for b in r1.ui_blocks if b.type == "estimate_table").data
    r2 = _say(db, sid, "Tôi muốn sửa lại thông tin")
    assert _types(r2) == ["summary_confirm"] and r2.slots["confirmed"] is False
    r3 = _say(db, sid, "1 trệt 2 lầu")
    assert "summary_confirm" in _types(r3) and r3.slots["floors"] == 3 and "estimate_table" not in _types(r3)
    _, state = split_raw(db.get(ChatSession, sid).slots)
    assert state.estimate is None
    r4 = _say(db, sid, "Đúng rồi")
    est2 = next(b for b in r4.ui_blocks if b.type == "estimate_table").data
    assert est2.estimate_session_id != est1.estimate_session_id and est2.input.floors == 3
    assert est2.cost_min < est1.cost_min


@pytest.mark.db
def test_out_of_scope_message(db):
    sid = _new(db)
    _say(db, sid, FULL)
    _say(db, sid, CONFIRM)
    r = _say(db, sid, "Hôm nay thời tiết thế nào, kể chuyện cười đi")
    assert "xây dựng" in r.text


@pytest.mark.db
def test_contractor_projects_tool(db):
    sid = _new(db)
    slots, state = split_raw(db.get(ChatSession, sid).slots)
    ctx = ToolContext(db=db, chat_session_id=sid, slots=slots, state=state)
    out = execute_tool(ctx, "get_contractor_projects", {"name": "Kiến Phong"})
    assert not out.is_error and out.result["projects"] and out.result["contractor"]["name"] == "Kiến Phong"
    out = execute_tool(ctx, "get_contractor_profile", {"name": "Không Tồn Tại XYZ"})
    assert out.is_error and "Không tìm thấy" in out.error
    out = execute_tool(ctx, "get_reference_rates", {"building_type": "nha_pho", "province": "TP. Hồ Chí Minh",
                                                    "work_scope": "xay_tho"})
    assert not out.is_error and out.result["rate"].has_value
    out = execute_tool(ctx, "search_knowledge", {"query": "biệt thự Indochine"})
    assert not out.is_error and "chunks" in out.result
    out = execute_tool(ctx, "get_contractor_overview", {})
    assert out.is_error  # chưa có khu vực/loại công trình


@pytest.mark.db
def test_tools_listed_in_registry_are_executable(db):
    assert set(tools.TOOLS) == {s.name for s in tool_specs()}


# =============================================================================================
# Bổ sung sau review
# =============================================================================================
def test_drop_questions_keeps_statements_and_abbreviations():
    d = orchestrator._drop_questions
    assert d("Dạ, em đã ghi nhận. Anh/chị xây ở TP. Hồ Chí Minh phải không? Cảm ơn anh/chị.") == \
        "Dạ, em đã ghi nhận. Cảm ơn anh/chị."
    assert d("Dạ, em ghi nhận nhà phố.\n\nAnh/chị muốn xây mấy tầng ạ?") == "Dạ, em ghi nhận nhà phố."
    assert d("Đơn giá 4,3–4,7 triệu/m².") == "Đơn giá 4,3–4,7 triệu/m²."
    assert d("Anh/chị ở đâu?") == ""


@pytest.mark.db
def test_llm_question_dropped_on_collect_turn(db):
    """Lượt thu thập: câu hỏi do code dựng là câu hỏi duy nhất — LLM hỏi lại slot đã có thì bị bỏ."""
    sid = _new(db)
    p = ScriptedProvider([], default=LLMResponse(text="Dạ, em ghi nhận nhà phố. Anh/chị muốn xây loại công trình gì ạ?"))
    r = _say(db, sid, "Nhà phố", provider=p)
    assert "loại công trình gì" not in r.text and "Dạ, em ghi nhận nhà phố." in r.text
    assert r.text.count("?") == 1 and "khu vực" in r.text


@pytest.mark.db
def test_question_after_estimate_does_not_overwrite_profile(db):
    """'Nhà thầu nào thi công biệt thự tốt?' / 'đơn giá hoàn thiện bao nhiêu?' là câu hỏi, không phải sửa thông tin:
    giữ loại công trình/phạm vi, giữ khái toán đã xác nhận."""
    sid = _new(db)
    _say(db, sid, FULL)
    _say(db, sid, CONFIRM)
    _, st1 = split_raw(db.get(ChatSession, sid).slots)
    for q in ("Nhà thầu nào thi công biệt thự tốt tại TP.HCM?", "đơn giá hoàn thiện ở HCM là bao nhiêu?"):
        r = _say(db, sid, q)
        assert r.slots["building_type"] == "nha_pho" and r.slots["work_scope"] == "xay_tho"
        assert r.slots["confirmed"] is True and "summary_confirm" not in _types(r)
        assert "Em tóm tắt lại" not in r.text
    _, st2 = split_raw(db.get(ChatSession, sid).slots)
    assert st2.estimate is not None and st2.estimate.id == st1.estimate.id
    # Câu hỏi có ngân sách vẫn được ghi nhận (không ảnh hưởng khái toán).
    r = _say(db, sid, "ngân sách 1,5 tỷ thì chọn nhà thầu nào?")
    assert r.slots["budget_vnd"] == 1_500_000_000 and r.slots["confirmed"] is True
    assert "budget_options" in _types(r)
    # Nói rõ muốn đổi -> vẫn sửa được.
    r = _say(db, sid, "đổi sang biệt thự")
    assert r.slots["building_type"] == "biet_thu" and "summary_confirm" in _types(r)


@pytest.mark.db
def test_edit_without_new_info_asks_what_to_change(db):
    sid = _new(db)
    _say(db, sid, FULL)
    r = _say(db, sid, "Sửa lại")
    assert r.text.startswith(orchestrator.EDIT_PROMPT) and "Em tóm tắt lại" in r.text
    assert _types(r) == ["summary_confirm"]


@pytest.mark.db
def test_estimate_disclaimer_added_when_llm_mentions_quote_without_disclaimer(db):
    sid = _new(db)
    _say(db, sid, FULL)
    p = ScriptedProvider([], default=LLMResponse(text="Dạ, anh/chị có thể gửi yêu cầu báo giá cho nhà thầu."))
    r = _say(db, sid, CONFIRM, provider=p)
    assert "không phải báo giá" in r.text
