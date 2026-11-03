"""Thẻ câu hỏi từng bước (question_step): ĐÚNG 5 câu cốt lõi (bước i/5 cố định), Quay lại / Bỏ qua / Cung cấp thêm
thông tin (câu chi tiết i/N) / Sửa từng dòng trên bản xác nhận; ngân sách hỏi sau khái toán; phong cách / mức đầu tư
không hỏi (chỉ ghi nhận khi khách tự nói)."""
import pytest
from sqlalchemy import select

from app.ai import dialog, orchestrator, slot_filling, tools
from app.ai.dialog import (
    BACK_VALUE,
    CONFIRM_VALUE,
    MORE_VALUE,
    SKIP_VALUE,
    build_summary,
    edit_field_question,
    nav_command,
    next_question,
    planned_keys,
    step_block,
)
from app.ai.providers.mock import MockProvider
from app.ai.state import estimate_slots_hash, split_raw
from app.ai.tools import ToolContext, execute_tool
from app.db.models import ChatSession, Contractor, QuoteRequest
from app.schemas.slots import Slots, investment_note, project_preferences
from app.schemas.ui_blocks import QuestionStepBlock
from app.services import chat_service
from app.services.quote_service import QuoteRequestIn, create_quote_requests, note_with_preferences
from tests.wizard import SKIP_OPTIONAL, question, step

HCM = "TP. Hồ Chí Minh"
FULL = "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô, xây kín đất, không hầm, không lửng, mái bằng"
CORE = dict(building_type="nha_pho", province=HCM, land_width_m=5, land_length_m=20, land_area_m2=100, floors=4,
            work_scope="xay_tho")
DONE_DETAILS = dict(building_area_m2=100, basement=False, has_mezzanine=False, roof_type="roof_concrete")
INVEST_NOTE = "Mức đầu tư phổ thông chưa được tính vào khái toán vì Dezon chưa có đơn giá theo mức đầu tư."
DEFAULTS_TAIL = " — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi."
CORE_TITLES = ["Loại công trình anh/chị dự định xây dựng?", "Khu vực xây dựng?", "Diện tích đất?", "Số tầng dự kiến?",
               "Phạm vi thi công cần khái toán?"]


def _new(db):
    return chat_service.create_session(db).session_id


def _say(db, sid, msg):
    return orchestrator.handle_message(db, sid, msg, provider=MockProvider())


def _types(r):
    return [b.type for b in r.ui_blocks]


def _block(r, t):
    return next(b for b in r.ui_blocks if b.type == t)


# =============================================================================================
# Thuần
# =============================================================================================
@pytest.mark.parametrize("msg,cmd", [
    (BACK_VALUE, "back"), ("quay lại", "back"), ("Quay lại câu trước ạ", "back"), ("trở lại bước trước", "back"),
    (MORE_VALUE, "more"), ("Cung cấp thêm thông tin để báo giá chính xác hơn", "more"),
    ("Quay lại câu trước đi, mà đổi sang biệt thự", None), (SKIP_VALUE, None), ("Nhà phố", None),
    (CONFIRM_VALUE, None),
])
def test_nav_command(msg, cmd):
    assert nav_command(msg) == cmd


def test_planned_keys_and_totals():
    # Phần hỏi: luôn đúng 5 câu cốt lõi, bất kể loại công trình / thông tin đã có.
    assert planned_keys(Slots()) == ["building_type", "province", "land", "floors", "work_scope"]
    assert planned_keys(Slots(**CORE, basement=True, has_mezzanine=True)) == dialog.CORE_KEYS
    # Nhóm câu hỏi thêm ("Cung cấp thêm thông tin"): diện tích xây dựng, hầm/lửng, mái, rồi độ sâu hầm / diện tích lửng.
    assert dialog.detail_keys(Slots(**CORE)) == ["building_area", "basement_mezz", "roof"]
    assert dialog.detail_keys(Slots(**CORE, basement=True, has_mezzanine=True)) == [
        "building_area", "basement_mezz", "roof", "basement_depth", "mezzanine_area"]
    apt = Slots(**{**CORE, "building_type": "can_ho"})
    assert planned_keys(apt) == dialog.CORE_KEYS and dialog.detail_keys(apt) == []  # căn hộ: không hỏi chi tiết


def test_core_cards_numbered_one_to_five():
    s = Slots()
    answers = ["Nhà phố", "Hà Nội", "Đất 5×20 m", "1 trệt 3 lầu", "Xây thô"]
    for i, (title, msg) in enumerate(zip(CORE_TITLES, answers, strict=True), start=1):
        q = next_question(s)
        card = step_block(q, s)
        assert (card.question, card.step, card.total) == (title, i, 5)
        assert card.back_value == (BACK_VALUE if i > 1 else None) and card.skippable is False
        assert len(card.slots) == 1 or card.slots == ["province", "district"]
        s = slot_filling.extract(msg, s, expected_slots=q.slots).slots
    assert next_question(s) is None and dialog.missing_required(s) == []


@pytest.mark.parametrize("known,expected_steps", [
    ({"province": "Hà Nội"}, [1, 3, 4, 5]),
    ({"building_type": "biet_thu", "floors": 3}, [2, 3, 5]),
    ({"building_type": "nha_pho", "province": HCM, "land_area_m2": 100}, [4, 5]),
    ({"work_scope": "tron_goi", "land_area_m2": 80}, [1, 2, 4]),
])
def test_counter_keeps_fixed_numbering_when_slots_known(known, expected_steps):
    """Câu đã có thông tin thì bỏ qua, nhưng số bước vẫn theo vị trí cố định trong 5 câu (không đánh số lại)."""
    s = Slots(**known)
    fill = {"building_type": "Nhà phố", "province": "Hà Nội", "land_area_m2": "Đất 5×20 m",
            "floors": "1 trệt 3 lầu", "work_scope": "Xây thô"}
    steps = []
    while (q := next_question(s)) is not None:
        card = step_block(q, s)
        assert card.total == 5
        steps.append(card.step)
        s = slot_filling.extract(fill[q.slots[0]], s, expected_slots=q.slots).slots
    assert steps == expected_steps


def test_land_card_has_size_chips_and_floor_rows():
    s = Slots(building_type="nha_pho", province=HCM)
    card = step_block(next_question(s), s)
    assert isinstance(card, QuestionStepBlock)
    assert card.question == "Diện tích đất?" and (card.step, card.total) == (3, 5)
    assert [c.label for c in card.chips] == ["4 × 10", "5 × 10", "6 × 10", "6 × 12", "9 × 12", "12 × 10"]
    assert [c.value for c in card.chips][:2] == ["Đất 4×10 m", "Đất 5×10 m"]
    assert card.options == [] and card.slots == ["land_area_m2"]
    assert card.hint == "Chọn kích thước đất ngang × dài (m), hoặc nhập tổng diện tích đất (m²)."
    assert card.back_value == BACK_VALUE and card.skippable is False and card.skip_value is None
    assert card.input_placeholder == "Nhập ngang × dài (m) hoặc tổng m²"
    u = slot_filling.extract(card.chips[1].value, s, expected_slots=card.slots)
    assert (u.slots.land_width_m, u.slots.land_length_m, u.slots.land_area_m2) == (5, 10, 50)
    assert u.slots.floors is None
    # Số tầng là câu riêng 4/5.
    q = next_question(u.slots)
    card = step_block(q, u.slots)
    assert (card.question, card.step, card.total, card.slots) == ("Số tầng dự kiến?", 4, 5, ["floors"])
    assert [o.label for o in card.options] == ["1 trệt", "1 trệt 1 lầu", "1 trệt 2 lầu", "1 trệt 3 lầu", "1 trệt 4 lầu"]
    assert [o.value for o in card.options] == ["1 trệt", "1 trệt 1 lầu", "1 trệt 2 lầu", "1 trệt 3 lầu", "1 trệt 4 lầu"]
    assert card.chips == [] and card.input_placeholder == "Nhập số tầng (vd 1 trệt 3 lầu)"


def test_apartment_area_question():
    s = Slots(building_type="can_ho", province=HCM)
    q = next_question(s)
    assert (q.title, q.slots, q.placeholder) == ("Diện tích căn hộ (m²)?", ["land_area_m2"], "Nhập diện tích (m²)")
    assert step_block(q, s).step == 3 and step_block(q, s).total == 5
    assert [o.label for o in q.options] == ["50 m²", "80 m²", "120 m²", "200 m²", "300 m²"]
    assert q.options[1].value == "Diện tích căn hộ 80 m2"
    assert q.chips == []
    s = slot_filling.extract(q.options[1].value, s, expected_slots=q.slots).slots
    assert s.land_area_m2 == 80 and s.building_type == "can_ho"
    q = next_question(s)
    assert q.title == "Căn hộ có mấy tầng?" and [o.label for o in q.options] == ["1 tầng", "2 tầng (duplex)"]
    assert [o.value for o in q.options] == ["Căn hộ 1 tầng", "Căn hộ 2 tầng (duplex)"]
    assert (step_block(q, s).step, step_block(q, s).total) == (4, 5)
    s = slot_filling.extract(q.options[1].value, s, expected_slots=q.slots).slots
    assert s.floors == 2
    q = next_question(s)
    assert q.slots == ["work_scope"] and step_block(q, s).step == 5
    s = slot_filling.extract("Nội thất", s, expected_slots=q.slots).slots
    assert next_question(s) is None and next_question(s, more=True) is None


def test_optional_questions_never_asked_but_editable():
    s = Slots(**CORE)
    # Đủ 5 câu cốt lõi: không hỏi phong cách / ngân sách / mức đầu tư (kể cả khi bấm "Cung cấp thêm thông tin").
    assert next_question(s) is None
    assert next_question(Slots(**CORE, **DONE_DETAILS), more=True) is None
    # Vẫn sửa được mục khách đã tự nêu: thẻ câu hỏi riêng (1/1), bỏ qua được.
    for key, slot, title in [("design_style", "design_style", "Phong cách thiết kế mong muốn?"),
                             ("budget", "budget_vnd", "Ngân sách dự kiến?"),
                             ("investment", "investment_level", "Mức đầu tư mong muốn?")]:
        q = dialog.question_for_key(key, s)
        card = step_block(q, s)
        assert (card.slots, card.question, card.skippable, card.skip_value) == ([slot], title, True, SKIP_VALUE)
        assert (card.step, card.total, card.back_value) == (1, 1, None)


def test_optional_answers_free_text_and_guards():
    s = Slots(**CORE, **DONE_DETAILS)
    u = slot_filling.extract("Nhà vườn kiểu Nhật", s, expected_slots=["design_style"])
    assert u.slots.design_style == "Nhà vườn kiểu Nhật" and "design_style" in u.slots.asked
    u = slot_filling.extract("Phong cách Indochine", s, expected_slots=["design_style"])
    assert u.slots.design_style == "indochine"
    u = slot_filling.extract("cao cấp", s, expected_slots=["investment_level"])
    assert u.slots.investment_level == "sieu_cao_cap"
    # Câu hỏi khi đang ở thẻ tùy chọn: không tính "đã hỏi" (thẻ hiện lại), không ghi câu hỏi thành phong cách.
    u = slot_filling.extract("phong cách nào hợp nhà phố?", s, expected_slots=["design_style"])
    assert u.slots.design_style is None and "design_style" not in u.slots.asked
    # "ok"/"đúng rồi" ở thẻ tùy chọn không phải xác nhận tính khái toán (bản tóm tắt chưa hiển thị)...
    for msg in ("ok", "Đúng rồi"):
        u = slot_filling.extract(msg, s, expected_slots=["budget_vnd"])
        assert u.slots.confirmed is False and "budget_vnd" in u.slots.asked
    # ... trừ đúng tin nhắn của nút "Tính ngay" trên bản xác nhận.
    assert slot_filling.extract(CONFIRM_VALUE, s, expected_slots=["budget_vnd"]).slots.confirmed is True
    # Không nhận mức đầu tư / phong cách từ câu nói chung chung.
    assert "investment_level" not in slot_filling.extract("tôi đầu tư khá nhiều tiền", s).changed
    assert "design_style" not in slot_filling.extract("nhà hiện đại giá bao nhiêu", s).changed
    assert slot_filling.extract("đổi mức đầu tư sang tiết kiệm", s).slots.investment_level == "tiet_kiem"


def test_summary_block_fields_and_edit_values():
    s = Slots(**CORE, **DONE_DETAILS, design_style="indochine", budget_vnd=4_000_000_000,
              investment_level="pho_thong", asked=["design_style", "budget_vnd", "investment_level"])
    text, block = build_summary(s)
    assert "phong cách Indochine" in text and "ngân sách 4 tỷ" in text and "mức đầu tư phổ thông" in text
    assert block.title == "Xác nhận thông tin — Nhà phố"
    assert block.confirm_label == "Tính ngay → xem bảng khái toán" and block.confirm_value == CONFIRM_VALUE
    assert block.more_label is None and block.more_value is None  # không còn câu nào bỏ qua / chưa biết
    edits = {i.label: i.edit_value for i in block.items}
    assert edits == {
        "Loại công trình": "Tôi muốn sửa loại công trình", "Khu vực": "Tôi muốn sửa khu vực",
        "Diện tích đất": "Tôi muốn sửa diện tích đất", "Số tầng": "Tôi muốn sửa số tầng",
        "Phạm vi": "Tôi muốn sửa phạm vi công việc",
        "Diện tích xây dựng mỗi tầng": "Tôi muốn sửa diện tích xây dựng",
        "Tầng hầm": "Tôi muốn sửa thông tin hầm, lửng của nhà",
        "Tầng lửng": "Tôi muốn sửa thông tin hầm, lửng của nhà", "Mái": "Tôi muốn sửa loại mái",
        "Phong cách": "Tôi muốn sửa phong cách thiết kế", "Ngân sách": "Tôi muốn sửa ngân sách",
        "Mức đầu tư": "Tôi muốn sửa mức đầu tư",
    }
    assert INVEST_NOTE in block.assumptions
    # Mỗi tin nhắn "Sửa" được hiểu là ý muốn sửa (không trích slot) và hỏi lại đúng mục bằng thẻ câu hỏi.
    for item in block.items:
        u = slot_filling.extract(item.edit_value, s)
        assert u.intent == "edit" and u.changed == [], item.edit_value
        q = edit_field_question(item.edit_value, s)
        assert q is not None and q.title, item.edit_value
    assert edit_field_question("Tôi muốn sửa ngân sách", s).slots == ["budget_vnd"]
    assert edit_field_question("Tôi muốn sửa mức đầu tư", s).slots == ["investment_level"]
    assert edit_field_question("Tôi muốn sửa phong cách thiết kế", s).slots == ["design_style"]
    assert edit_field_question("Tôi muốn sửa diện tích đất", s).slots == ["land_area_m2"]  # không hỏi kèm số tầng


def test_summary_more_button_when_details_default_or_unknown():
    s = Slots(**CORE, building_area_m2=100, basement=False, has_mezzanine=False, asked=["roof_type"])
    _, block = build_summary(s)
    assert block.more_label == "Cung cấp thêm thông tin để báo giá chính xác hơn" and block.more_value == MORE_VALUE
    assert dialog.reaskable_slots(s) == ["roof_type"] and dialog.more_slots(s) == ["roof_type"]
    assert block.assumptions[0] == "Tạm tính: mái bê tông cốt thép" + DEFAULTS_TAIL
    # Chưa hỏi chi tiết nào: vẫn có nút (mọi chi tiết đang dùng mặc định).
    _, block = build_summary(Slots(**CORE))
    assert block.more_value == MORE_VALUE
    # Đủ chi tiết -> không còn nút.
    assert build_summary(Slots(**CORE, **DONE_DETAILS))[1].more_value is None


def test_investment_note_and_preferences():
    assert investment_note("pho_thong") == INVEST_NOTE
    assert investment_note("Tầm trung bình khá") == ("Mức đầu tư “Tầm trung bình khá” chưa được tính vào khái toán vì "
                                                     "Dezon chưa có đơn giá theo mức đầu tư.")
    assert investment_note(None) is None
    s = Slots(design_style="hien_dai", investment_level="sieu_cao_cap")
    assert project_preferences(s) == ["Phong cách thiết kế: Hiện đại", "Mức đầu tư mong muốn: Siêu cao cấp"]
    assert note_with_preferences("Gọi giờ hành chính", project_preferences(s)) == (
        "Gọi giờ hành chính\nPhong cách thiết kế: Hiện đại\nMức đầu tư mong muốn: Siêu cao cấp")
    assert note_with_preferences(None, []) is None


def test_estimate_hash_ignores_style_and_keeps_legacy_hash():
    s = Slots(**CORE, **DONE_DETAILS)
    base = estimate_slots_hash(s)
    assert estimate_slots_hash(s.model_copy(update={"design_style": "hien_dai", "budget_vnd": 3_000_000_000})) == base
    # Mức đầu tư nằm trong hash: đổi mức đầu tư -> dựng lại bảng khái toán kèm ghi chú (số liệu không đổi).
    assert estimate_slots_hash(s.model_copy(update={"investment_level": "pho_thong"})) != base


def test_llm_schema_and_tool_params_have_new_fields():
    props = slot_filling.EXTRACTION_JSON_SCHEMA["properties"]
    assert {"design_style", "investment_level"} <= set(props)
    assert {"design_style", "investment_level"} <= set(slot_filling.EXTRACTION_JSON_SCHEMA["required"])
    spec = next(t for t in tools.tool_specs() if t.name == "update_project_requirements")
    assert {"design_style", "investment_level"} <= set(spec.input_schema["properties"])
    # Giá trị LLM: mã chuẩn nhận khi lời khách có nói; chữ tự do phải có trong tin nhắn.
    raw = {"design_style": "hien_dai", "investment_level": "trung_cap"}
    assert slot_filling.normalize_raw_slots(raw, "muốn phong cách hiện đại, đầu tư trung cấp") == raw
    assert slot_filling.normalize_raw_slots(raw, "nhà phố 5x20") == {}
    assert slot_filling.normalize_raw_slots({"design_style": "Wabi-sabi"}, "tôi thích Wabi-sabi") == \
        {"design_style": "Wabi-sabi"}


# =============================================================================================
# Luồng thật (DB)
# =============================================================================================
@pytest.mark.db
def test_wizard_back_keeps_values_and_steps(db):
    sid = _new(db)
    r = _say(db, sid, "Nhà phố")
    assert question(r) == "Khu vực xây dựng?" and (step(r).step, step(r).back_value) == (2, BACK_VALUE)
    assert "?" not in r.text  # văn bản không lặp câu hỏi
    r = _say(db, sid, "Hà Nội")
    assert step(r).step == 3
    r = _say(db, sid, BACK_VALUE)
    assert question(r) == "Khu vực xây dựng?" and r.slots["province"] == "Hà Nội"  # giữ giá trị
    assert r.text == orchestrator.BACK_ACK and step(r).step == 2
    r = _say(db, sid, BACK_VALUE)
    assert question(r) == "Loại công trình anh/chị dự định xây dựng?" and step(r).back_value is None
    r = _say(db, sid, BACK_VALUE)  # đang ở câu đầu -> hiện lại câu đầu
    assert step(r).step == 1 and r.slots["building_type"] == "nha_pho"
    r = _say(db, sid, "Biệt thự")  # trả lời lại câu đã quay về -> đi tiếp tới câu còn thiếu
    assert r.slots["building_type"] == "biet_thu" and r.slots["province"] == "Hà Nội"
    assert question(r) == "Diện tích đất?" and (step(r).step, step(r).total) == (3, 5)


@pytest.mark.db
def test_wizard_skip_more_edit_and_confirm(db):
    sid = _new(db)
    r = _say(db, sid, "Nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô")  # đủ 5 câu cốt lõi -> bản xác nhận ngay
    assert _types(r) == ["summary_confirm"] and r.stage == "estimate"
    summary = _block(r, "summary_confirm")
    assert summary.title == "Xác nhận thông tin — Nhà phố" and summary.more_value == MORE_VALUE
    assert summary.assumptions[0] == ("Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng, mái bê tông cốt "
                                      "thép" + DEFAULTS_TAIL)
    # "Cung cấp thêm thông tin" -> hỏi lần lượt các mục chi tiết (bước i/N của nhóm câu hỏi thêm).
    r = _say(db, sid, summary.more_value)
    assert question(r) == "Diện tích xây dựng mỗi tầng (m²)?" and r.text == orchestrator.MORE_ACK
    assert (step(r).step, step(r).total, step(r).back_value) == (1, 3, None)
    assert [o.label for o in step(r).options] == ["Xây kín đất (100 m²)", "Chưa biết"]
    r = _say(db, sid, "Tôi chưa biết diện tích xây dựng")
    assert question(r) == "Nhà có tầng hầm hoặc tầng lửng không?" and (step(r).step, step(r).total) == (2, 3)
    assert step(r).back_value == BACK_VALUE
    r = _say(db, sid, BACK_VALUE)  # quay lại câu trước trong nhóm câu hỏi thêm
    assert question(r) == "Diện tích xây dựng mỗi tầng (m²)?" and step(r).step == 1
    r = _say(db, sid, "Diện tích xây dựng mỗi tầng 80 m2")
    assert r.slots["building_area_m2"] == 80 and step(r).step == 2
    r = _say(db, sid, "Có tầng hầm, không lửng")
    assert question(r) == "Loại mái dự kiến?" and (step(r).step, step(r).total) == (3, 4)  # + độ sâu hầm
    r = _say(db, sid, "Mái tôn")
    assert question(r) == "Độ sâu tầng hầm?" and (step(r).step, step(r).total) == (4, 4)
    r = _say(db, sid, "Tôi chưa biết độ sâu hầm")
    # Hết câu hỏi thêm -> quay lại bản xác nhận.
    assert _types(r) == ["summary_confirm"]
    summary = _block(r, "summary_confirm")
    rows = {i.label: i for i in summary.items}
    assert rows["Diện tích xây dựng mỗi tầng"].value == "80 m²" and rows["Tầng hầm"].value == "Có"
    assert rows["Tầng lửng"].value == "Không" and rows["Mái"].value == "Mái tôn"
    assert summary.assumptions[0] == "Tạm tính: hầm sâu 1,7–2 m" + DEFAULTS_TAIL
    assert summary.more_value == MORE_VALUE  # độ sâu hầm vẫn dùng mặc định
    _, st = split_raw(db.get(ChatSession, sid).slots)
    assert st.more_mode is False
    # "Sửa" dòng Mái -> chỉ hỏi lại đúng mục đó, trả lời xong quay lại bản xác nhận (không hỏi tiếp độ sâu hầm).
    r = _say(db, sid, rows["Mái"].edit_value)
    assert question(r) == "Loại mái dự kiến?" and r.text == orchestrator.EDIT_ACK
    r = _say(db, sid, "Mái ngói kèo thép")
    summary = _block(r, "summary_confirm")
    assert {i.label: i.value for i in summary.items}["Mái"] == "Mái ngói kèo thép"
    # Tính ngay -> khái toán, rồi hỏi ngân sách (ngân sách chỉ hỏi SAU khái toán).
    r = _say(db, sid, summary.confirm_value)
    assert "estimate_table" in _types(r) and r.text.endswith(orchestrator.BUDGET_TAIL)
    assert "question_step" not in _types(r)
    _, st = split_raw(db.get(ChatSession, sid).slots)
    assert st.current_question is None and st.expected_slots == ["budget_vnd"]


@pytest.mark.db
def test_more_flow_all_unknown_then_summary(db):
    sid = _new(db)
    _say(db, sid, FULL.split(",")[0])  # "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô"
    r = _say(db, sid, MORE_VALUE)
    for expected in ["Diện tích xây dựng mỗi tầng (m²)?", "Nhà có tầng hầm hoặc tầng lửng không?", "Loại mái dự kiến?"]:
        assert question(r) == expected
        r = _say(db, sid, "chưa biết")
    assert _types(r) == ["summary_confirm"]
    summary = _block(r, "summary_confirm")
    assert summary.assumptions[0] == ("Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng, mái bê tông cốt "
                                      "thép" + DEFAULTS_TAIL)
    # Bấm lại "Cung cấp thêm thông tin" -> hỏi lại từ đầu nhóm.
    r = _say(db, sid, MORE_VALUE)
    assert question(r) == "Diện tích xây dựng mỗi tầng (m²)?" and step(r).step == 1


@pytest.mark.db
def test_more_round_counts_only_missing_details(db):
    # Diện tích xây dựng đã có -> lượt hỏi thêm chỉ gồm hầm/lửng + mái: bước 1/2 (không "2/3"), câu đầu không có
    # "Quay lại câu trước" dẫn về câu ngoài lượt.
    sid = _new(db)
    r = _say(db, sid, "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô xây kín đất 100m2")
    assert _types(r) == ["summary_confirm"]
    r = _say(db, sid, MORE_VALUE)
    assert question(r) == "Nhà có tầng hầm hoặc tầng lửng không?"
    assert (step(r).step, step(r).total, step(r).back_value) == (1, 2, None)
    r = _say(db, sid, BACK_VALUE)  # câu đầu của lượt: hiện lại đúng câu đó
    assert question(r) == "Nhà có tầng hầm hoặc tầng lửng không?" and step(r).step == 1
    r = _say(db, sid, "Có tầng hầm và tầng lửng")  # + độ sâu hầm, diện tích lửng ở cuối lượt
    assert question(r) == "Loại mái dự kiến?" and (step(r).step, step(r).total) == (2, 4)
    r = _say(db, sid, "Tôi chưa biết loại mái")
    assert question(r) == "Độ sâu tầng hầm?" and (step(r).step, step(r).total) == (3, 4)
    r = _say(db, sid, "Hầm sâu 1,5 m")
    assert question(r) == "Diện tích tầng lửng (m²)?" and (step(r).step, step(r).total) == (4, 4)
    r = _say(db, sid, "Tầng lửng 50 m2")
    summary = _block(r, "summary_confirm")
    assert summary.assumptions[0] == "Tạm tính: mái bê tông cốt thép" + DEFAULTS_TAIL
    # Lượt hỏi thêm mới: chỉ còn mái (khách đã "chưa biết") -> 1/1.
    r = _say(db, sid, MORE_VALUE)
    assert question(r) == "Loại mái dự kiến?" and (step(r).step, step(r).total, step(r).back_value) == (1, 1, None)
    _, st = split_raw(db.get(ChatSession, sid).slots)
    assert st.more_mode is True and st.more_keys == ["roof"]
    r = _say(db, sid, "Mái tôn")
    assert _types(r) == ["summary_confirm"] and _block(r, "summary_confirm").more_value is None
    _, st = split_raw(db.get(ChatSession, sid).slots)
    assert st.more_mode is False and st.more_keys == []


@pytest.mark.db
def test_edit_detail_row_is_single_step_without_back(db):
    # "Sửa" một dòng chi tiết: chỉ hỏi lại đúng mục đó, bước 1/1 (không mượn số của nhóm câu hỏi thêm, không giống
    # bước i/5 của câu cốt lõi), không có "Quay lại câu trước".
    sid = _new(db)
    r = _say(db, sid, FULL.split(",")[0])
    rows = {i.label: i for i in _block(r, "summary_confirm").items}
    for label, title in [("Mái", "Loại mái dự kiến?"), ("Diện tích xây dựng mỗi tầng", "Diện tích xây dựng mỗi tầng (m²)?")]:
        r = _say(db, sid, rows[label].edit_value)
        assert question(r) == title
        assert (step(r).step, step(r).total, step(r).back_value) == (1, 1, None)
        r = _say(db, sid, BACK_VALUE)  # gõ "quay lại" khi sửa một dòng: hiện lại đúng câu đó
        assert question(r) == title and (step(r).step, step(r).total) == (1, 1)
        r = _say(db, sid, "chưa biết")
        assert _types(r) == ["summary_confirm"]
    r = _say(db, sid, rows["Tầng hầm"].edit_value)
    assert question(r) == "Nhà có tầng hầm hoặc tầng lửng không?" and (step(r).step, step(r).total) == (1, 1)
    r = _say(db, sid, "có")  # "có" chung chung -> hỏi rõ, vẫn 1/1
    assert question(r) == "Nhà có tầng hầm, tầng lửng hay cả hai?" and (step(r).step, step(r).total) == (1, 1)
    r = _say(db, sid, "Có tầng hầm, không lửng")
    summary = _block(r, "summary_confirm")  # sửa một dòng: không hỏi tiếp độ sâu hầm, nêu mặc định
    assert summary.assumptions[0] == ("Tạm tính: xây kín đất (100 m²/tầng), hầm sâu 1,7–2 m, mái bê tông cốt thép"
                                      + DEFAULTS_TAIL)
    # Lượt hỏi thêm: diện tích xây dựng, mái, độ sâu hầm -> i/3 (như mock frontend); "ok" = giữ mặc định, không xác nhận.
    r = _say(db, sid, MORE_VALUE)
    assert question(r) == "Diện tích xây dựng mỗi tầng (m²)?" and (step(r).step, step(r).total) == (1, 3)
    r = _say(db, sid, "ok")
    assert question(r) == "Loại mái dự kiến?" and (step(r).step, step(r).total) == (2, 3)
    assert r.slots["building_area_m2"] is None and r.slots["confirmed"] is False
    r = _say(db, sid, "Mái tôn")
    assert question(r) == "Độ sâu tầng hầm?" and (step(r).step, step(r).total) == (3, 3)


@pytest.mark.db
def test_edit_core_row_from_summary_reasks_single_field(db):
    sid = _new(db)
    r = _say(db, sid, FULL)
    rows = {i.label: i for i in _block(r, "summary_confirm").items}
    r = _say(db, sid, rows["Số tầng"].edit_value)
    assert question(r) == "Số tầng dự kiến?" and (step(r).step, step(r).total) == (4, 5)
    assert r.text == orchestrator.EDIT_ACK
    r = _say(db, sid, "1 trệt 2 lầu")
    assert r.slots["floors"] == 3 and _types(r) == ["summary_confirm"]
    r = _say(db, sid, rows["Diện tích đất"].edit_value)
    assert question(r) == "Diện tích đất?" and step(r).slots == ["land_area_m2"]
    r = _say(db, sid, "Đất 6×12 m")
    assert r.slots["land_area_m2"] == 72 and _types(r) == ["summary_confirm"]


@pytest.mark.db
def test_apartment_flow_five_steps(db):
    sid = _new(db)
    r = _say(db, sid, "Căn hộ")
    assert (question(r), step(r).step, step(r).total) == ("Khu vực xây dựng?", 2, 5)
    r = _say(db, sid, "Hà Nội")
    assert (question(r), step(r).step) == ("Diện tích căn hộ (m²)?", 3)
    r = _say(db, sid, step(r).options[1].value)
    assert (question(r), step(r).step) == ("Căn hộ có mấy tầng?", 4)
    r = _say(db, sid, "Căn hộ 2 tầng (duplex)")
    assert (question(r), step(r).step) == ("Phạm vi thi công cần khái toán?", 5)
    r = _say(db, sid, "Nội thất")
    summary = _block(r, "summary_confirm")
    rows = {i.label: i.value for i in summary.items}
    assert rows["Diện tích căn hộ"] == "80 m²" and rows["Số tầng"] == "2 tầng (duplex)"
    assert summary.more_value is None and not any(a.startswith("Tạm tính") for a in summary.assumptions)


@pytest.mark.db
def test_investment_level_does_not_change_estimate_numbers(db):
    sid = _new(db)
    _say(db, sid, FULL)
    r = _say(db, sid, CONFIRM_VALUE)  # nút "Tính ngay": ngân sách hỏi sau khái toán
    base = _block(r, "estimate_table").data
    assert not any(a.startswith("Mức đầu tư") for a in base.assumptions)
    assert r.text.endswith(orchestrator.BUDGET_TAIL)
    r = _say(db, sid, "đổi mức đầu tư sang siêu cao cấp")
    assert r.slots["investment_level"] == "sieu_cao_cap" and r.slots["confirmed"] is True
    est = _block(r, "estimate_table").data
    assert (est.cost_min, est.cost_max) == (base.cost_min, base.cost_max)
    assert est.estimate_session_id != base.estimate_session_id
    assert ("Mức đầu tư siêu cao cấp chưa được tính vào khái toán vì Dezon chưa có đơn giá theo mức đầu tư."
            in est.assumptions)
    # Sửa thông tin sau khi đã xác nhận: không hỏi lại câu tùy chọn, đi thẳng tới bản xác nhận.
    r = _say(db, sid, "đổi sang 1 trệt 2 lầu")
    assert _types(r) == ["summary_confirm"]


@pytest.mark.db
def test_quote_request_includes_style_and_investment(db):
    sid = _new(db)
    for msg in [f"{FULL}, phong cách tân cổ điển, mức đầu tư phổ thông", CONFIRM_VALUE]:
        r = _say(db, sid, msg)
    est_id = _block(r, "estimate_table").data.estimate_session_id
    # Qua tool trong hội thoại.
    r = _say(db, sid, "tôi muốn nhận báo giá từ Kiến Phong, tên Trần Văn B, sđt 0987654321")
    assert "create_quote_request" in r.tool_calls
    row = db.scalars(select(QuoteRequest).where(QuoteRequest.customer_phone == "0987654321")).first()
    assert "Phong cách thiết kế: Tân cổ điển" in row.note and "Mức đầu tư mong muốn: Phổ thông" in row.note
    # Qua REST (form báo giá): lấy theo phiên chat của estimate_session_id.
    cid = db.scalars(select(Contractor.id).order_by(Contractor.id)).first()
    res = create_quote_requests(db, QuoteRequestIn(contractor_ids=[cid], estimate_session_id=est_id,
                                                   customer_name="Lê C", customer_phone="0911222333",
                                                   note="Gọi buổi sáng"))
    row = db.get(QuoteRequest, res.ids[0])
    assert row.note == "Gọi buổi sáng\nPhong cách thiết kế: Tân cổ điển\nMức đầu tư mong muốn: Phổ thông"


@pytest.mark.db
def test_tool_estimate_adds_investment_note(db):
    sid = _new(db)
    slots = Slots(**CORE, **DONE_DETAILS, investment_level="tiet_kiem", confirmed=True)
    ctx = ToolContext(db=db, chat_session_id=sid, slots=slots, state=split_raw(None)[1], user_texts=[])
    o = execute_tool(ctx, "calculate_estimate", {})
    assert not o.is_error
    assert o.result.assumptions[-1] == ("Mức đầu tư tiết kiệm chưa được tính vào khái toán vì Dezon chưa có đơn giá "
                                        "theo mức đầu tư.")


@pytest.mark.db
def test_full_sentence_goes_to_summary_and_budget_asked_after_estimate(db):
    sid = _new(db)
    r = _say(db, sid, FULL)
    assert _types(r) == ["summary_confirm"]
    assert _block(r, "summary_confirm").more_value is None  # khách đã nêu đủ chi tiết
    r = _say(db, sid, CONFIRM_VALUE)
    assert "estimate_table" in _types(r) and r.text.endswith(orchestrator.BUDGET_TAIL)
    budget = _block(r, "quick_replies")
    assert [o.label for o in budget.options] == ["<2 tỷ", "2–4 tỷ", "4–6 tỷ", "6–10 tỷ", ">10 tỷ"]
    r = _say(db, sid, budget.options[1].value)
    assert r.slots["budget_vnd"] == 4_000_000_000 and r.slots["confirmed"] is True
    assert orchestrator.BUDGET_TAIL not in r.text


# =============================================================================================
# Sửa theo review
# =============================================================================================
@pytest.mark.parametrize("msg", [SKIP_VALUE, "bỏ qua", "Bỏ qua nhé", BACK_VALUE, MORE_VALUE])
def test_skip_or_nav_text_is_not_a_province(msg):
    u = slot_filling.extract(msg, Slots(building_type="nha_pho"), expected_slots=["province", "district"])
    assert u.slots.province is None and "province" not in u.changed


@pytest.mark.db
def test_skip_on_required_card_reasks_same_card(db):
    sid = _new(db)
    r = _say(db, sid, "Nhà phố")
    assert question(r) == "Khu vực xây dựng?" and step(r).skip_value is None
    r = _say(db, sid, SKIP_VALUE)
    assert question(r) == "Khu vực xây dựng?" and step(r).step == 2
    assert r.text == orchestrator.REQUIRED_SKIP_ACK and r.slots["province"] is None
    r = _say(db, sid, "Hà Nội")
    assert r.slots["province"] == "Hà Nội"


@pytest.mark.parametrize("text,value,label", [
    ("Ngân sách trên 10 tỷ", None, "Trên 10 tỷ"),
    ("10 tỷ trở lên", None, "Trên 10 tỷ"),
    ("Ngân sách 4–6 tỷ", 6_000_000_000, "4–6 tỷ"),
    ("ngân sách 2,5-4 tỷ", 4_000_000_000, "2,5–4 tỷ"),
    ("từ 800 triệu đến 1 tỷ", 1_000_000_000, "800 triệu–1 tỷ"),
    ("Ngân sách dưới 2 tỷ", 2_000_000_000, "Dưới 2 tỷ"),
    ("3,5 tỷ", 3_500_000_000, None),
])
def test_parse_budget_info(text, value, label):
    from app.ai.normalize import parse_budget_info
    assert parse_budget_info(text) == (value, label)


def test_budget_label_merge_and_summary():
    s = Slots(**CORE, **DONE_DETAILS)
    u = slot_filling.extract("Ngân sách trên 10 tỷ", s.model_copy(update={"budget_vnd": 4_000_000_000}),
                             expected_slots=["budget_vnd"])
    # "Trên 10 tỷ": bỏ trần cũ, chỉ giữ nhãn; câu hỏi ngân sách coi như đã trả lời.
    assert u.slots.budget_vnd is None and u.slots.budget_label == "Trên 10 tỷ"
    assert dialog.budget_question(u.slots) is None and "budget_vnd" not in dialog.reaskable_slots(u.slots)
    rows = {i.label: i.value for i in build_summary(u.slots)[1].items}
    assert rows["Ngân sách"] == "Trên 10 tỷ"
    assert "ngân sách trên 10 tỷ" in build_summary(u.slots)[0]
    # Số mới không kèm khoảng -> bỏ nhãn cũ.
    u2 = slot_filling.extract("3,5 tỷ", u.slots, expected_slots=["budget_vnd"])
    assert u2.slots.budget_vnd == 3_500_000_000 and u2.slots.budget_label is None
    assert {i.label: i.value for i in build_summary(u2.slots)[1].items}["Ngân sách"] == "3,5 tỷ"
    # Nhãn ngân sách không làm đổi khái toán.
    assert estimate_slots_hash(u.slots) == estimate_slots_hash(s)


@pytest.mark.db
def test_open_ended_budget_has_no_ceiling_after_estimate(db):
    sid = _new(db)
    _say(db, sid, FULL)
    r = _say(db, sid, CONFIRM_VALUE)
    assert "estimate_table" in _types(r) and r.text.endswith(orchestrator.BUDGET_TAIL)
    r = _say(db, sid, next(o.value for o in _block(r, "quick_replies").options if o.label == ">10 tỷ"))
    assert r.slots["budget_vnd"] is None and r.slots["budget_label"] == "Trên 10 tỷ"
    assert "budget_options" not in _types(r) and "suggest_budget_adjustments" not in r.tool_calls
    assert orchestrator.BUDGET_TAIL not in r.text


@pytest.mark.db
@pytest.mark.parametrize("edit,answer,slot,value", [
    (dialog.EDIT_INVESTMENT, "Mức đầu tư trung cấp", "investment_level", "trung_cap"),
    (dialog.EDIT_DESIGN_STYLE, "Phong cách tối giản", "design_style", "toi_gian"),
    (dialog.EDIT_BUDGET, "Ngân sách 4–6 tỷ", "budget_vnd", 6_000_000_000),
])
def test_edit_optional_after_estimate_keeps_confirmation(db, edit, answer, slot, value):
    sid = _new(db)
    for msg in [FULL, *SKIP_OPTIONAL, CONFIRM_VALUE]:
        r = _say(db, sid, msg)
    assert "estimate_table" in _types(r)
    est_id = split_raw(db.get(ChatSession, sid).slots)[1].estimate.id
    r = _say(db, sid, edit)
    card = step(r)
    assert card is not None and card.slots == list(dialog.KEY_SLOTS[dialog.key_of(card.slots)])
    assert card.back_value is None and r.slots["confirmed"] is True and "summary_confirm" not in _types(r)
    r = _say(db, sid, answer)
    assert r.slots[slot] == value and r.slots["confirmed"] is True
    assert "summary_confirm" not in _types(r)
    st = split_raw(db.get(ChatSession, sid).slots)[1]
    if slot == "investment_level":
        # Bảng khái toán dựng lại kèm ghi chú; con số giữ nguyên (mức đầu tư không vào công thức).
        est = _block(r, "estimate_table").data
        assert "Mức đầu tư trung cấp chưa được tính vào khái toán" in " ".join(est.assumptions)
    else:
        assert st.estimate.id == est_id  # phong cách / ngân sách: không tính lại khái toán


def test_apartment_summary_wording():
    s = Slots(building_type="can_ho", province="Hà Nội", land_area_m2=80, floors=1, work_scope="noi_that")
    text, block = build_summary(s)
    rows = {i.label: i.value for i in block.items}
    assert rows["Diện tích căn hộ"] == "80 m²" and rows["Số tầng"] == "1 tầng" and "Diện tích đất" not in rows
    assert "đất" not in text and "trệt" not in text and "diện tích 80 m²" in text
    duplex = build_summary(s.model_copy(update={"floors": 2}))[1]
    assert {i.label: i.value for i in duplex.items}["Số tầng"] == "2 tầng (duplex)"


def test_combined_land_and_floors_value():
    # Khách nhắn cả đất và số tầng trong một tin (vd ở thẻ "Diện tích đất?") -> nhận cả hai, câu số tầng bỏ qua.
    u = slot_filling.extract("Đất 5×10 m, 1 trệt 3 lầu", Slots(building_type="nha_pho", province=HCM),
                             expected_slots=["land_area_m2", "floors"])
    assert (u.slots.land_area_m2, u.slots.floors) == (50, 4)
