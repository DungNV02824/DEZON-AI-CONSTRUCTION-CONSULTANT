"""Slot filling: trích + gộp + ý định + LLM tùy chọn (đặc tả mục 5.3, nghiệm thu 13)."""
from typing import Any

from app.ai.slot_filling import EXTRACTION_JSON_SCHEMA, SlotUpdate, extract, normalize_raw_slots, update_slots
from app.schemas.slots import Slots

HCM = "TP. Hồ Chí Minh"


def test_acceptance_full_sentence_no_missing():
    u = extract("nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô ngân sách 2 tỷ", Slots())
    s = u.slots
    assert s.building_type == "nha_pho"
    assert s.district == "TP. Thủ Đức"
    assert s.province == HCM
    assert (s.land_width_m, s.land_length_m, s.land_area_m2) == (5, 20, 100)
    assert s.floors == 4
    assert s.work_scope == "xay_tho"
    assert s.budget_vnd == 2_000_000_000
    assert u.missing_required == []
    assert u.intent == "provide_info"
    assert set(u.changed) >= {"building_type", "province", "district", "land_area_m2", "floors", "work_scope",
                              "budget_vnd"}


def test_spec_sentence_only_missing_work_scope():
    u = extract("Tôi muốn xây nhà phố 5x20 ở Thủ Đức, 1 trệt 3 lầu, ngân sách 2 tỷ", Slots())
    assert u.missing_required == ["work_scope"]
    assert u.slots.budget_vnd == 2_000_000_000


def test_incremental_merge_keeps_previous():
    s = extract("Tôi muốn xây nhà phố", Slots()).slots
    s = extract("TP. Hồ Chí Minh", s).slots
    u = extract("5x20, 1 trệt 3 lầu", s)
    assert u.slots.building_type == "nha_pho"
    assert u.slots.province == HCM
    assert u.slots.floors == 4
    assert u.missing_required == ["work_scope"]


# --- Câu trả lời ngắn theo slot đang hỏi --------------------------------------

def test_short_answer_floors():
    u = extract("4", Slots(building_type="nha_pho", province=HCM, land_area_m2=100), expected_slots=["floors"])
    assert u.slots.floors == 4


def test_short_answer_land_and_floors_combined_question():
    u = extract("100", Slots(), expected_slots=["land_area_m2", "floors"])
    assert u.slots.land_area_m2 == 100 and u.slots.floors is None
    u = extract("3", Slots(), expected_slots=["land_area_m2", "floors"])
    assert u.slots.floors == 3 and u.slots.land_area_m2 is None
    u = extract("5 20", Slots(), expected_slots=["land_area_m2"])
    assert (u.slots.land_width_m, u.slots.land_length_m, u.slots.land_area_m2) == (5, 20, 100)


def test_short_answer_other_building_type():
    u = extract("Khác", Slots(), expected_slots=["building_type"])
    assert u.slots.building_type == "khac"


def test_short_answer_other_province_sets_nothing():
    u = extract("Khác", Slots(building_type="nha_pho"), expected_slots=["province", "district"])
    assert u.slots.province is None
    assert u.missing_required[0] == "province"


def test_short_answer_unknown_province_kept_as_typed():
    u = extract("bình phước", Slots(), expected_slots=["province"])
    assert u.slots.province == "Bình Phước"  # có trong danh mục
    u = extract("Tỉnh Mới", Slots(), expected_slots=["province"])
    assert u.slots.province == "Tỉnh Mới"


def test_short_answer_budget_and_scope():
    assert extract("2", Slots(), expected_slots=["budget_vnd"]).slots.budget_vnd == 2_000_000_000
    assert extract("850", Slots(), expected_slots=["budget_vnd"]).slots.budget_vnd == 850_000_000
    assert extract("thô", Slots(), expected_slots=["work_scope"]).slots.work_scope == "xay_tho"
    assert extract("chưa xác định", Slots(), expected_slots=["start_time"]).slots.start_time == "Chưa xác định"


def test_bare_number_without_expected_slot_is_ignored():
    u = extract("4", Slots())
    assert u.changed == []
    assert u.intent == "other"


def test_expected_slots_recorded_in_asked():
    u = extract("chưa có", Slots(), expected_slots=["budget_vnd"])
    assert u.slots.budget_vnd is None
    assert u.slots.asked == ["budget_vnd"]
    u2 = extract("ok", u.slots, expected_slots=["budget_vnd"])
    assert u2.slots.asked == ["budget_vnd"]


# --- Xác nhận / sửa ------------------------------------------------------------

FULL = dict(building_type="nha_pho", province=HCM, land_width_m=5, land_length_m=20, land_area_m2=100, floors=4,
            work_scope="xay_tho")


def test_confirm_when_complete_sets_confirmed():
    u = extract("Đúng rồi, tính khái toán giúp tôi", Slots(**FULL))
    assert u.intent == "confirm"
    assert u.slots.confirmed is True
    assert u.changed == []


def test_confirm_when_incomplete_does_not_confirm():
    u = extract("ok", Slots(building_type="nha_pho"))
    assert u.intent == "confirm"
    assert u.slots.confirmed is False


def test_change_after_confirm_resets_confirmed():
    s = Slots(**FULL, confirmed=True)
    u = extract("đổi sang 5 tầng", s)
    assert u.slots.floors == 5
    assert u.intent == "edit"
    assert u.changed == ["floors"]
    assert u.slots.confirmed is False


def test_overwriting_value_is_edit_even_without_edit_word():
    u = extract("1 trệt 2 lầu", Slots(**FULL))
    assert u.intent == "edit"
    assert u.slots.floors == 3


def test_edit_request_without_values_unconfirms():
    u = extract("Tôi muốn sửa lại thông tin", Slots(**FULL, confirmed=True))
    assert u.intent == "edit"
    assert u.slots.confirmed is False


def test_repeat_same_value_keeps_confirmed():
    u = extract("nhà phố", Slots(**FULL, confirmed=True))
    assert u.changed == []
    assert u.slots.confirmed is True


def test_question_and_out_of_scope_intents():
    assert extract("Khái toán gồm những gì?", Slots()).intent == "question"
    assert extract("thời tiết hôm nay thế nào", Slots()).intent == "out_of_scope"


def test_question_with_info_captures_info():
    u = extract("Xây nhà phố 5x20 hết bao nhiêu?", Slots())
    assert u.slots.building_type == "nha_pho"
    assert u.slots.land_area_m2 == 100
    assert u.intent == "provide_info"


# --- Nhất quán khi sửa --------------------------------------------------------

def test_change_province_drops_district_of_other_province():
    s = Slots(**{**FULL, "district": "TP. Thủ Đức"})
    u = extract("à không, xây ở Đà Nẵng", s)
    assert u.slots.province == "Đà Nẵng"
    assert u.slots.district is None
    assert "district" in u.changed


def test_new_area_drops_stale_dimensions():
    u = extract("đất 120m2", Slots(**FULL))
    assert u.slots.land_area_m2 == 120
    assert u.slots.land_width_m is None and u.slots.land_length_m is None


# --- LLM provider --------------------------------------------------------------

class FakeProvider:
    def __init__(self, result: dict[str, Any] | Exception, name: str = "fake"):
        self.name = name
        self.result = result
        self.calls: list[tuple[str, str, dict]] = []

    def chat(self, system, messages, tools, context=None):  # pragma: no cover - không dùng
        raise NotImplementedError

    def extract(self, system: str, text: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, text, schema))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _llm(**kw) -> dict[str, Any]:
    base = {k: None for k in EXTRACTION_JSON_SCHEMA["properties"]}
    base["intent"] = "provide_info"
    base.update(kw)
    return base


def test_json_schema_is_strict():
    assert EXTRACTION_JSON_SCHEMA["additionalProperties"] is False
    assert set(EXTRACTION_JSON_SCHEMA["required"]) == set(EXTRACTION_JSON_SCHEMA["properties"])


def test_llm_fills_fields_rules_missed_and_is_normalized_by_code():
    msg = "Mình định làm cái nhà mặt đất rộng chừng 5 mét, sâu 20 mét, ở khu Sài Gòn"
    p = FakeProvider(_llm(building_type="nha_pho", province="Sài Gòn", land_width_m=5, land_length_m=20,
                          land_area_m2=999))
    u = extract(msg, Slots(), provider=p)
    assert p.calls and p.calls[0][2] is EXTRACTION_JSON_SCHEMA
    assert u.llm_used is True
    assert u.slots.province == HCM  # chuẩn hóa bằng code
    assert u.slots.land_area_m2 == 100  # tính bằng code, bỏ diện tích LLM
    assert u.slots.building_type == "nha_pho"


def test_rule_based_wins_over_llm():
    p = FakeProvider(_llm(floors=7, floors_text="6 lầu", budget_text="9 tỷ", province="Hà Nội"))
    u = extract("1 trệt 3 lầu, ngân sách 2 tỷ, ở Sài Gòn", Slots(), provider=p)
    assert u.slots.floors == 4
    assert u.slots.budget_vnd == 2_000_000_000
    assert u.slots.province == HCM


def test_llm_hallucinated_numbers_rejected():
    p = FakeProvider(_llm(land_area_m2=150, floors=5, budget_text=None))
    u = extract("nhà phố nha em", Slots(), provider=p)
    assert u.slots.land_area_m2 is None
    assert u.slots.floors is None


def test_llm_budget_only_from_text_parsed_by_code():
    p = FakeProvider(_llm(budget_text="tầm hai tỷ"))  # code không parse được chữ -> bỏ qua
    assert extract("ngân sách tầm hai tỷ", Slots(), provider=p).slots.budget_vnd is None
    p = FakeProvider(_llm(budget_text="2 tỷ rưỡi"))
    assert extract("ngân sách của em là 2 tỷ rưỡi", Slots(), provider=p).slots.budget_vnd == 2_500_000_000


def test_provider_error_falls_back_to_rules():
    p = FakeProvider(RuntimeError("timeout"))
    u = extract("nhà phố ở Hà Nội", Slots(), provider=p)
    assert u.llm_used is False
    assert u.slots.province == "Hà Nội"
    assert u.slots.building_type == "nha_pho"


def test_mock_provider_is_not_called():
    p = FakeProvider(_llm(province="Hà Nội"), name="mock")
    u = extract("nhà phố", Slots(), provider=p)
    assert p.calls == []
    assert u.slots.province is None


def test_llm_intent_used_when_rules_unsure():
    p = FakeProvider(_llm(intent="confirm"))
    u = extract("chốt vậy đi em", Slots(**FULL), provider=p)
    assert u.intent == "confirm"
    assert u.slots.confirmed is True


# --- Tool update_project_requirements ------------------------------------------

def test_update_slots_normalizes_partial():
    u = update_slots(Slots(), {"province": "HCM", "district": "thu duc", "floors": 4, "budget_vnd": 2_000_000_000,
                               "building_type": "nhà phố", "land_width_m": 5, "land_length_m": 20,
                               "work_scope": "xay_tho"})
    assert isinstance(u, SlotUpdate)
    assert u.slots.province == HCM and u.slots.district == "TP. Thủ Đức"
    assert u.slots.land_area_m2 == 100
    assert u.missing_required == []


def test_normalize_raw_slots_drops_invalid():
    assert normalize_raw_slots({"floors": 0, "work_scope": "abc", "roof_type": "xyz", "land_area_m2": -5}) == {}


# --- Review: lỗi đã sửa --------------------------------------------------------

def test_short_answer_decimal_kept():
    assert extract("1,5", Slots(), expected_slots=["budget_vnd"]).slots.budget_vnd == 1_500_000_000
    assert extract("2.5", Slots(), expected_slots=["budget_vnd"]).slots.budget_vnd == 2_500_000_000
    u = extract("62,5", Slots(), expected_slots=["land_area_m2"])
    assert u.slots.land_area_m2 == 62.5  # không bị hiểu thành 62 × 5
    assert u.slots.land_width_m is None


def test_short_answer_province_rejects_non_place_phrases():
    for msg in ["tôi chưa chọn được", "anh không rõ nữa", "để tôi hỏi vợ đã", "đợi chút"]:
        u = extract(msg, Slots(building_type="nha_pho"), expected_slots=["province", "district"])
        assert u.slots.province is None, msg
    assert extract("Phan Thiết", Slots(), expected_slots=["province"]).slots.province == "Phan Thiết"


def test_short_answer_start_time_rejects_non_time_phrases():
    u = extract("để tôi suy nghĩ đã", Slots(), expected_slots=["start_time"])
    assert u.slots.start_time is None
    assert extract("sau Tết", Slots(), expected_slots=["start_time"]).slots.start_time == "sau Tết"


def test_other_building_type_button_works_without_expected_slots():
    assert extract("Công trình khác", Slots()).slots.building_type == "khac"


def test_bare_no_after_summary_is_edit_and_unconfirms():
    u = extract("không", Slots(**FULL, confirmed=True))
    assert u.intent == "edit"
    assert u.slots.confirmed is False


def test_llm_cannot_invent_budget_floors_location_or_structure():
    p = FakeProvider(_llm(budget_text="2 tỷ", floors_text="1 trệt 3 lầu", province="Hà Nội", district="Cầu Giấy",
                          basement=True, has_mezzanine=True, roof_type="roof_metal", rooftop_terrace="open",
                          foundation_type="foundation_pile", building_type="khac"))
    u = extract("xin chào em", Slots(), provider=p)
    assert u.llm_used is True
    s = u.slots
    assert (s.budget_vnd, s.floors, s.province, s.district) == (None, None, None, None)
    assert (s.basement, s.has_mezzanine, s.roof_type, s.rooftop_terrace, s.foundation_type) == (None,) * 5
    assert s.building_type is None
    assert u.changed == []


def test_llm_word_numbers_accepted_when_present_in_message():
    p = FakeProvider(_llm(budget_text="2 tỷ"))
    assert extract("nhà phố hai tỷ", Slots(), provider=p).slots.budget_vnd == 2_000_000_000
    p = FakeProvider(_llm(land_width_m=5, land_length_m=20))
    u = extract("đất ngang năm mét, dài hai mươi mét", Slots(), provider=p)
    assert (u.slots.land_width_m, u.slots.land_length_m, u.slots.land_area_m2) == (5, 20, 100)
    # "tầm" (gập ra "tam") không được coi là số 8.
    p = FakeProvider(_llm(budget_text="8 tỷ"))
    assert extract("ngân sách tầm vài tỷ", Slots(), provider=p).slots.budget_vnd is None


def test_llm_location_lowercase_case_sensitive_alias_accepted_when_in_message():
    p = FakeProvider(_llm(province="Long An"))
    assert extract("nhà ở long an", Slots(), provider=p).slots.province == "Long An"


def test_llm_structure_accepted_with_keyword_and_other_type_when_asked():
    p = FakeProvider(_llm(basement=True, roof_type="roof_tile_on_truss"))
    u = extract("làm thêm cái hầm, lợp ngói", Slots(), provider=p)
    assert u.slots.basement is True and u.slots.roof_type == "roof_tile_on_truss"
    p = FakeProvider(_llm(building_type="khac"))
    assert extract("nhà xưởng", Slots(), provider=p, expected_slots=["building_type"]).slots.building_type == "khac"
