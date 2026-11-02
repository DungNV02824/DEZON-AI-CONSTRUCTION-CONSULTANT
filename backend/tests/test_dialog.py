"""Logic hỏi tiếp + tóm tắt (đặc tả mục 5.1–5.4, nghiệm thu 13 hai tiêu chí đầu)."""
import pytest

from app.ai import dialog
from app.ai.dialog import budget_question, build_summary, greeting, next_question
from app.ai.slot_filling import extract
from app.formatting import NO_DATA
from app.schemas.slots import Slots
from app.schemas.ui_blocks import QuickRepliesBlock, SummaryConfirmBlock

HCM = "TP. Hồ Chí Minh"


def labels(block: QuickRepliesBlock) -> list[str]:
    return [o.label for o in block.options]


def test_greeting_matches_example():
    text, block = greeting()
    assert text.startswith("Xin chào anh/chị! Em có thể hỗ trợ khái toán chi phí xây dựng")
    assert "loại công trình nào" in text
    assert labels(block)[:5] == ["Nhà phố", "Biệt thự", "Căn hộ", "Văn phòng", "Khách sạn"]
    assert block.allow_free_text is True


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
    q = next_question(s)
    assert q.slots == ["land_area_m2", "floors"]
    assert q.text == "Anh/chị cho em xin thêm diện tích đất và số tầng dự kiến nhé."
    assert labels(q.quick_replies) == ["4 × 16 m", "5 × 20 m", "1 trệt 2 lầu", "1 trệt 3 lầu"]

    s = extract("5x20, 1 trệt 3 lầu", s, expected_slots=q.slots).slots
    q = next_question(s)
    assert q.slots == ["work_scope"]
    assert labels(q.quick_replies) == ["Xây thô", "Hoàn thiện", "Trọn gói", "Nội thất"]

    s = extract("Xây thô", s, expected_slots=q.slots).slots
    assert next_question(s) is None  # không hỏi quality_tier — dùng mặc định

    text, block = build_summary(s)
    assert text == ("Em tóm tắt lại: Nhà phố tại TP. Hồ Chí Minh, đất 5 × 20 m (100 m²), 1 trệt 3 lầu (4 tầng), "
                    "xây thô, mức hoàn thiện trung bình (mặc định). Anh/chị xác nhận để em tính khái toán nhé?")
    assert block.confirm.label == "Đúng rồi" and block.edit.label == "Sửa lại"

    u = extract(block.confirm.value, s)
    assert u.intent == "confirm" and u.slots.confirmed is True


def test_land_and_floors_asked_separately_when_one_known():
    s = Slots(building_type="nha_pho", province=HCM, floors=4)
    q = next_question(s)
    assert q.slots == ["land_area_m2"]
    assert labels(q.quick_replies) == ["4 × 16 m", "5 × 20 m"]
    s = Slots(building_type="nha_pho", province=HCM, land_area_m2=100)
    q = next_question(s)
    assert q.slots == ["floors"]
    assert labels(q.quick_replies) == ["1 trệt 2 lầu", "1 trệt 3 lầu"]


def test_each_question_asks_at_most_two_infos_and_uses_em_anh_chi():
    s = Slots()
    for msg in ["Biệt thự", "Đà Nẵng", "Đất 5×20 m", "1 trệt 2 lầu", "Trọn gói"]:
        q = next_question(s)
        assert q is not None
        assert len([x for x in q.slots if x != "district"]) <= 2
        assert "anh/chị" in q.text.lower()
        assert q.quick_replies.options
        s = extract(msg, s, expected_slots=q.slots).slots
    assert next_question(s) is None


def test_full_sentence_no_question_and_summary_complete():
    s = extract("nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô ngân sách 2 tỷ", Slots()).slots
    assert next_question(s) is None
    text, block = build_summary(s)
    assert isinstance(block, SummaryConfirmBlock)
    items = {i.label: i.value for i in block.items}
    assert items == {
        "Loại công trình": "Nhà phố",
        "Khu vực": "TP. Thủ Đức, TP. Hồ Chí Minh",
        "Diện tích đất": "5 × 20 m (100 m²)",
        "Số tầng": "1 trệt 3 lầu (4 tầng)",
        "Phạm vi": "Xây thô",
        "Mức hoàn thiện": "Trung bình (mặc định)",
        "Ngân sách": "2 tỷ",
    }
    assert text.startswith("Em tóm tắt lại: Nhà phố tại TP. Thủ Đức, TP. Hồ Chí Minh, đất 5 × 20 m (100 m²)")
    assert "ngân sách 2 tỷ" in text
    assert any("Móng cọc" in a for a in block.assumptions)  # 4 tầng nổi -> móng cọc (mục 6.2)
    assert any("Mái bê tông cốt thép" in a for a in block.assumptions)
    assert any("Diện tích xây dựng mỗi tầng bằng diện tích đất (100 m²)" in a for a in block.assumptions)


def test_summary_optional_fields_and_mezzanine():
    s = extract("biệt thự 10x20 Quận 7, 1 trệt 1 lửng 2 lầu, có tầng hầm, mái ngói, móng cọc, trọn gói, cao cấp",
                Slots()).slots
    _, block = build_summary(s)
    items = {i.label: i.value for i in block.items}
    assert items["Số tầng"] == "1 trệt 1 lửng 2 lầu (3 tầng + lửng)"
    assert items["Mức hoàn thiện"] == "Cao cấp"
    assert items["Tầng hầm"] == "Có"
    assert items["Mái"] == "Mái ngói đổ BTCT"
    assert items["Móng"] == "Móng cọc"
    assert not any("Móng" in a for a in block.assumptions)
    assert not any("Mức hoàn thiện" in a for a in block.assumptions)


def test_summary_area_only_and_one_floor():
    s = Slots(building_type="nha_pho", province="Hà Nội", land_area_m2=80, floors=1, work_scope="hoan_thien")
    _, block = build_summary(s)
    items = {i.label: i.value for i in block.items}
    assert items["Diện tích đất"] == "80 m²"
    assert items["Số tầng"] == "1 trệt (1 tầng)"
    assert any("Móng băng" in a for a in block.assumptions)


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
    assert labels(q.quick_replies) == ["Dưới 1,5 tỷ", "1,5–2,5 tỷ", "2,5–4 tỷ", "Trên 4 tỷ"]
    assert budget_question(s.model_copy(update={"budget_vnd": 2_000_000_000})) is None
    # Đã hỏi (khách chưa trả lời con số) -> không hỏi lại.
    s2 = extract("chưa biết nữa", s, expected_slots=q.slots).slots
    assert budget_question(s2) is None


# --- value của mỗi quick reply phải được extract() hiểu đúng --------------------

QUICK_REPLY_CASES = [
    (dialog.BUILDING_TYPE_REPLIES, ["building_type"], "building_type",
     ["nha_pho", "biet_thu", "can_ho", "van_phong", "khach_san", "khac"]),
    (dialog.PROVINCE_REPLIES, ["province", "district"], "province", [HCM, "Hà Nội", "Đà Nẵng"]),
    (dialog.LAND_REPLIES, ["land_area_m2", "floors"], "land_area_m2", [64, 100]),
    (dialog.FLOORS_REPLIES, ["land_area_m2", "floors"], "floors", [3, 4]),
    (dialog.WORK_SCOPE_REPLIES, ["work_scope"], "work_scope", ["xay_tho", "hoan_thien", "tron_goi", "noi_that"]),
    (dialog.QUALITY_TIER_REPLIES, ["quality_tier"], "quality_tier", ["tiet_kiem", "trung_binh", "kha", "cao_cap"]),
    (dialog.BUDGET_REPLIES, ["budget_vnd"], "budget_vnd",
     [1_500_000_000, 2_500_000_000, 4_000_000_000, 4_000_000_000]),
    (dialog.START_TIME_REPLIES, ["start_time"], "start_time", ["Trong 3 tháng", "3–6 tháng", "Chưa xác định"]),
]


@pytest.mark.parametrize("replies,expected_slots,slot,values", QUICK_REPLY_CASES)
def test_quick_reply_values_understood(replies, expected_slots, slot, values):
    assert len(replies) == len(values)
    for reply, value in zip(replies, values, strict=True):
        u = extract(reply.value, Slots(), expected_slots=expected_slots)
        assert getattr(u.slots, slot) == value, reply.value
        assert slot in u.changed


@pytest.mark.parametrize("replies", [dialog.BUILDING_TYPE_REPLIES, dialog.WORK_SCOPE_REPLIES, dialog.PROVINCE_REPLIES,
                                     dialog.BUDGET_REPLIES, dialog.FLOORS_REPLIES, dialog.LAND_REPLIES])
def test_quick_reply_values_work_without_expected_slots(replies):
    # Kể cả nút "Khác" (value "Công trình khác") — hiểu được cả ngay sau lời chào khi chưa có expected_slots.
    for reply in replies:
        assert extract(reply.value, Slots()).changed, reply.value


def test_summary_buttons_understood():
    s = Slots(building_type="nha_pho", province=HCM, land_area_m2=100, floors=4, work_scope="xay_tho")
    _, block = build_summary(s)
    assert extract(block.confirm.value, s).intent == "confirm"
    assert extract(block.edit.value, s).intent == "edit"
