"""Logic hỏi tiếp + tóm tắt (đặc tả mục 5.1–5.4, nghiệm thu 13 hai tiêu chí đầu)."""
import pytest

from app.ai import dialog
from app.ai.dialog import budget_question, build_summary, greeting, next_question
from app.ai.slot_filling import extract
from app.formatting import NO_DATA
from app.schemas.slots import DETAIL_SLOTS, OPTIONAL_SLOTS, Slots, missing_required
from app.schemas.ui_blocks import QuestionStepBlock, SummaryConfirmBlock

HCM = "TP. Hồ Chí Minh"


def labels(block) -> list[str]:
    return [o.label for o in block.options]


# Câu tùy chọn (phong cách, ngân sách, mức đầu tư) không bao giờ hỏi trước bản tóm tắt.
OPT_ASKED = list(OPTIONAL_SLOTS)
DEFAULTS_TAIL = " — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi."


def test_greeting_matches_example():
    text, block = greeting()
    assert text.startswith("Xin chào anh/chị! Em có thể hỗ trợ khái toán chi phí xây dựng")
    assert "?" not in text  # câu hỏi nằm trên thẻ question_step, không lặp trong văn bản
    assert isinstance(block, QuestionStepBlock)
    assert block.question == "Loại công trình anh/chị dự định xây dựng?"
    assert labels(block)[:5] == ["Nhà phố", "Biệt thự", "Căn hộ", "Văn phòng", "Khách sạn"]
    assert block.step == 1 and block.total == 5 and block.back_value is None
    assert block.skippable is False and block.skip_value is None and block.slots == ["building_type"]
    assert block.input_placeholder == "Nhập câu trả lời của anh/chị"


def test_greeting_buttons_understood_with_greeting_expected_slots():
    _, block = greeting()
    assert dialog.GREETING_EXPECTED_SLOTS == ["building_type"]
    for opt in block.options:
        u = extract(opt.value, Slots(), expected_slots=dialog.GREETING_EXPECTED_SLOTS)
        assert u.slots.building_type is not None, opt.value
    other = next(o for o in block.options if o.label == "Khác")
    assert extract(other.value, Slots()).slots.building_type == "khac"


def test_summary_other_building_type_wording():
    s = Slots(building_type="khac", province=HCM, land_area_m2=100, floors=1, work_scope="noi_that")
    text, _ = build_summary(s)
    assert text.startswith("Em tóm tắt lại: Công trình khác tại TP. Hồ Chí Minh")


def test_conversation_5_4_step_by_step():
    """Mô phỏng ví dụ 5.4: mỗi lượt hỏi đúng slot còn thiếu, có quick replies, tối đa 2 thông tin."""
    s = Slots()
    q = next_question(s)
    assert q.slots == ["building_type"]

    u = extract("Tôi muốn xây nhà phố.", s, expected_slots=q.slots)
    s = u.slots
    q = next_question(s)
    assert q.slots[0] == "province"
    assert "khu vực nào" in q.text and q.text.startswith("Dạ")
    assert labels(q.quick_replies) == ["TP. Hồ Chí Minh", "Hà Nội", "Đà Nẵng"]

    s = extract("TP. Hồ Chí Minh.", s, expected_slots=q.slots).slots
    assert s.province == HCM
    # Diện tích đất và số tầng là HAI câu riêng (3/5, 4/5).
    q = next_question(s)
    assert q.slots == ["land_area_m2"]
    assert q.text == "Anh/chị cho em xin diện tích đất (ngang × dài hoặc tổng m²) nhé."
    assert q.title == "Diện tích đất?" and q.options == []
    assert [c.label for c in q.chips] == ["4 × 10", "5 × 10", "6 × 10", "6 × 12", "9 × 12", "12 × 10"]

    s = extract("5x20", s, expected_slots=q.slots).slots
    assert s.land_area_m2 == 100 and s.floors is None
    q = next_question(s)
    assert q.slots == ["floors"] and q.title == "Số tầng dự kiến?"
    assert labels(q) == ["1 trệt", "1 trệt 1 lầu", "1 trệt 2 lầu", "1 trệt 3 lầu", "1 trệt 4 lầu"]

    s = extract("1 trệt 3 lầu", s, expected_slots=q.slots).slots
    q = next_question(s)
    assert q.slots == ["work_scope"]
    assert labels(q.quick_replies) == ["Xây thô", "Hoàn thiện", "Trọn gói", "Nội thất"]

    s = extract("Xây thô", s, expected_slots=q.slots).slots
    # Đủ 5 slot cốt lõi -> hết câu hỏi (không hỏi chi tiết, phong cách, ngân sách, mức đầu tư) -> bản tóm tắt.
    assert next_question(s) is None and missing_required(s) == []

    text, block = build_summary(s)
    assert text == ("Em tóm tắt lại: Nhà phố tại TP. Hồ Chí Minh, đất 5 × 20 m (100 m²), 1 trệt 3 lầu (4 tầng), xây "
                    "thô. Anh/chị xác nhận để em tính khái toán nhé?")
    assert "mức hoàn thiện" not in text.lower()
    # Chi tiết chưa hỏi -> mặc định của engine nêu rõ trong MỘT dòng "Tạm tính: ..." + quy ước móng.
    assert block.assumptions == [
        "Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng, mái bê tông cốt thép" + DEFAULTS_TAIL,
        "Móng tính theo thông lệ: móng cọc (nhà từ 4 tầng nổi trở lên), chưa gồm chi phí ép cọc.",
    ]
    assert block.confirm.label == "Đúng rồi" and block.edit.label == "Sửa lại"
    assert block.title == "Xác nhận thông tin — Nhà phố"
    assert (block.confirm_label, block.confirm_value) == ("Tính ngay → xem bảng khái toán",
                                                          "Đúng rồi, tính khái toán giúp tôi")
    # Chi tiết đang dùng mặc định -> có nút "Cung cấp thêm thông tin".
    assert (block.more_label, block.more_value) == ("Cung cấp thêm thông tin để báo giá chính xác hơn",
                                                    "Tôi muốn cung cấp thêm thông tin")
    items = {i.label: (i.value, i.edit_value) for i in block.items}
    assert items["Số tầng"] == ("1 trệt 3 lầu (4 tầng)", "Tôi muốn sửa số tầng")
    # Mục chi tiết mặc định có dòng riêng "(tạm tính)" kèm nút Sửa.
    assert items["Diện tích xây dựng mỗi tầng"] == ("100 m² (tạm tính)", "Tôi muốn sửa diện tích xây dựng")
    assert items["Tầng hầm"] == ("Không (tạm tính)", "Tôi muốn sửa thông tin hầm, lửng của nhà")
    assert items["Tầng lửng"] == ("Không (tạm tính)", "Tôi muốn sửa thông tin hầm, lửng của nhà")
    assert items["Mái"] == ("Mái bê tông cốt thép (tạm tính)", "Tôi muốn sửa loại mái")
    assert "Ngân sách" not in items and "Phong cách" not in items and "Mức đầu tư" not in items

    u = extract(block.confirm.value, s)
    assert u.intent == "confirm" and u.slots.confirmed is True


def test_more_flow_details_in_order_then_none():
    """"Cung cấp thêm thông tin": diện tích xây dựng -> hầm/lửng -> mái -> độ sâu hầm -> diện tích lửng."""
    s = Slots(building_type="nha_pho", province=HCM, land_width_m=5, land_length_m=20, land_area_m2=100, floors=4,
              work_scope="xay_tho")
    q = next_question(s, more=True)
    assert q.slots == ["building_area_m2"]
    assert q.text == "Diện tích xây dựng mỗi tầng khoảng bao nhiêu m² ạ? (phần sàn xây, không tính sân)"
    assert labels(q.quick_replies) == ["Xây kín đất (100 m²)", "Chưa biết"]
    s = extract(q.quick_replies.options[0].value, s, expected_slots=q.slots).slots
    assert s.building_area_m2 == 100
    q = next_question(s, more=True)
    assert q.slots == ["basement", "has_mezzanine"] and q.text == "Nhà có tầng hầm hoặc tầng lửng không ạ?"
    assert labels(q.quick_replies) == ["Không hầm, không lửng", "Có tầng lửng", "Có tầng hầm", "Có cả hầm và lửng"]
    s = extract("Có tầng hầm và tầng lửng", s, expected_slots=q.slots).slots
    q = next_question(s, more=True)
    assert q.slots == ["roof_type"] and q.text == "Mái nhà dự kiến làm loại nào ạ?"
    assert labels(q.quick_replies) == ["Mái bằng bê tông", "Mái tôn", "Mái ngói kèo thép", "Mái ngói đổ bê tông",
                                       "Chưa biết"]
    s = extract("Mái tôn", s, expected_slots=q.slots).slots
    q = next_question(s, more=True)
    assert q.slots == ["basement_depth_m"]
    s = extract("Hầm sâu 1,5 m", s, expected_slots=q.slots).slots
    q = next_question(s, more=True)
    assert q.slots == ["mezzanine_area_m2"]
    s = extract("Tầng lửng 50 m2", s, expected_slots=q.slots).slots
    assert next_question(s, more=True) is None and next_question(s) is None
    _, block = build_summary(s)
    assert block.more_value is None  # không còn mục nào dùng mặc định
    assert not any(a.startswith("Tạm tính") for a in block.assumptions)


def test_land_and_floors_asked_separately_when_one_known():
    s = Slots(building_type="nha_pho", province=HCM, floors=4)
    q = next_question(s)
    assert q.slots == ["land_area_m2"] and q.title == "Diện tích đất?"
    assert [c.label for c in q.chips] == ["4 × 10", "5 × 10", "6 × 10", "6 × 12", "9 × 12", "12 × 10"]
    assert q.options == []
    s = Slots(building_type="nha_pho", province=HCM, land_area_m2=100)
    q = next_question(s)
    assert q.slots == ["floors"] and q.title == "Số tầng dự kiến?"
    assert labels(q) == ["1 trệt", "1 trệt 1 lầu", "1 trệt 2 lầu", "1 trệt 3 lầu", "1 trệt 4 lầu"]


def test_each_question_asks_at_most_two_infos_and_uses_em_anh_chi():
    s = Slots()
    for msg in ["Biệt thự", "Đà Nẵng", "Đất 5×20 m", "1 trệt 2 lầu", "Trọn gói", "80 m2", "Có tầng hầm, không lửng",
                "Mái ngói kèo thép", "Hầm sâu 1,5 m"]:
        q = next_question(s, more=True)
        assert q is not None
        assert len([x for x in q.slots if x != "district"]) <= 2
        assert "anh/chị" in q.text.lower() or "ạ?" in q.text
        assert q.quick_replies.options and q.title.endswith("?")
        s = extract(msg, s, expected_slots=q.slots).slots
    assert next_question(s, more=True) is None


def test_full_sentence_then_details_unknown_and_summary_complete():
    s = extract("nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô ngân sách 2 tỷ", Slots()).slots
    assert next_question(s) is None  # đủ 5 câu cốt lõi -> bản tóm tắt ngay (không hỏi chi tiết)
    assert missing_required(s) == []
    # "Cung cấp thêm thông tin" + khách chưa biết các chi tiết -> đã hỏi, dùng mặc định và nêu rõ.
    for _ in range(3):
        q = next_question(s, more=True)
        s = extract("chưa biết", s, expected_slots=q.slots).slots
    assert next_question(s, more=True) is None
    assert s.building_area_m2 is None and s.basement is None and s.roof_type is None
    text, block = build_summary(s)
    assert isinstance(block, SummaryConfirmBlock)
    items = {i.label: i.value for i in block.items}
    assert items == {
        "Loại công trình": "Nhà phố",
        "Khu vực": "TP. Thủ Đức, TP. Hồ Chí Minh",
        "Diện tích đất": "5 × 20 m (100 m²)",
        "Số tầng": "1 trệt 3 lầu (4 tầng)",
        "Phạm vi": "Xây thô",
        "Diện tích xây dựng mỗi tầng": "100 m² (tạm tính)",
        "Tầng hầm": "Không (tạm tính)",
        "Tầng lửng": "Không (tạm tính)",
        "Mái": "Mái bê tông cốt thép (tạm tính)",
        "Ngân sách": "2 tỷ",
    }
    assert text.startswith("Em tóm tắt lại: Nhà phố tại TP. Thủ Đức, TP. Hồ Chí Minh, đất 5 × 20 m (100 m²)")
    assert "ngân sách 2 tỷ" in text and "mức hoàn thiện" not in text.lower()
    assert block.assumptions == [
        "Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng, mái bê tông cốt thép" + DEFAULTS_TAIL,
        "Móng tính theo thông lệ: móng cọc (nhà từ 4 tầng nổi trở lên), chưa gồm chi phí ép cọc.",
    ]
    # Khách đã trả lời "chưa biết" -> vẫn có nút "Cung cấp thêm thông tin" (hỏi lại).
    assert block.more_value == dialog.MORE_VALUE


def test_summary_optional_fields_and_mezzanine():
    s = extract("biệt thự 10x20 Quận 7, 1 trệt 1 lửng 2 lầu, có tầng hầm, mái ngói, móng cọc, trọn gói, cao cấp",
                Slots()).slots
    _, block = build_summary(s)
    items = {i.label: i.value for i in block.items}
    assert items["Số tầng"] == "1 trệt 1 lửng 2 lầu (3 tầng + lửng)"
    assert "Mức hoàn thiện" not in items and s.quality_tier is None  # không phân mức hoàn thiện
    assert items["Tầng hầm"] == "Có"
    assert items["Tầng lửng"] == "Có"
    assert items["Mái"] == "Mái ngói đổ BTCT"
    assert items["Móng"] == "Móng cọc"
    # Có hầm / lửng nhưng chưa rõ độ sâu / diện tích -> mặc định nêu trong dòng "Tạm tính"; móng khách đã nói -> không
    # nêu quy ước móng.
    assert block.assumptions == [
        "Tạm tính: xây kín đất (200 m²/tầng), hầm sâu 1,7–2 m, lửng bằng diện tích sàn (200 m²)" + DEFAULTS_TAIL]


def test_summary_area_only_and_one_floor():
    s = Slots(building_type="nha_pho", province="Hà Nội", land_area_m2=80, floors=1, work_scope="hoan_thien")
    _, block = build_summary(s)
    items = {i.label: i.value for i in block.items}
    assert items["Diện tích đất"] == "80 m²"
    assert items["Số tầng"] == "1 trệt (1 tầng)"
    # C-F12: phạm vi Hoàn thiện không tính móng -> không nêu giả định móng; xây thô thì có.
    assert not any("Móng" in a for a in block.assumptions)
    _, rough = build_summary(s.model_copy(update={"work_scope": "xay_tho"}))
    assert "Móng tính theo thông lệ: móng băng (nhà đến 3 tầng nổi)." in rough.assumptions


def test_summary_missing_fields_show_no_data():
    text, block = build_summary(Slots(building_type="nha_pho"))
    items = {i.label: i.value for i in block.items}
    assert items["Khu vực"] == NO_DATA
    assert items["Số tầng"] == NO_DATA
    assert "bổ sung" in text


def test_budget_question():
    s = Slots(building_type="nha_pho", province=HCM, land_area_m2=100, floors=4, work_scope="xay_tho")
    q = budget_question(s)
    assert q.slots == ["budget_vnd"]
    assert labels(q.quick_replies) == ["<2 tỷ", "2–4 tỷ", "4–6 tỷ", "6–10 tỷ", ">10 tỷ"]
    assert budget_question(s.model_copy(update={"budget_vnd": 2_000_000_000})) is None
    # Đã hỏi (khách chưa trả lời con số) -> không hỏi lại.
    s2 = extract("chưa biết nữa", s, expected_slots=q.slots).slots
    assert budget_question(s2) is None


# --- value của mỗi quick reply phải được extract() hiểu đúng --------------------

QUICK_REPLY_CASES = [
    (dialog.BUILDING_TYPE_REPLIES, ["building_type"], "building_type",
     ["nha_pho", "biet_thu", "can_ho", "van_phong", "khach_san", "khac"]),
    (dialog.PROVINCE_REPLIES, ["province", "district"], "province", [HCM, "Hà Nội", "Đà Nẵng"]),
    (dialog.LAND_REPLIES, ["land_area_m2"], "land_area_m2", [40, 50, 60, 72, 108, 120]),
    (dialog.FLOORS_REPLIES, ["floors"], "floors", [1, 2, 3, 4, 5]),
    (dialog.APARTMENT_AREA_REPLIES, ["land_area_m2"], "land_area_m2", [50, 80, 120, 200, 300]),
    (dialog.APARTMENT_FLOORS_REPLIES, ["floors"], "floors", [1, 2]),
    (dialog.DESIGN_STYLE_REPLIES, ["design_style"], "design_style", ["hien_dai", "toi_gian", "tan_co_dien",
                                                                     "indochine"]),
    (dialog.INVESTMENT_LEVEL_REPLIES, ["investment_level"], "investment_level",
     ["tiet_kiem", "pho_thong", "trung_cap", "sieu_cao_cap"]),
    (dialog.WORK_SCOPE_REPLIES, ["work_scope"], "work_scope", ["xay_tho", "hoan_thien", "tron_goi", "noi_that"]),
    (dialog.BUDGET_REPLIES, ["budget_vnd"], "budget_vnd",
     [2_000_000_000, 4_000_000_000, 6_000_000_000, 10_000_000_000, None]),  # ">10 tỷ": không có trần
    (dialog.START_TIME_REPLIES, ["start_time"], "start_time", ["Trong 3 tháng", "3–6 tháng", "Chưa xác định"]),
]


@pytest.mark.parametrize("replies,expected_slots,slot,values", QUICK_REPLY_CASES)
def test_quick_reply_values_understood(replies, expected_slots, slot, values):
    assert len(replies) == len(values)
    for reply, value in zip(replies, values, strict=True):
        u = extract(reply.value, Slots(), expected_slots=expected_slots)
        assert getattr(u.slots, slot) == value, reply.value
        assert slot in u.changed or (value is None and "budget_label" in u.changed), reply.value


@pytest.mark.parametrize("replies", [dialog.BUILDING_TYPE_REPLIES, dialog.WORK_SCOPE_REPLIES, dialog.PROVINCE_REPLIES,
                                     dialog.BUDGET_REPLIES, dialog.FLOORS_REPLIES, dialog.LAND_REPLIES,
                                     dialog.APARTMENT_AREA_REPLIES, dialog.APARTMENT_FLOORS_REPLIES,
                                     dialog.DESIGN_STYLE_REPLIES, dialog.INVESTMENT_LEVEL_REPLIES])
def test_quick_reply_values_work_without_expected_slots(replies):
    # Kể cả nút "Khác" (value "Công trình khác") — hiểu được cả ngay sau lời chào khi chưa có expected_slots.
    for reply in replies:
        assert extract(reply.value, Slots()).changed, reply.value


# --- câu hỏi chi tiết (sau 5 slot cốt lõi) -------------------------------------------

CORE = dict(building_type="nha_pho", province=HCM, land_width_m=5, land_length_m=16, land_area_m2=80, floors=3,
            work_scope="tron_goi")


def test_building_area_quick_replies_parse():
    s = Slots(**CORE)
    q = next_question(s, more=True)
    assert q.slots == ["building_area_m2"]
    full, unknown = q.quick_replies.options
    assert (full.label, full.value) == ("Xây kín đất (80 m²)", "Diện tích xây dựng mỗi tầng 80 m2")
    u = extract(full.value, s, expected_slots=q.slots)
    assert u.slots.building_area_m2 == 80 and u.changed == ["building_area_m2"]
    assert u.slots.land_area_m2 == 80 and u.slots.land_width_m == 5  # không đụng diện tích đất
    assert (unknown.label, unknown.value) == ("Chưa biết", "Tôi chưa biết diện tích xây dựng")
    u = extract(unknown.value, s, expected_slots=q.slots)
    assert u.changed == [] and u.slots.building_area_m2 is None and "building_area_m2" in u.slots.asked
    assert next_question(u.slots, more=True).slots == ["basement", "has_mezzanine"]  # không hỏi lại


def test_building_area_fractional_land_reply_parses():
    s = Slots(**{**CORE, "land_width_m": 4.5, "land_length_m": 15, "land_area_m2": 67.5})
    full = next_question(s, more=True).quick_replies.options[0]
    assert full.label == "Xây kín đất (67,5 m²)"
    assert extract(full.value, s, expected_slots=["building_area_m2"]).slots.building_area_m2 == 67.5


@pytest.mark.parametrize("msg,area", [("80 m2", 72), ("72 m2", 72), ("72", 72), ("khoảng 65", 65), ("65m²", 65),
                                      ("xây kín đất", 80), ("kín đất", 80)])
def test_building_area_free_text_answers(msg, area):
    """Đang hỏi diện tích xây dựng: số m² (hoặc số trơn) là diện tích xây dựng, không phải đổi diện tích đất."""
    if msg == "80 m2":
        msg = f"{area} m2"
    u = extract(msg, Slots(**CORE), expected_slots=["building_area_m2"])
    assert u.slots.building_area_m2 == area
    assert u.slots.land_area_m2 == 80 and u.slots.land_width_m == 5
    assert "land_area_m2" not in u.changed


def test_building_area_question_still_accepts_new_land_area():
    u = extract("đất 90 m2", Slots(**CORE), expected_slots=["building_area_m2"])
    assert u.slots.land_area_m2 == 90 and u.slots.building_area_m2 is None


@pytest.mark.parametrize("value,basement,mezz", [
    ("Không hầm, không lửng", False, False), ("Có tầng lửng, không hầm", False, True),
    ("Có tầng hầm, không lửng", True, False), ("Có tầng hầm và tầng lửng", True, True)])
def test_basement_mezzanine_quick_replies_parse(value, basement, mezz):
    assert value in [r.value for r in dialog.BASEMENT_MEZZ_REPLIES]
    s = Slots(**CORE, building_area_m2=80)
    u = extract(value, s, expected_slots=["basement", "has_mezzanine"])
    assert (u.slots.basement, u.slots.has_mezzanine) == (basement, mezz)
    assert u.intent == "provide_info"
    q = next_question(u.slots, more=True)
    assert q.slots == ["roof_type"]  # mái trước; độ sâu hầm / diện tích lửng (nếu có) hỏi sau
    q = next_question(u.slots.model_copy(update={"roof_type": "roof_metal"}), more=True)
    assert (q.slots if q else None) == (["basement_depth_m"] if basement else ["mezzanine_area_m2"] if mezz else None)


def test_bare_no_answers_basement_mezzanine_question():
    s = Slots(**CORE, building_area_m2=80)
    u = extract("Không", s, expected_slots=["basement", "has_mezzanine"])
    assert u.slots.basement is False and u.slots.has_mezzanine is False
    assert u.intent == "provide_info"  # không phải ý muốn sửa
    u = extract("có", s.model_copy(update={"has_mezzanine": False}), expected_slots=["basement"])
    assert u.slots.basement is True


def test_single_basement_or_mezzanine_question_when_other_known():
    s = Slots(**{**CORE, "has_mezzanine": True}, building_area_m2=80)
    q = next_question(s, more=True)
    assert q.slots == ["basement"] and q.text == "Nhà có tầng hầm không ạ?"
    for r, v in zip(q.quick_replies.options, [False, True], strict=True):
        assert extract(r.value, s, expected_slots=q.slots).slots.basement is v
    s = Slots(**{**CORE, "basement": False}, building_area_m2=80)
    q = next_question(s, more=True)
    assert q.slots == ["has_mezzanine"] and q.text == "Nhà có tầng lửng không ạ?"
    for r, v in zip(q.quick_replies.options, [False, True], strict=True):
        assert extract(r.value, s, expected_slots=q.slots).slots.has_mezzanine is v


def test_basement_depth_quick_replies_parse():
    s = Slots(**CORE, building_area_m2=80, basement=True, has_mezzanine=False, roof_type="roof_concrete")
    q = next_question(s, more=True)
    assert q.slots == ["basement_depth_m"]
    assert labels(q.quick_replies) == ["Dưới 1,3 m", "1,3–1,7 m", "1,7–2 m", "Trên 2 m", "Chưa biết"]
    depths = [1.2, 1.5, 1.9, 2.5]
    for reply, depth in zip(q.quick_replies.options[:4], depths, strict=True):
        u = extract(reply.value, s, expected_slots=q.slots)
        assert u.slots.basement_depth_m == depth and u.changed == ["basement_depth_m"], reply.value
    u = extract(q.quick_replies.options[4].value, s, expected_slots=q.slots)
    assert u.changed == [] and "basement_depth_m" in u.slots.asked
    assert next_question(u.slots, more=True) is None
    _, block = build_summary(u.slots)
    assert "Tạm tính: hầm sâu 1,7–2 m" + DEFAULTS_TAIL in block.assumptions
    # Trả lời số trơn / "1,5 m".
    assert extract("1,5 m", s, expected_slots=q.slots).slots.basement_depth_m == 1.5
    assert extract("2", s, expected_slots=q.slots).slots.basement_depth_m == 2


def test_roof_quick_replies_parse():
    s = Slots(**CORE, building_area_m2=80, basement=False, has_mezzanine=False)
    q = next_question(s, more=True)
    assert q.slots == ["roof_type"]
    roofs = ["roof_concrete", "roof_metal", "roof_tile_on_truss", "roof_tile_on_concrete"]
    for reply, roof in zip(q.quick_replies.options[:4], roofs, strict=True):
        u = extract(reply.value, s, expected_slots=q.slots)
        assert u.slots.roof_type == roof and u.changed == ["roof_type"], reply.value
        assert next_question(u.slots, more=True) is None  # hết chi tiết -> bản tóm tắt (không hỏi câu tùy chọn)
    unknown = q.quick_replies.options[4]
    assert unknown.label == "Chưa biết"
    u = extract(unknown.value, s, expected_slots=q.slots)
    assert u.changed == [] and u.slots.roof_type is None and next_question(u.slots, more=True) is None


def test_ok_to_detail_question_is_not_summary_confirmation():
    s = Slots(**CORE, building_area_m2=80, basement=False, has_mezzanine=False)
    u = extract("ok", s, expected_slots=["roof_type"])
    assert u.slots.confirmed is False  # bản tóm tắt chưa hiển thị
    assert u.missing_required == []  # đã hỏi mái -> lượt sau tóm tắt


@pytest.mark.parametrize("update", [{"building_type": "can_ho"}, {"work_scope": "noi_that"}])
def test_details_not_asked_for_apartment_or_interior(update):
    s = Slots(**{**CORE, **update})
    assert missing_required(s) == [] and next_question(s) is None and next_question(s, more=True) is None
    assert next_question(s.model_copy(update={"asked": OPT_ASKED})) is None
    assert dialog.detail_keys(s) == []
    _, block = build_summary(s)
    assert not any(a.startswith("Tạm tính") for a in block.assumptions)
    assert block.more_value is None  # không có mục chi tiết nào để hỏi thêm
    assert not {"Tầng hầm", "Tầng lửng", "Mái", "Diện tích xây dựng mỗi tầng"} & {i.label for i in block.items}


def test_detail_slots_order():
    assert DETAIL_SLOTS == ["building_area_m2", "basement", "has_mezzanine", "roof_type", "basement_depth_m",
                            "mezzanine_area_m2"]
    s = Slots(**CORE)
    # Chi tiết không còn chặn bản tóm tắt: chỉ 5 slot cốt lõi là bắt buộc.
    assert missing_required(s) == []
    assert dialog.more_slots(s) == ["building_area_m2", "basement", "has_mezzanine", "roof_type"]
    assert dialog.more_slots(s.model_copy(update={"basement": True})) == [
        "building_area_m2", "has_mezzanine", "roof_type", "basement_depth_m"]
    # Thiếu slot cốt lõi -> chỉ trả slot cốt lõi.
    assert missing_required(Slots(building_type="nha_pho")) == ["province", "land_area_m2", "floors", "work_scope"]


def test_no_quality_tier_anywhere_in_dialog():
    assert not hasattr(dialog, "QUALITY_TIER_REPLIES")
    assert all("hoàn thiện" not in r.label.lower() for r in dialog.EDIT_FIELD_REPLIES)
    assert dialog.edit_field_question("Tôi muốn sửa mức chất lượng") is None
    assert extract("mức cao cấp", Slots()).slots.quality_tier is None


@pytest.mark.parametrize("label,slots_", [("Diện tích xây dựng", ["building_area_m2"]),
                                          ("Hầm, lửng", ["basement", "has_mezzanine"]), ("Mái", ["roof_type"]),
                                          ("Diện tích đất", ["land_area_m2"]), ("Số tầng", ["floors"])])
def test_edit_field_replies_ask_the_field(label, slots_):
    s = Slots(**CORE, building_area_m2=80, basement=False, has_mezzanine=False, roof_type="roof_metal")
    reply = next(r for r in dialog.EDIT_FIELD_REPLIES if r.label == label)
    u = extract(reply.value, s)
    assert u.intent == "edit" and u.changed == [], reply.value  # không trích slot nào từ nút sửa
    q = dialog.edit_field_question(reply.value, s)
    assert q is not None and q.slots == slots_
    if label == "Diện tích xây dựng":
        assert labels(q.quick_replies) == ["Xây kín đất (80 m²)", "Chưa biết"]


def test_edit_field_replies_hide_details_for_apartment():
    lbls = [r.label for r in dialog.edit_field_replies(Slots(**{**CORE, "building_type": "can_ho"}))]
    assert "Mái" not in lbls and "Hầm, lửng" not in lbls and "Số tầng" in lbls
    # "Diện tích lửng" chỉ hiện khi nhà có lửng.
    assert len(dialog.edit_field_replies(Slots(**CORE))) == len(dialog.EDIT_FIELD_REPLIES) - 1
    assert len(dialog.edit_field_replies(Slots(**CORE, has_mezzanine=True))) == len(dialog.EDIT_FIELD_REPLIES)


def test_summary_buttons_understood():
    s = Slots(building_type="nha_pho", province=HCM, land_area_m2=100, floors=4, work_scope="xay_tho")
    _, block = build_summary(s)
    assert extract(block.confirm.value, s).intent == "confirm"
    assert extract(block.edit.value, s).intent == "edit"
