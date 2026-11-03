"""Parse số liệu tiếng Việt (đặc tả 11.2)."""
from datetime import date

import pytest

from app.ingestion.parse_vi import (
    HCM,
    HN,
    fold_vi,
    infer_building_type_from_name,
    is_placeholder,
    normalize_province,
    parse_area_m2,
    parse_building_types,
    parse_count,
    parse_date_vi,
    parse_decimal_vi,
    parse_int,
    parse_money,
    parse_money_range,
    parse_price_per_m2_range,
    parse_staff,
    parse_year,
    parse_years,
)


@pytest.mark.parametrize("text,expected", [
    ("4,3–4,7 triệu/m²", [4_300_000, 4_700_000]),
    ("4,3 - 4,7 triệu/m2", [4_300_000, 4_700_000]),
    ("4.3 - 4.7tr/m2", [4_300_000, 4_700_000]),
    ("Từ 4,3 đến 4,7 tr/m²", [4_300_000, 4_700_000]),  # định dạng thật trên hồ sơ Dezon
    ("Từ 7,3 đến 9,5 tr/m²", [7_300_000, 9_500_000]),
    ("7,3—9,5 triệu / m²", [7_300_000, 9_500_000]),
    ("3.500.000 - 4.000.000 đ/m²", [3_500_000, 4_000_000]),
    ("3.500.000đ/m2", [3_500_000, 3_500_000]),
    ("850 triệu – 1,2 tỷ", [850_000_000, 1_200_000_000]),
    ("7 – 15 tỷ / công trình", [7_000_000_000, 15_000_000_000]),
    ("1 tỷ 200 triệu", [1_200_000_000, 1_200_000_000]),
    ("2,058–2,352 tỷ", [2_058_000_000, 2_352_000_000]),
    ("500k", [500_000, 500_000]),
])
def test_parse_money_range(text, expected):
    assert parse_money_range(text) == expected


@pytest.mark.parametrize("text", ["Liên hệ", "Đang cập nhật", "liên hệ báo giá", "", "-", "N/A", None,
                                  "Chưa có dữ liệu", "Thỏa thuận"])
def test_placeholders_return_none(text):
    assert parse_money_range(text) is None
    assert parse_money(text) is None
    assert parse_int(text) is None
    assert parse_years(text) is None
    assert parse_staff(text) == (None, None)


def test_placeholder_detection():
    assert is_placeholder("Liên hệ")
    assert is_placeholder("  Đang cập nhật ...")
    assert not is_placeholder("8 năm")


def test_money_without_unit_is_not_guessed():
    assert parse_money_range("4,3") is None  # không có đơn vị -> không đoán triệu hay tỷ
    assert parse_price_per_m2_range("4,3 – 4,7") is None
    assert parse_money_range("4,3", default_unit=1_000_000) == [4_300_000, 4_300_000]


def test_parse_money_single_value():
    assert parse_money("25 tỷ/công trình") == 25_000_000_000
    assert parse_money("25 tỷ / công trình") == 25_000_000_000
    assert parse_money("7 – 15 tỷ / công trình") is None  # khoảng -> không tự chọn giá trị đại diện
    assert parse_money("7 – 15 tỷ / công trình", allow_range=True) == 7_000_000_000


def test_parse_staff():
    assert parse_staff("20 thợ, 12 văn phòng") == (20, 12)
    assert parse_staff("20 thợ · 12 văn phòng") == (20, 12)
    assert parse_staff("12 văn phòng, 20 thợ") == (20, 12)
    assert parse_staff("20 · 12") == (20, 12)
    assert parse_staff("Huy động theo công trình 100 - 300 người · 5") == (None, 5)
    assert parse_staff("15 thợ") == (15, None)


def test_years_and_counts():
    assert parse_years("8 năm") == 8
    assert parse_years("Công trình 8 năm kinh nghiệm") == 8
    assert parse_years("Hơn 10 năm") == 10
    assert parse_years("2018") is None  # năm dương lịch, không phải số năm kinh nghiệm
    assert parse_year("Năm thành lập: 2018") == 2018
    assert parse_year("được thành lập vào năm 2004, hoạt động") == 2004
    assert parse_year("8 năm") is None
    assert parse_count("7 công trình") == 7
    assert parse_count("7 dự án trên Dezon") == 7
    assert parse_count("5-7 công trình") is None
    assert parse_int("1.200 m²") == 1200


def test_decimal_conventions():
    assert parse_decimal_vi("4,3") == 4.3
    assert parse_decimal_vi("4.3") == 4.3
    assert parse_decimal_vi("1.500") == 1500
    assert parse_decimal_vi("1.500.000") == 1_500_000
    assert parse_decimal_vi("1.234,5") == 1234.5
    assert parse_decimal_vi("abc") is None


def test_area_and_date():
    assert parse_area_m2("600m2") == 600
    assert parse_area_m2("85,5 m²") == 85.5
    assert parse_area_m2("100 - 200 m2") is None
    assert parse_date_vi("Đăng ngày 22/10/2026") == date(2026, 10, 22)
    assert parse_date_vi("2026-10-31") == date(2026, 10, 31)
    assert parse_date_vi("31/02/2026") is None


@pytest.mark.parametrize("text,expected", [
    ("TP. HCM", HCM), ("TP Hồ Chí Minh", HCM), ("TP.HCM", HCM), ("Quận 7 TPHCM", HCM), ("HCM", HCM),
    ("Thành phố Hồ Chí Minh", HCM), ("TP. Thủ Đức", HCM), ("Q9", HCM), ("Quận Tân bình", HCM),
    ("TP. HCM.", HCM), ("Hà Nội", HN), ("TP Hà Nội", HN), ("Mai Hiên Đông Anh Hà nội", HN),
    ("VPGD: 94 Nam Đuống - Long Biên - Hà Nội", HN), ("Đồng Nai, Việt Nam", "Đồng Nai"),
    ("Vĩnh Long", "Vĩnh Long"), ("không có văn phòng", None), ("", None),
])
def test_normalize_province(text, expected):
    assert normalize_province(text) == expected


def test_building_types():
    assert parse_building_types("Nhà phố · Biệt thự · Căn hộ · Công trình khác") == \
        ["nha_pho", "biet_thu", "can_ho", "khac"]
    assert parse_building_types("Nhà phố, biệt thự, Khách sạn") == ["nha_pho", "biet_thu", "khach_san"]
    assert parse_building_types("Căn hộ dịch vụ") == ["can_ho"]
    assert infer_building_type_from_name("Indochine Villa Quận 7 Sài gòn") == "biet_thu"
    assert infer_building_type_from_name("The Opera Duplex") == "can_ho"
    assert infer_building_type_from_name("Nhà phố Khang Điền") == "nha_pho"
    assert infer_building_type_from_name("Crystal Galleria") is None


def test_fold_vi():
    assert fold_vi("Biệt Thự Đông Dương") == "biet thu dong duong"


# ------------------------------------------------------------------ review fixes
@pytest.mark.parametrize("text,expected", [
    ("3tr5/m2", [3_500_000, 3_500_000]),            # viết tắt: 3tr5 = 3,5 triệu (trước đây ra 3 triệu)
    ("4tr - 4tr5", [4_000_000, 4_500_000]),
    ("1ty2", [1_200_000_000, 1_200_000_000]),
    ("1 tỷ 200 triệu - 1 tỷ 500 triệu", [1_200_000_000, 1_500_000_000]),
    ("4,3 ~ 4,7 triệu/m²", [4_300_000, 4_700_000]),
    ("Từ 4,3 tới 4,7 triệu", [4_300_000, 4_700_000]),
])
def test_money_shorthand_and_range_connectors(text, expected):
    assert parse_money_range(text) == expected


def test_money_not_joined_without_range_connector():
    # hai giá không nối bằng dấu khoảng -> không ghép thành khoảng giả
    assert parse_money_range("4,3 triệu xây thô, 7 triệu hoàn thiện") == [4_300_000, 4_300_000]
    # số không đơn vị đứng trước số có đơn vị nhưng không phải khoảng -> không mượn đơn vị
    assert parse_money_range("120m2 x 5tr") is None


def test_zero_money_is_unavailable():
    assert parse_money_range("0 đ") is None
    assert parse_money("0 triệu/m²") is None


@pytest.mark.parametrize("rng,ok", [
    ([4_200_000, 4_800_000], True),
    ([7_500_000, 9_000_000], True),
    ([2_000_000, 80_000_000], False),  # vượt trần và lệch > 10 lần
    ([100_000, 300_000], False),  # quá thấp cho đơn giá/m²
    (None, False),
])
def test_plausible_price_per_m2_range(rng, ok):
    from app.ingestion.parse_vi import plausible_price_per_m2_range

    assert plausible_price_per_m2_range(rng) is ok


@pytest.mark.parametrize("text,expected,reason", [
    ("Từ 4,3 đến 4,7 tr/m²", [4_300_000, 4_700_000], None),
    ("Từ 2 đến 4,3 tr/m²", [None, 4_300_000], "default_min"),  # cận dưới mặc định của form
    ("Từ 2 đến 80 tr/m²", None, "implausible"),
    ("Từ 2 tr/m²", [2_000_000, 2_000_000], None),  # nhà thầu khai đúng 2 tr -> giữ
    ("Từ 4,3 tr/m²", [4_300_000, 4_300_000], None),
])
def test_clean_price_per_m2_range(text, expected, reason):
    from app.ingestion.parse_vi import clean_price_per_m2_range

    assert clean_price_per_m2_range(parse_money_range(text)) == (expected, reason)
