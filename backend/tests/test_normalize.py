"""Unit test chuẩn hóa slot (đặc tả mục 5.3) — mỗi mẫu một test."""
import pytest

from app.ai.normalize import (
    detect_intent,
    fold,
    parse_budget,
    parse_building_type,
    parse_dimensions,
    parse_floors,
    parse_location,
    parse_message,
    parse_quality_tier,
    parse_start_time,
    parse_structure,
    parse_vn_number,
    parse_work_scope,
    province_of_district,
)

HCM = "TP. Hồ Chí Minh"


def test_fold_keeps_length_and_removes_marks():
    s = "Nhà phố 5×20 m² ở Thủ Đức – TP.HCM"
    f = fold(s)
    assert f == "nha pho 5x20 m2 o thu duc - tp.hcm"
    assert len(f) == len(s)


@pytest.mark.parametrize("s,expected", [
    ("4,5", 4.5), ("2.000", 2000), ("1.500.000", 1_500_000), ("2.5", 2.5), ("20", 20), ("1.234,5", 1234.5),
])
def test_parse_vn_number(s, expected):
    assert parse_vn_number(s) == expected


# --- Diện tích ---------------------------------------------------------------

@pytest.mark.parametrize("text,w,length,area", [
    ("5x20", 5, 20, 100),
    ("5 x 20m", 5, 20, 100),
    ("5×20 m", 5, 20, 100),
    ("5m x 20m", 5, 20, 100),
    ("đất 4*16", 4, 16, 64),
    ("ngang 5 dài 20", 5, 20, 100),
    ("Ngang 5m, dài 20m", 5, 20, 100),
    ("rộng 4,5m dài 18m", 4.5, 18, 81),
    ("chiều ngang 6 m chiều dài 15 m", 6, 15, 90),
    ("lô đất 4.5x20", 4.5, 20, 90),
])
def test_parse_dimensions_width_length(text, w, length, area):
    d = parse_dimensions(text)
    assert d["land_width_m"] == w
    assert d["land_length_m"] == length
    assert d["land_area_m2"] == area


@pytest.mark.parametrize("text,area", [
    ("100m2", 100), ("100 m²", 100), ("100 m2", 100), ("diện tích 120 mét vuông", 120),
    ("đất 80,5 m2", 80.5), ("diện tích đất 150", 150), ("rộng 100m2", 100),
])
def test_parse_dimensions_area_only(text, area):
    d = parse_dimensions(text)
    assert d["land_area_m2"] == area
    assert "land_width_m" not in d


def test_building_area_separate_from_land():
    d = parse_dimensions("đất 100m2, diện tích xây dựng 80m2")
    assert d["land_area_m2"] == 100
    assert d["building_area_m2"] == 80


def test_price_per_m2_is_not_area():
    assert parse_dimensions("đơn giá 5 triệu/m2") == {}


# --- Số tầng & kết cấu -------------------------------------------------------

@pytest.mark.parametrize("text,floors", [
    ("1 trệt 3 lầu", 4),
    ("1 trệt 2 lầu", 3),
    ("3 tầng", 3),
    ("nhà 4 tầng", 4),
    ("trệt + 2 lầu", 3),
    ("1 trệt", 1),
    ("nhà trệt", 1),
    ("một trệt hai lầu", 3),
    ("3 lầu", 4),  # quy ước: "N lầu" = 1 trệt + N lầu
    ("1 hầm 1 trệt 3 lầu", 4),
    ("nhà cấp 4", 1),
])
def test_parse_floors(text, floors):
    assert parse_floors(text)["floors"] == floors


def test_parse_floors_mezzanine():
    d = parse_floors("1 trệt 1 lửng 2 lầu")
    assert d == {"floors": 3, "has_mezzanine": True}


def test_parse_floors_no_mezzanine():
    assert parse_floors("4 tầng, không có lửng")["has_mezzanine"] is False


def test_basement_not_counted_as_floor():
    assert parse_floors("2 tầng hầm") == {}


@pytest.mark.parametrize("text,expected", [
    ("có tầng hầm", {"basement": True}),
    ("1 hầm", {"basement": True}),
    ("không có hầm", {"basement": False}),
    ("hầm sâu 1,5m", {"basement": True, "basement_depth_m": 1.5}),
    ("có sân thượng", {"rooftop_terrace": "open"}),
    ("sân thượng có mái che", {"rooftop_terrace": "covered"}),
    ("không sân thượng", {"rooftop_terrace": "none"}),
    ("mái ngói", {"roof_type": "roof_tile_on_concrete"}),
    ("mái ngói kèo thép", {"roof_type": "roof_tile_on_truss"}),
    ("mái tôn", {"roof_type": "roof_metal"}),
    ("mái bằng", {"roof_type": "roof_concrete"}),
    ("mái BTCT", {"roof_type": "roof_concrete"}),
    ("móng cọc", {"foundation_type": "foundation_pile"}),
    ("móng băng", {"foundation_type": "foundation_strip"}),
    ("móng đơn", {"foundation_type": "foundation_single"}),
])
def test_parse_structure(text, expected):
    assert parse_structure(text) == expected


# --- Tiền --------------------------------------------------------------------

@pytest.mark.parametrize("text,vnd", [
    ("2 tỷ", 2_000_000_000),
    ("2 tỉ", 2_000_000_000),
    ("2.000 triệu", 2_000_000_000),
    ("2,5 tỷ", 2_500_000_000),
    ("1 tỷ 8", 1_800_000_000),
    ("1 tỷ 800 triệu", 1_800_000_000),
    ("1 tỷ rưỡi", 1_500_000_000),
    ("850 triệu", 850_000_000),
    ("850tr", 850_000_000),
    ("Dưới 1,5 tỷ", 1_500_000_000),
    ("1,5–2,5 tỷ", 2_500_000_000),
    ("2,5-4 tỷ", 4_000_000_000),
    ("từ 800 triệu đến 1 tỷ", 1_000_000_000),
    ("Trên 4 tỷ", 4_000_000_000),
    ("ngân sách 2 tỷ", 2_000_000_000),
    ("2.000.000.000 đồng", 2_000_000_000),
    ("ngân sách khoảng 3", 3_000_000_000),
])
def test_parse_budget(text, vnd):
    assert parse_budget(text) == vnd


@pytest.mark.parametrize("text", ["5 triệu/m2", "4,2 triệu một m2", "1 trệt 3 lầu", "5x20", "100m2"])
def test_parse_budget_ignores_non_budget(text):
    assert parse_budget(text) is None


def test_budget_not_confused_with_area_or_floors():
    d = parse_message("đất 5x20, 2 tầng, 2 tỷ")
    assert d["land_area_m2"] == 100
    assert d["floors"] == 2
    assert d["budget_vnd"] == 2_000_000_000


# --- Vị trí ------------------------------------------------------------------

@pytest.mark.parametrize("text", ["Sài Gòn", "HCM", "TPHCM", "tp.hcm", "TP. HCM", "Hồ Chí Minh", "TP. Hồ Chí Minh",
                                  "thành phố hồ chí minh", "saigon"])
def test_parse_location_hcm(text):
    assert parse_location(text) == {"province": HCM}


@pytest.mark.parametrize("text,province", [
    ("Hà Nội", "Hà Nội"), ("HN", "Hà Nội"), ("ha noi", "Hà Nội"), ("Đà Nẵng", "Đà Nẵng"), ("da nang", "Đà Nẵng"),
    ("Bình Dương", "Bình Dương"), ("Đồng Nai", "Đồng Nai"), ("Hải Phòng", "Hải Phòng"), ("Cần Thơ", "Cần Thơ"),
    ("Vũng Tàu", "Bà Rịa - Vũng Tàu"), ("Khánh Hòa", "Khánh Hòa"), ("tỉnh Vĩnh Long", "Vĩnh Long"),
])
def test_parse_location_province(text, province):
    assert parse_location(text)["province"] == province


@pytest.mark.parametrize("text,district,province", [
    ("Thủ Đức", "TP. Thủ Đức", HCM),
    ("ở thu duc", "TP. Thủ Đức", HCM),
    ("Quận 7", "Quận 7", HCM),
    ("q7", "Quận 7", HCM),
    ("Bình Thạnh", "Bình Thạnh", HCM),
    ("Gò Vấp", "Gò Vấp", HCM),
    ("Cầu Giấy", "Cầu Giấy", "Hà Nội"),
    ("Hoàn Kiếm", "Hoàn Kiếm", "Hà Nội"),
    ("Hải Châu", "Hải Châu", "Đà Nẵng"),
    ("Biên Hòa", "TP. Biên Hòa", "Đồng Nai"),
])
def test_parse_location_district_infers_province(text, district, province):
    assert parse_location(text) == {"district": district, "province": province}


def test_location_false_positives():
    assert parse_location("tính giá lại giúp tôi") == {}
    assert parse_location("nhà bé thôi") == {}
    assert parse_location("Tôi muốn sửa lại thông tin") == {}


def test_province_of_district():
    assert province_of_district("TP. Thủ Đức") == HCM
    assert province_of_district("Quận 3") == HCM
    assert province_of_district("Cầu Giấy") == "Hà Nội"
    assert province_of_district("Không rõ") is None


# --- Loại công trình / phạm vi / chất lượng / thời gian -----------------------

@pytest.mark.parametrize("text,value", [
    ("nhà phố", "nha_pho"), ("Nhà ống", "nha_pho"), ("biệt thự", "biet_thu"), ("villa", "biet_thu"),
    ("căn hộ", "can_ho"), ("chung cư", "can_ho"), ("văn phòng", "van_phong"), ("khách sạn", "khach_san"),
])
def test_parse_building_type(text, value):
    assert parse_building_type(text) == value


@pytest.mark.parametrize("text,value", [
    ("xây thô", "xay_tho"), ("phần thô", "xay_tho"), ("hoàn thiện", "hoan_thien"), ("trọn gói", "tron_goi"),
    ("chìa khóa trao tay", "tron_goi"), ("nội thất", "noi_that"), ("xây thô và hoàn thiện", "tron_goi"),
])
def test_parse_work_scope(text, value):
    assert parse_work_scope(text) == value


def test_quality_phrase_is_not_scope():
    assert parse_work_scope("mức hoàn thiện khá") is None
    assert parse_quality_tier("mức hoàn thiện khá") == "kha"


@pytest.mark.parametrize("text,value", [
    ("tiết kiệm", "tiet_kiem"), ("trung bình", "trung_binh"), ("mức khá", "kha"), ("Khá", "kha"),
    ("cao cấp", "cao_cap"), ("vật tư khá", "kha"),
])
def test_parse_quality_tier(text, value):
    assert parse_quality_tier(text) == value


def test_kha_as_adverb_is_not_quality():
    assert parse_quality_tier("đất khá rộng") is None


@pytest.mark.parametrize("text,value", [
    ("Khởi công trong 3 tháng", "Trong 3 tháng"),
    ("3-6 tháng nữa", "3–6 tháng"),
    ("Thời gian khởi công chưa xác định", "Chưa xác định"),
    ("dự kiến khởi công sau Tết", "Sau Tết"),
    ("muốn xây vào tháng 3 năm 2027", "Tháng 3 năm 2027"),
])
def test_parse_start_time(text, value):
    assert parse_start_time(text) == value


# --- Ý định ------------------------------------------------------------------

@pytest.mark.parametrize("text", ["Đúng rồi", "đúng rồi, tính khái toán giúp tôi", "ok", "OK em", "Chuẩn",
                                  "xác nhận", "Đồng ý", "dạ đúng", "chính xác"])
def test_intent_confirm(text):
    assert detect_intent(text) == "confirm"


@pytest.mark.parametrize("text", ["Sửa lại", "Tôi muốn sửa lại thông tin", "không đúng", "chưa đúng",
                                  "đổi sang 4 tầng", "đổi lại ngân sách"])
def test_intent_edit(text):
    assert detect_intent(text) == "edit"


@pytest.mark.parametrize("text", ["Đơn giá bao nhiêu?", "khái toán gồm những gì", "nhà thầu nào phù hợp",
                                  "có bao gồm nội thất không"])
def test_intent_question(text):
    assert detect_intent(text) == "question"


@pytest.mark.parametrize("text", ["thời tiết hôm nay", "kể chuyện bóng đá đi", "mua cổ phiếu gì"])
def test_intent_out_of_scope(text):
    assert detect_intent(text) == "out_of_scope"


def test_intent_none_for_plain_info():
    assert detect_intent("Sài Gòn") is None
    assert detect_intent("sửa chữa nhà phố") is None


# --- Tổng hợp ----------------------------------------------------------------

def test_parse_message_full_sentence():
    d = parse_message("nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô ngân sách 2 tỷ")
    assert d == {
        "building_type": "nha_pho", "district": "TP. Thủ Đức", "province": HCM, "land_width_m": 5,
        "land_length_m": 20, "land_area_m2": 100, "floors": 4, "work_scope": "xay_tho",
        "budget_vnd": 2_000_000_000,
    }


# --- Review: lỗi đã sửa --------------------------------------------------------

@pytest.mark.parametrize("text,vnd", [("1ty5", 1_500_000_000), ("1tỷ5", 1_500_000_000), ("2ti", 2_000_000_000)])
def test_parse_budget_unit_glued_to_digits(text, vnd):
    assert parse_budget(text) == vnd


def test_parse_budget_unit_not_matched_inside_word():
    assert parse_budget("xây 2 tiếng") is None


@pytest.mark.parametrize("text", ["không", "Không ạ", "ko", "chưa", "sai", "Đổi số tầng", "doi so tang"])
def test_intent_bare_no_or_doi_is_edit(text):
    assert detect_intent(text) == "edit"


@pytest.mark.parametrize("text", ["đợi chút", "Đợi em xíu"])
def test_intent_doi_wait_is_not_edit(text):
    assert detect_intent(text) != "edit"


@pytest.mark.parametrize("text", ["Công trình khác", "loại công trình khác", "Nhà khác ạ"])
def test_parse_building_type_other_whole_message(text):
    assert parse_building_type(text) == "khac"


def test_parse_building_type_other_not_from_sentence():
    assert parse_building_type("tôi đã xem công trình khác của nhà thầu") is None
