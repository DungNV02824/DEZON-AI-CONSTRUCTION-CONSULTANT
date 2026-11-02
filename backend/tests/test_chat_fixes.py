"""Hồi quy nghiệm thu MVP — fixer chat (C-F1…C-F15, W-F1, W-F7, W-F9, K4). Luồng thật qua orchestrator + MockProvider."""
import uuid

import pytest
from sqlalchemy import select

from app.ai import dialog, orchestrator
from app.ai.normalize import detect_intent, parse_message
from app.ai.providers.mock import MockProvider
from app.ai.state import split_raw
from app.db.models import ChatSession, QuoteRequest
from app.formatting import NO_DATA
from app.schemas.slots import Slots
from app.services import chat_service

PROFILE = "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô"
CONFIRM = "Đúng rồi"


def _new(db) -> uuid.UUID:
    return chat_service.create_session(db).session_id


def _say(db, sid, msg) -> orchestrator.TurnResult:
    return orchestrator.handle_message(db, sid, msg, provider=MockProvider())


def _types(r) -> list[str]:
    return [b.type for b in r.ui_blocks]


def _block(r, t):
    return next(b for b in r.ui_blocks if b.type == t)


def _matched(db, budget: str | None = None):
    sid = _new(db)
    _say(db, sid, PROFILE)
    r = _say(db, sid, CONFIRM)
    if budget:
        r = _say(db, sid, budget)
    return sid, r


# =============================================================================================
# Thuần
# =============================================================================================
def test_wf1_parse_remaining_floors_wins_over_relative_change():
    assert parse_message("Tôi chọn phương án: Giảm 1 tầng (còn 3 tầng)")["floors"] == 3
    assert parse_message("Kết hợp: Giảm 1 tầng (còn 3 tầng) + hạ mức chất lượng xuống tiết kiệm")["floors"] == 3
    assert "floors" not in parse_message("bớt 1 tầng giúp tôi")
    assert parse_message("1 trệt 3 lầu")["floors"] == 4


def test_wf1_chosen_option_matches_label_exactly_before_partial():
    opts = [{"code": "reduce_floor", "label_vi": "Giảm 1 tầng (còn 3 tầng)", "changes": {"floors": 3}},
            {"code": "combined", "label_vi": "Kết hợp: Giảm 1 tầng (còn 3 tầng) + hạ mức chất lượng xuống tiết kiệm",
             "changes": {"floors": 3, "quality_tier": "tiet_kiem"}}]
    pick = orchestrator.chosen_budget_option
    assert pick("Tôi chọn phương án: Giảm 1 tầng (còn 3 tầng)", opts)["code"] == "reduce_floor"
    assert pick(f"Tôi chọn phương án: {opts[1]['label_vi']}", opts)["code"] == "combined"
    assert pick("tôi muốn xây 3 tầng", opts) is None
    upd = orchestrator.apply_budget_option(Slots(floors=4, confirmed=True), opts[1])
    assert upd.slots.floors == 3 and str(upd.slots.quality_tier) == "tiet_kiem" and upd.slots.confirmed
    assert sorted(upd.changed) == ["floors", "quality_tier"]


def test_cf9_edit_field_replies_are_understood():
    for qr in dialog.EDIT_FIELD_REPLIES:
        found = parse_message(qr.value)
        assert found == {}, (qr.value, found)
        expected = "confirm" if qr.label == "Giữ nguyên" else "edit"
        assert detect_intent(qr.value) == expected
        if expected == "edit":
            assert dialog.edit_field_question(qr.value) is not None, qr.value
    q = dialog.edit_field_question("Tôi muốn sửa số tầng")
    assert q.slots == ["floors"] and q.quick_replies.options == dialog.FLOORS_REPLIES
    assert dialog.edit_field_question("Tôi muốn sửa lại thông tin") is None


def test_cf11_no_match_replies_skip_current_province():
    labels = [r.label for r in dialog.no_match_replies("Đà Nẵng")]
    assert labels == ["Tìm nhà thầu tại TP. Hồ Chí Minh", "Tìm nhà thầu tại Hà Nội", "Sửa thông tin công trình"]


# =============================================================================================
# Luồng chat (DB)
# =============================================================================================
@pytest.mark.db
def test_cf1_cf2_quote_only_to_named_contractor_and_name_without_la(db):
    sid, r = _matched(db)
    names = [c.name for c in split_raw(db.get(ChatSession, sid).slots)[1].contractors]
    assert len(names) >= 2 and "Kiến Phong" in names
    r = _say(db, sid, "tôi muốn nhận báo giá từ Kiến Phong, tên Nguyễn Văn A, sđt 0912345678")
    assert "create_quote_request" in r.tool_calls
    rows = db.scalars(select(QuoteRequest).where(QuoteRequest.customer_phone == "0912345678")).all()
    assert len(rows) == 1 and rows[0].customer_name == "Nguyễn Văn A"
    assert "Kiến Phong" in r.text and "DZ-" in r.text
    assert "quote_request_form" not in _types(r)


@pytest.mark.db
def test_cf3_hypothetical_estimate_does_not_change_slots(db):
    sid, r = _matched(db)
    base = _block(r, "estimate_table").data
    r = _say(db, sid, "nếu tôi chỉ xây 3 tầng thì hết bao nhiêu?")
    assert "calculate_estimate" in r.tool_calls and "search_knowledge" not in r.tool_calls
    hyp = _block(r, "estimate_table").data
    assert hyp.input.floors == 3 and hyp.cost_min < base.cost_min
    assert hyp.cost.display in r.text and "không phải báo giá" in r.text
    assert r.slots["floors"] == 4 and r.slots["confirmed"] is True
    _, state = split_raw(db.get(ChatSession, sid).slots)
    assert state.estimate.id == str(base.estimate_session_id)  # khái toán chính giữ nguyên


@pytest.mark.db
def test_cf3_out_of_engine_cost_says_no_data_and_not_included(db):
    sid, _ = _matched(db)
    r = _say(db, sid, "ép cọc hết bao nhiêu tiền?")
    assert r.tool_calls == [] and NO_DATA in r.text and "chưa gồm" in r.text and "ép cọc" in r.text
    assert "mô tả" not in r.text


@pytest.mark.db
def test_cf3_unrelated_knowledge_question_not_answered_with_random_chunks(db):
    sid, _ = _matched(db)
    r = _say(db, sid, "thời hạn giấy phép lái xe máy là bao lâu vậy?")
    assert "Indochine" not in r.text and "em tìm thấy một số thông tin" not in r.text


@pytest.mark.db
def test_cf4_overview_has_structured_block_and_names(db):
    sid = _new(db)
    r = _say(db, sid, "Nhà thầu nào thi công nhà phố tốt tại TP.HCM?")
    assert "get_contractor_overview" in r.tool_calls
    table = _block(r, "comparison_table").data
    assert table.rows[0].key == "projects_of_type" and len(table.contractors) >= 2
    assert all(len(row.cells) == len(table.contractors) for row in table.rows)
    assert "nguồn:" not in r.text and "http" not in r.text and "tốt nhất" not in r.text


@pytest.mark.db
def test_cf5_project_count_with_note_and_detail_block(db):
    sid = _new(db)
    r = _say(db, sid, "Kiến Phong có bao nhiêu công trình?")
    assert "get_contractor_projects" in r.tool_calls
    assert "không đồng nghĩa với số công trình đã hoàn thành" in r.text and "trang dự án" in r.text
    assert "contractor_detail" in _types(r) and "http" not in r.text


@pytest.mark.db
def test_cf6_rate_question_during_collect_does_not_fill_scope(db):
    sid = _new(db)
    _say(db, sid, "Công trình khác")
    r = _say(db, sid, "đơn giá xây thô ở HCM bao nhiêu?")
    assert "work_scope" in r.missing_required and r.slots["work_scope"] is None
    assert ": Chưa có dữ liệu." not in r.text


@pytest.mark.db
def test_cf7_profile_question_answers_directly(db):
    sid, _ = _matched(db)
    r = _say(db, sid, "Huy Hoàng bảo hành 5 năm phải không?")
    assert "contractor_detail" in _types(r)
    assert r.text.count("\n") <= 1 and len(r.text) < 400
    r = _say(db, sid, "tìm nhà thầu khác giúp tôi")
    assert "chỉ có" in r.text or "vẫn là" in r.text


@pytest.mark.db
def test_cf8_slot_question_single_da(db):
    sid = _new(db)
    r = _say(db, sid, "Tôi muốn xây nhà phố")
    assert r.text.count("Dạ") == 1 and "đã ghi nhận" not in r.text


@pytest.mark.db
def test_cf11_no_contractor_suggests_next_steps(db):
    sid = _new(db)
    for msg in ("Nhà phố", "Đà Nẵng", "Đất 5×20 m", "1 trệt 2 lầu", "Hoàn thiện"):
        _say(db, sid, msg)
    r = _say(db, sid, CONFIRM)
    assert "contractor_list" not in _types(r)
    assert "đơn giá mặc định toàn quốc" in r.text
    labels = [o.label for o in _block(r, "quick_replies").options]
    assert "Sửa thông tin công trình" in labels and any(lbl.startswith("Tìm nhà thầu tại") for lbl in labels)
    r2 = _say(db, sid, "Tìm nhà thầu tại TP. Hồ Chí Minh")
    assert r2.slots["province"] == "Đà Nẵng" and r2.slots["confirmed"] is True
    assert "contractor_list" in _types(r2)


@pytest.mark.db
def test_cf15_slots_do_not_expose_asked(db):
    sid, r = _matched(db)
    assert "asked" not in r.slots
    assert "asked" not in chat_service.get_session_view(db, sid).slots


@pytest.mark.db
def test_k4_edit_then_confirm_without_change_reshows_estimate_and_contractors(db):
    sid, r1 = _matched(db)
    assert "estimate_table" in _types(r1)
    r2 = _say(db, sid, "Tôi muốn sửa lại thông tin")
    assert _types(r2) == ["quick_replies"]
    r3 = _say(db, sid, "Đúng rồi")
    assert {"estimate_table", "contractor_list"} <= set(_types(r3))
    assert _block(r3, "estimate_table").data.cost.display in r3.text


@pytest.mark.db
def test_cf9_edit_specific_field_asks_that_field(db):
    sid, _ = _matched(db)
    _say(db, sid, "Tôi muốn sửa lại thông tin")
    r = _say(db, sid, "Tôi muốn sửa số tầng")
    assert _types(r) == ["quick_replies"] and "mấy tầng" in r.text
    r = _say(db, sid, "1 trệt 2 lầu")
    assert r.slots["floors"] == 3 and "summary_confirm" in _types(r)


@pytest.mark.db
def test_wf1_wf7_choose_budget_option_applies_changes_and_recalculates(db):
    sid, r = _matched(db, "ngân sách 1,2 tỷ")
    opts = _block(r, "budget_options").data.options
    assert opts[-1].code == "combined"  # W-F9
    reduce = next(o for o in opts if o.code == "reduce_floor")
    r = _say(db, sid, f"Tôi chọn phương án: {reduce.label_vi}")
    assert r.slots["floors"] == 3 and r.slots["confirmed"] is True
    est = _block(r, "estimate_table").data
    assert est.input.floors == 3 and est.cost.display == reduce.cost.display
    assert reduce.label_vi in r.text
    assert "summary_confirm" not in _types(r)


@pytest.mark.db
def test_wf7_estimate_text_is_short_and_budget_question_last(db):
    sid = _new(db)
    _say(db, sid, PROFILE)
    r = _say(db, sid, CONFIRM)
    est = _block(r, "estimate_table").data
    names = [m.contractor.name for m in _block(r, "contractor_list").data.results]
    paragraphs = r.text.split("\n\n")
    assert paragraphs[-1] == orchestrator.BUDGET_TAIL
    assert est.cost.display in r.text and "không phải báo giá" in r.text
    # Không chép lại bảng/thẻ: không liệt kê từng hạng mục hay lý do từng nhà thầu.
    assert all(it.label_vi not in r.text for it in est.items if it.label_vi.startswith("Tầng"))
    assert "Lý do" in r.text and r.text.count("- ") == 0
    assert all(n in r.text for n in names) and len(r.text) < 1300
