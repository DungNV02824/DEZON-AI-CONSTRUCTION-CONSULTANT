"""Mô phỏng tiêu chí nghiệm thu mục 13 với MockProvider — qua orchestrator và qua HTTP/SSE."""
import json
import re
import uuid

import pytest
from sqlalchemy import func, select

from app.ai import orchestrator, validator
from app.ai.dialog import next_question
from app.ai.providers.mock import MockProvider
from app.ai.state import split_raw
from app.ai.tools import ToolContext, execute_tool
from app.db.models import ChatSession, EstimateSession, GuardrailViolation, QuoteRequest, RecommendationLog
from app.formatting import NO_DATA, format_vnd_range
from app.schemas.slots import Slots
from app.services import chat_service
from tests.wizard import question, step

pytestmark = pytest.mark.db

# Câu đầy đủ kèm chi tiết (diện tích xây dựng, hầm/lửng, mái) -> đi thẳng tới tóm tắt.
DETAILS = ", xây kín đất, không hầm, không lửng, mái bằng"
FULL = "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô ngân sách 2 tỷ" + DETAILS
CONFIRM = "Đúng rồi, tính khái toán giúp tôi"


def _new(db) -> uuid.UUID:
    return chat_service.create_session(db).session_id


def _say(db, sid, msg, provider=None) -> orchestrator.TurnResult:
    return orchestrator.handle_message(db, sid, msg, provider=provider or MockProvider())


def _types(r) -> list[str]:
    return [b.type for b in r.ui_blocks]


def _block(r, t):
    return next(b for b in r.ui_blocks if b.type == t)


def _user_texts(db, sid) -> list[str]:
    return [m.content for m in chat_service.get_session_view(db, sid).messages if m.role == "user"]


def _confirmed_session(db, msg=FULL):
    sid = _new(db)
    _say(db, sid, msg)
    return sid, _say(db, sid, CONFIRM)


# ---------------------------------------------------------------------------------------------
# 1. Một câu đầy đủ -> không hỏi lại, chỉ xác nhận
# ---------------------------------------------------------------------------------------------
def test_1_full_sentence_goes_straight_to_summary(db):
    sid = _new(db)
    r = _say(db, sid, FULL)
    assert r.missing_required == []
    # Không hỏi lại thông tin đã nói, không hỏi câu tùy chọn -> bản xác nhận ngay.
    assert r.stage == "estimate"
    assert _types(r) == ["summary_confirm"]  # không có câu hỏi slot
    assert "Em tóm tắt lại" in r.text and "xác nhận" in r.text
    for q in ("loại công trình nào", "khu vực nào", "diện tích đất", "mấy tầng", "phạm vi nào"):
        assert q not in r.text
    summary = _block(r, "summary_confirm")
    values = {i.label: i.value for i in summary.items}
    assert values["Loại công trình"] == "Nhà phố" and "TP. Thủ Đức" in values["Khu vực"]
    assert values["Ngân sách"] == "2 tỷ" and values["Phạm vi"] == "Xây thô"
    assert r.slots["budget_vnd"] == 2_000_000_000 and "_state" not in r.slots
    assert values["Diện tích xây dựng mỗi tầng"] == "100 m²" and values["Mái"] == "Mái bê tông cốt thép"
    assert values["Tầng hầm"] == "Không" and values["Tầng lửng"] == "Không"
    assert "Mức hoàn thiện" not in values and "mức hoàn thiện" not in r.text.lower()


def test_1b_core_sentence_goes_to_summary_with_disclosed_defaults(db):
    """Đủ 5 thông tin cốt lõi nhưng chưa nói chi tiết -> bản xác nhận ngay, mặc định nêu rõ ở "Giả định"; "Cung cấp
    thêm thông tin" -> hỏi diện tích xây dựng, hầm/lửng, mái, độ sâu hầm; khách "chưa biết" -> dùng mặc định."""
    sid = _new(db)
    r = _say(db, sid, "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu trọn gói")
    assert r.stage == "estimate" and _types(r) == ["summary_confirm"] and r.missing_required == []
    summary = _block(r, "summary_confirm")
    assert summary.assumptions[0] == ("Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng, mái bê tông cốt "
                                      "thép — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.")

    r = _say(db, sid, summary.more_value)
    assert question(r) == "Diện tích xây dựng mỗi tầng (m²)?" and "?" not in r.text
    assert [o.label for o in step(r).options] == ["Xây kín đất (100 m²)", "Chưa biết"]

    r = _say(db, sid, "80 m2")
    assert r.slots["building_area_m2"] == 80 and r.slots["land_area_m2"] == 100
    assert question(r) == "Nhà có tầng hầm hoặc tầng lửng không?"

    r = _say(db, sid, "Có tầng hầm, không lửng")
    assert r.slots["basement"] is True and r.slots["has_mezzanine"] is False
    assert question(r) == "Loại mái dự kiến?"

    r = _say(db, sid, "Chưa biết")
    assert r.slots["roof_type"] is None and question(r) == "Độ sâu tầng hầm?"
    assert [o.label for o in step(r).options] == ["Dưới 1,3 m", "1,3–1,7 m", "1,7–2 m", "Trên 2 m", "Chưa biết"]

    r = _say(db, sid, "Chưa biết")
    assert r.missing_required == [] and r.stage == "estimate" and _types(r) == ["summary_confirm"]
    summary = _block(r, "summary_confirm")
    values = {i.label: i.value for i in summary.items}
    assert values["Diện tích xây dựng mỗi tầng"] == "80 m²" and values["Tầng hầm"] == "Có"
    assert values["Mái"] == "Mái bê tông cốt thép (tạm tính)" and "Mức hoàn thiện" not in values
    assert summary.assumptions == [
        "Tạm tính: hầm sâu 1,7–2 m, mái bê tông cốt thép — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.",
        "Móng tính theo thông lệ: móng cọc (nhà từ 4 tầng nổi trở lên), chưa gồm chi phí ép cọc.",
    ]
    r = _say(db, sid, CONFIRM)
    table = _block(r, "estimate_table").data
    assert table.input.building_area_m2 == 80 and table.input.basement is True


# ---------------------------------------------------------------------------------------------
# 2. Trả lời từng phần -> hỏi lần lượt đúng slot thiếu, có quick replies
# ---------------------------------------------------------------------------------------------
def test_2_step_by_step_asks_only_missing_slot(db):
    sid = _new(db)
    steps = [
        ("Nhà phố", ["province", "district"], "TP. Hồ Chí Minh", 2),
        ("TP. Hồ Chí Minh", ["land_area_m2"], "5 × 10", 3),
        ("Đất 5×20 m", ["floors"], "1 trệt 3 lầu", 4),
        ("1 trệt 3 lầu", ["work_scope"], "Xây thô", 5),
    ]
    asked_before: list[str] = []
    for msg, expected_slots, reply_label, step_no in steps:
        r = _say(db, sid, msg)
        assert r.stage == "collect"
        q = next_question(Slots.model_validate(r.slots))
        assert q is not None and q.slots == expected_slots
        assert question(r) == q.title and q.text not in r.text  # câu hỏi nằm trên thẻ, không lặp trong văn bản
        for prev in asked_before:
            assert prev not in r.text  # không hỏi lại slot đã có
        card = step(r)
        assert reply_label in [o.label for o in [*card.chips, *card.options]]
        assert card.slots == expected_slots and (card.step, card.total) == (step_no, 5) and card.back_value
        assert "summary_confirm" not in _types(r)
        _, state = split_raw(db.get(ChatSession, sid).slots)
        assert state.expected_slots == expected_slots
        asked_before.append(q.text)
    r = _say(db, sid, "Xây thô")
    assert _types(r) == ["summary_confirm"] and r.missing_required == []
    assert _block(r, "summary_confirm").assumptions == [
        "Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng, mái bê tông cốt thép — anh/chị bấm Sửa hoặc "
        "Cung cấp thêm thông tin để thay đổi.",
        "Móng tính theo thông lệ: móng cọc (nhà từ 4 tầng nổi trở lên), chưa gồm chi phí ép cọc."]


# ---------------------------------------------------------------------------------------------
# 3. Xác nhận -> estimate_table; số tiền khớp engine 100%
# ---------------------------------------------------------------------------------------------
def test_3_confirm_produces_estimate_matching_engine(db):
    sid, r = _confirmed_session(db)
    assert "calculate_estimate" in r.tool_calls  # orchestrator tự gọi, không phụ thuộc LLM
    table = _block(r, "estimate_table").data
    assert table.items and table.assumptions and table.disclaimer
    assert table.rate.source is not None and table.rate.source.updated_at is not None
    assert table.cost.display == format_vnd_range(table.cost_min, table.cost_max)
    assert table.cost.display in r.text
    assert "không phải báo giá" in r.text
    res = validator.check(r.text, r.tool_results, _user_texts(db, sid))
    assert res.ok, res.violations
    money = [c for c in validator.extract_claims(r.text) if c.kind in (validator.MONEY, validator.UNIT_PRICE)]
    assert money  # có nêu số tiền
    row = db.get(EstimateSession, table.estimate_session_id)
    assert row is not None and row.chat_session_id == sid
    assert int(row.cost_min) == table.cost_min and int(row.cost_max) == table.cost_max
    # Tóm tắt -> xác nhận -> đã có khái toán: gửi lại "đúng rồi" không tính lại.
    again = _say(db, sid, "Đúng rồi")
    assert "calculate_estimate" not in again.tool_calls


# ---------------------------------------------------------------------------------------------
# 4. Ngân sách thấp hơn khái toán -> ≥ 2 phương án, chi phí do engine tính
# ---------------------------------------------------------------------------------------------
def test_4_low_budget_gives_budget_options(db):
    sid, r = _confirmed_session(db, "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô" + DETAILS)
    assert "budget_options" not in _types(r)
    est = _block(r, "estimate_table").data
    r2 = _say(db, sid, "Ngân sách 1,2 tỷ")
    assert r2.slots["budget_vnd"] == 1_200_000_000 and r2.slots["confirmed"] is True
    assert "calculate_estimate" not in r2.tool_calls  # đổi ngân sách không bắt tính lại
    data = _block(r2, "budget_options").data
    assert data.estimate_session_id == est.estimate_session_id
    assert data.target_budget.display == "1,2 tỷ"
    assert len(data.options) >= 2
    for o in data.options:
        assert o.cost_min is not None and o.cost.display == format_vnd_range(o.cost_min, o.cost_max)
        assert o.cost_min < est.cost_min
    assert validator.check(r2.text, r2.tool_results, _user_texts(db, sid)).ok
    if not any(o.fits_budget for o in data.options):
        assert "chưa có phương án nào" in r2.text
    # Có ngân sách mới -> matching chạy lại với ngân sách.
    assert _block(r2, "contractor_list").data.criteria.budget_vnd == 1_200_000_000


def test_4b_budget_in_first_message_below_cost(db):
    sid = _new(db)
    _say(db, sid, "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô ngân sách 1,2 tỷ" + DETAILS)
    r = _say(db, sid, CONFIRM)
    assert {"estimate_table", "budget_options", "contractor_list"} <= set(_types(r))
    # Đã có ngân sách -> không hỏi lại ngân sách, gợi ý bước tiếp theo.
    assert "So sánh các nhà thầu" in [o.label for o in _block(r, "quick_replies").options]
    assert "ngân sách dự kiến" not in r.text


# ---------------------------------------------------------------------------------------------
# 5. Matching: 3–5 nhà thầu, ≥ 2 lý do có nguồn, breakdown trong recommendation_logs
# ---------------------------------------------------------------------------------------------
def test_5_matching_contractor_list_with_reasons_and_log(db):
    sid, r = _confirmed_session(db)
    data = _block(r, "contractor_list").data
    assert 3 <= len(data.results) <= 5
    assert data.criteria.estimate_session_id == _block(r, "estimate_table").data.estimate_session_id
    for m in data.results:
        assert len(m.reasons) >= 2
        for reason in m.reasons:
            ev = m.contractor.evidence.get(reason.evidence)
            assert ev is not None and ev.has_value and ev.source is not None, reason
    log = db.get(RecommendationLog, data.recommendation_log_id)
    assert log is not None and log.chat_session_id == sid and log.estimate_session_id is not None
    assert all(item["breakdown"] for item in log.results)
    _, state = split_raw(db.get(ChatSession, sid).slots)
    assert [c.id for c in state.contractors] == [m.contractor.id for m in data.results]
    assert state.recommendation_log_id == log.id
    assert r.stage == "match"
    badges = _block(r, "source_badges").badges
    assert len({(b.label, b.source.url) for b in badges}) == len(badges)  # khử trùng lặp


# ---------------------------------------------------------------------------------------------
# 6. So sánh -> comparison_table, không ô trống
# ---------------------------------------------------------------------------------------------
def test_6_compare_contractors_no_empty_cells(db):
    sid, _ = _confirmed_session(db)
    r = _say(db, sid, "so sánh các nhà thầu")
    table = _block(r, "comparison_table").data
    assert 3 <= len(table.contractors) <= 5
    has_missing = False
    for row in table.rows:
        assert len(row.cells) == len(table.contractors)
        for cell in row.cells:
            assert cell.display and cell.display.strip()
            if cell.status == "unavailable":
                has_missing = True
                assert cell.display == NO_DATA
    assert has_missing  # seed có nhà thầu thiếu dữ liệu
    assert r.stage == "present"
    assert validator.check(r.text, r.tool_results, _user_texts(db, sid)).ok


# ---------------------------------------------------------------------------------------------
# 7. Đối kháng
# ---------------------------------------------------------------------------------------------
def test_7a_price_of_contractor_without_price(db):
    sid, _ = _confirmed_session(db)
    r = _say(db, sid, "đơn giá của Huy Hoàng là bao nhiêu?")
    assert "get_contractor_profile" in r.tool_calls
    assert NO_DATA in r.text
    assert not [c for c in validator.extract_claims(r.text) if c.kind == validator.UNIT_PRICE]
    detail = _block(r, "contractor_detail")
    assert detail.data.card.name == "Huy Hoàng" and detail.estimate_session_id is not None


def test_7a_price_question_in_fresh_session(db):
    sid = _new(db)
    r = _say(db, sid, "đơn giá của Huy Hoàng là bao nhiêu?")
    assert NO_DATA in r.text
    assert not [c for c in validator.extract_claims(r.text) if c.kind == validator.UNIT_PRICE]
    assert "question_step" in _types(r)  # vẫn tiếp tục hỏi slot còn thiếu


def test_7b_best_contractor_question(db):
    sid, _ = _confirmed_session(db)
    r = _say(db, sid, "nhà thầu nào tốt nhất?")
    res = validator.check(r.text, r.tool_results, _user_texts(db, sid))
    assert not [v for v in res.violations if v.rule == validator.RULE_BEST]
    assert res.ok, res.violations
    assert "tốt nhất" not in r.text and "phù hợp" in r.text


def test_7c_free_question_overview_with_links(db):
    sid = _new(db)
    r = _say(db, sid, "Nhà thầu nào thi công biệt thự tốt tại TP.HCM?")
    assert "get_contractor_overview" in r.tool_calls
    assert "tốt nhất" not in r.text and "phù hợp" in r.text
    # C-F4: link hồ sơ/công trình nằm trong block có cấu trúc (bảng tổng quan), không dán URL thô vào câu.
    table = _block(r, "comparison_table").data
    assert all(c.profile_url.startswith("https://dezon.vn/") for c in table.contractors)
    projects_row = next(row for row in table.rows if row.key == "projects_of_type")
    assert any(c.source and str(c.source.url or "").startswith("https://dezon.vn/") for c in projects_row.cells)
    assert "https://" not in r.text
    assert validator.check(r.text, r.tool_results, _user_texts(db, sid)).ok


# ---------------------------------------------------------------------------------------------
# 8. Provider "nói dối" -> validator chặn, sinh lại 1 lần, vẫn sai -> dự phòng; có guardrail_violations
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("lie", [
    "Dạ, Kiến Phong có đơn giá 5,2 triệu/m² cho xây thô.",
    "Dạ, Kiến Phong đã có 15 công trình ghi nhận trên Dezon.",
])
def test_8_lying_provider_is_blocked(db, lie):
    sid, _ = _confirmed_session(db)
    liar = MockProvider(lie_text=lie)
    before = db.scalar(select(func.count()).select_from(GuardrailViolation)
                       .where(GuardrailViolation.session_id == sid))
    r = _say(db, sid, "cho em xem hồ sơ Kiến Phong", provider=liar)
    assert r.used_fallback
    assert "5,2" not in r.text and "15 công trình" not in r.text
    retries = [c["retry"] for c in liar.calls]
    assert retries[-2:] == [False, True] and sum(retries) == 1  # sinh lại đúng 1 lần
    rows = db.scalars(select(GuardrailViolation).where(GuardrailViolation.session_id == sid)).all()
    assert len(rows) - before >= 2
    assert {row.detail["attempt"] for row in rows} == {1, 2}
    assert all(row.rule == validator.RULE_UNTRACEABLE for row in rows)
    assert validator.check(r.text, r.tool_results, _user_texts(db, sid)).ok
    assert "contractor_detail" in _types(r)  # UI block vẫn dựng từ tool, không phụ thuộc văn bản


def test_8b_best_claim_from_llm_is_blocked(db):
    sid, _ = _confirmed_session(db)
    r = _say(db, sid, "so sánh các nhà thầu",
             provider=MockProvider(lie_text="Kiến Phong là nhà thầu tốt nhất khu vực."))
    assert r.used_fallback and "tốt nhất" not in r.text
    rules = set(db.scalars(select(GuardrailViolation.rule).where(GuardrailViolation.session_id == sid)).all())
    assert validator.RULE_BEST in rules


# ---------------------------------------------------------------------------------------------
# 9. Yêu cầu báo giá -> quote_requests có estimate_session_id
# ---------------------------------------------------------------------------------------------
def test_9_create_quote_request_tool_saves_estimate_id(db):
    sid, r = _confirmed_session(db)
    est_id = _block(r, "estimate_table").data.estimate_session_id
    sess = db.get(ChatSession, sid)
    slots, state = split_raw(sess.slots)
    ctx = ToolContext(db=db, chat_session_id=sid, slots=slots, state=state,
                      user_texts=["Tôi là Trần Thị Bình, số 0987 654 321"])
    out = execute_tool(ctx, "create_quote_request", {"customer_name": "Trần Thị Bình",
                                                     "customer_phone": "0987 654 321",
                                                     "contractor_ids": [state.contractors[0].id]})
    assert not out.is_error, out.error
    rows = db.scalars(select(QuoteRequest).where(QuoteRequest.id.in_(out.result.ids))).all()
    assert len(rows) == 1 and rows[0].estimate_session_id == est_id and rows[0].customer_phone == "0987654321"


def test_9b_quote_via_chat(db):
    sid, r = _confirmed_session(db)
    est_id = _block(r, "estimate_table").data.estimate_session_id
    form_turn = _say(db, sid, "Tôi muốn yêu cầu báo giá")
    form = _block(form_turn, "quote_request_form")
    assert form.estimate_session_id == est_id and len(form.contractors) >= 1
    r2 = _say(db, sid, "Tôi muốn yêu cầu báo giá, tên tôi là Nguyễn Văn An, số điện thoại 0912 345 678")
    assert "create_quote_request" in r2.tool_calls
    rows = db.scalars(select(QuoteRequest).where(QuoteRequest.customer_phone == "0912345678")).all()
    assert rows and all(q.estimate_session_id == est_id for q in rows)
    assert re.search(r"DZ-[0-9A-F]{6}", r2.text)
    assert validator.check(r2.text, r2.tool_results, _user_texts(db, sid)).ok


def test_9c_quote_tool_rejects_fabricated_contact(db):
    sid, _ = _confirmed_session(db)
    slots, state = split_raw(db.get(ChatSession, sid).slots)
    ctx = ToolContext(db=db, chat_session_id=sid, slots=slots, state=state, user_texts=["Tôi muốn báo giá"])
    out = execute_tool(ctx, "create_quote_request", {"customer_name": "Lê Văn C", "customer_phone": "0912345678"})
    assert out.is_error and "Số điện thoại" in out.error


# ---------------------------------------------------------------------------------------------
# 10. SSE qua HTTP
# ---------------------------------------------------------------------------------------------
def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for chunk in body.split("\n\n"):
        if not chunk.strip():
            continue
        name = data = None
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        events.append((name, data))
    return events


@pytest.fixture
def api(client):
    from app.api.chat import get_chat_provider
    from app.main import app

    app.dependency_overrides[chat_service.get_rate_limiter] = lambda: _LIMITER
    app.dependency_overrides[get_chat_provider] = lambda: MockProvider()
    _LIMITER.__init__(20)
    yield client


_LIMITER = chat_service.MemoryRateLimiter(20)


def test_10_sse_full_flow_over_http(api):
    created = api.post("/api/chat/sessions", json={}).json()
    sid = created["session_id"]
    assert created["messages"][0]["ui_blocks"][0]["type"] == "question_step"

    resp = api.post(f"/api/chat/sessions/{sid}/messages", json={"content": FULL})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache" and resp.headers["x-accel-buffering"] == "no"
    events = _parse_sse(resp.text)
    names = [n for n, _ in events]
    assert names[-1] == "done" and "error" not in names
    assert names == sorted(names, key=lambda n: {"text": 0, "ui_block": 1, "done": 2}[n])  # text… ui_block… done
    assert names.count("text") >= 2  # văn bản chia nhiều mảnh
    assert [d["block"]["type"] for n, d in events if n == "ui_block"] == ["summary_confirm"]
    done = events[-1][1]
    assert done["stage"] == "estimate" and done["missing_required"] == [] and "_state" not in done["slots"]

    resp = api.post(f"/api/chat/sessions/{sid}/messages", json={"content": CONFIRM})
    events = _parse_sse(resp.text)
    text = "".join(d["delta"] for n, d in events if n == "text")
    blocks = [d["block"] for n, d in events if n == "ui_block"]
    assert [b["type"] for b in blocks][:2] == ["estimate_table", "contractor_list"]
    est = blocks[0]["data"]
    assert est["cost"]["display"] in text and est["rate"]["source"]["updated_at"]

    hist = api.get(f"/api/chat/sessions/{sid}").json()
    assert hist["session_id"] == sid and hist["stage"] == "match"
    assert [m["role"] for m in hist["messages"]] == ["assistant", *(["user", "assistant"] * 2)]
    assert hist["messages"][-1]["content"] == text
    assert [b["type"] for b in hist["messages"][-1]["ui_blocks"]] == [b["type"] for b in blocks]
    assert events[-1][1]["message_id"] == hist["messages"][-1]["id"]
    assert "_state" not in hist["slots"]


def test_10_rate_limit_21_messages_per_minute(api):
    sid = api.post("/api/chat/sessions", json={}).json()["session_id"]
    codes = [api.post(f"/api/chat/sessions/{sid}/messages", json={"content": "Nhà phố"}).status_code
             for _ in range(21)]
    assert codes[:20] == [200] * 20
    assert codes[20] == 429
    last = api.post(f"/api/chat/sessions/{sid}/messages", json={"content": "Nhà phố"})
    assert last.status_code == 429 and "quá nhanh" in last.json()["detail"] and last.headers["retry-after"]


def test_10_unknown_session_404(api):
    r = api.post(f"/api/chat/sessions/{uuid.uuid4()}/messages", json={"content": "xin chào"})
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert "phiên" in r.json()["detail"]
    assert api.get(f"/api/chat/sessions/{uuid.uuid4()}").status_code == 404
    assert api.get("/api/chat/sessions/khong-phai-uuid").status_code == 404
