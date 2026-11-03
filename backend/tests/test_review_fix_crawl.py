"""Review sửa lỗi — nhóm crawl (#19 sàn đơn giá theo loại, #21 tỉnh công trình suy từ tên)."""
from __future__ import annotations

import copy
import json

import pytest

from app.ingestion.crawler import parsers
from app.ingestion.crawler.runner import reclean_snapshot
from app.ingestion.parse_vi import clean_price_per_m2_range, parse_money_range
from app.ingestion.seed_snapshot import SNAPSHOT_PATH


# ------------------------------------------------------------------ #19 đơn giá
@pytest.mark.parametrize("text,kind,expected,reason", [
    # An Phú: cận dưới 0,5 tr xây thô vô lý -> bỏ cận dưới, giữ "tối đa 4,2 tr"
    ("Từ 0,5 đến 4,2 tr/m²", "rough_price", [None, 4_200_000], "implausible_min"),
    # KHP: "hoàn thiện cơ bản" (= trọn gói) 0,7–2 tr: cả cận trên dưới sàn -> bỏ hết
    ("Từ 0,7 đến 2 tr/m²", "finish_price", None, "implausible"),
    ("Từ 2 đến 4,3 tr/m²", "rough_price", [None, 4_300_000], "default_min"),
    ("Từ 2 đến 8 tr/m²", "finish_price", [None, 8_000_000], "default_min"),
    ("Từ 4,3 đến 4,7 tr/m²", "rough_price", [4_300_000, 4_700_000], None),
    ("Từ 3,8 đến 4 tr/m²", "rough_price", [3_800_000, 4_000_000], None),
    ("Từ 6 đến 7,5 tr/m²", "finish_price", [6_000_000, 7_500_000], None),
    ("Từ 2 tr/m²", "rough_price", None, "implausible"),  # xây thô 2 tr: dưới sàn
    ("Từ 2 đến 80 tr/m²", "finish_price", None, "implausible"),
    ("Từ 3 đến 5 tr/m²", "finish_price", [None, 5_000_000], "implausible_min"),
])
def test_clean_price_floor_by_kind(text, kind, expected, reason):
    assert clean_price_per_m2_range(parse_money_range(text), kind=kind) == (expected, reason)


def test_clean_price_without_kind_keeps_generic_behaviour():
    assert clean_price_per_m2_range(parse_money_range("Từ 0,5 đến 4,2 tr/m²")) == ([500_000, 4_200_000], None)
    assert clean_price_per_m2_range(parse_money_range("Từ 2 tr/m²")) == ([2_000_000, 2_000_000], None)


def _profile(spec: dict[str, str]) -> dict:
    return {"extra": {"spec": spec}}


def test_apply_spec_prices_drops_absurd_lower_bounds():
    rec = parsers.apply_spec_prices(_profile({"don gia tho": "Từ 0,5 đến 4,2 tr/m²",
                                              "don gia hoan thien co ban": "Từ 2 đến 7,5 tr/m²"}))
    assert (rec["rough_price_min"], rec["rough_price_max"]) == (None, 4_200_000)
    assert (rec["finish_price_min"], rec["finish_price_max"]) == (None, 7_500_000)
    assert rec["extra"]["implausible_min_prices"] == {"rough_price": "Từ 0,5 đến 4,2 tr/m²"}
    assert rec["extra"]["default_min_prices"] == {"finish_price": "Từ 2 đến 7,5 tr/m²"}


def test_apply_spec_prices_khp_finish_unavailable():
    rec = parsers.apply_spec_prices(_profile({"don gia tho": "Từ 2 đến 4,2 tr/m²",
                                              "don gia hoan thien co ban": "Từ 0,7 đến 2 tr/m²"}))
    assert (rec["rough_price_min"], rec["rough_price_max"]) == (None, 4_200_000)
    assert rec["finish_price_min"] is None and rec["finish_price_max"] is None
    assert rec["extra"]["implausible_prices"] == {"finish_price": "Từ 0,7 đến 2 tr/m²"}


def test_apply_spec_prices_finish_below_rough_is_dropped():
    """Trọn gói gồm cả phần thô: cận trên trọn gói < cận dưới xây thô -> trọn gói vô lý."""
    rec = parsers.apply_spec_prices(_profile({"don gia hoan thien co ban": "Từ 4 đến 4,2 tr/m²",
                                              "don gia tho": "Từ 4,3 đến 4,7 tr/m²"}))
    assert (rec["rough_price_min"], rec["rough_price_max"]) == (4_300_000, 4_700_000)
    assert rec["finish_price_min"] is None and rec["finish_price_max"] is None
    assert rec["extra"]["implausible_prices"] == {"finish_price": "Từ 4 đến 4,2 tr/m²"}


def test_apply_spec_prices_is_idempotent_and_ignores_placeholders():
    rec = _profile({"don gia tho": "Từ 4,2 đến 4,5 tr/m²", "don gia hoan thien co ban": "Không làm"})
    parsers.apply_spec_prices(rec)
    first = copy.deepcopy(rec)
    parsers.apply_spec_prices(rec)
    assert rec == first
    assert (rec["rough_price_min"], rec["rough_price_max"]) == (4_200_000, 4_500_000)
    assert rec["finish_price_min"] is None and rec["finish_price_max"] is None
    assert set(rec["extra"]) == {"spec"}


# ------------------------------------------------------------------ #21 tỉnh từ tên công trình
@pytest.mark.parametrize("name,expected", [
    ("Nhà phố số 30, Điện Biên Phủ, Ninh Hòa, Ninh Thạnh, Tây Ninh", ("Tây Ninh", None)),
    ("Biệt thự ấp Lập Khánh, Khánh Hòa, Tây Ninh", ("Tây Ninh", None)),
    ("Biệt thự Khánh Hòa Tây Ninh", ("Tây Ninh", None)),  # không có dấu phẩy: lần nhắc tỉnh cuối cùng
    ("Nhà phố đường Điện Biên Phủ, Quận 3", ("TP. Hồ Chí Minh", "Quận 3")),
    ("Nhà phố Điện Biên Phủ Bình Thạnh", ("TP. Hồ Chí Minh", "Bình Thạnh")),
    ("Nhà phố xã Khánh Hòa", (None, None)),  # tên xã, không phải tỉnh
    ("France Indochine 22 Hà Nội", ("Hà Nội", None)),
    ("Indochine Villa Quận 7 Sài gòn", ("TP. Hồ Chí Minh", "Quận 7")),
    ("Villa Hồ Tây, Đường Xuân Diệu, Ngõ 31, P Quảng An, Tây Hồ, Hà Nội", ("Hà Nội", "Tây Hồ")),
    ("Căn hộ dich vụ, đường Lê Lai, P Hạnh Thông, TO HCM", ("TP. Hồ Chí Minh", None)),
    ("Nhà thép Biên Hòa", ("Đồng Nai", "TP. Biên Hòa")),
    ("Biệt thự Điện Biên", ("Điện Biên", None)),  # tỉnh thật vẫn nhận
    ("The Opera Duplex", (None, None)),
])
def test_province_from_name(name, expected):
    assert parsers.province_from_name(name) == expected


def test_apply_location_from_name_ignores_street_and_commune_names():
    rec = parsers.apply_location_from_name({"name": "Nhà phố số 30, Điện Biên Phủ, Ninh Hòa, Ninh Thạnh, Tây Ninh",
                                            "province": "TP. Hồ Chí Minh", "district": None, "extra": {}})
    assert rec["province"] == "Tây Ninh" and rec["extra"]["location_source"] == "project_name"
    rec = parsers.apply_location_from_name({"name": "Nhà phố xã Khánh Hòa", "province": "TP. Hồ Chí Minh",
                                            "district": None, "extra": {}})
    assert rec["province"] == "TP. Hồ Chí Minh" and rec["extra"]["location_source"] == "page_meta"


def test_reapply_work_location_uses_meta_line_then_name():
    rec = {"name": "Biệt thự ấp Lập Khánh, Khánh Hòa, Tây Ninh", "province": "Khánh Hòa", "district": None,
           "extra": {"meta_line": "Công trình thực tế do Smartcons Tech thi công · TP Hồ Chí Minh · 8 ảnh ghi nhận "
                                  "từ hồ sơ khảo sát · Đăng ngày 19/10/2026", "location_source": "project_name"}}
    parsers.reapply_work_location(rec)
    assert rec["province"] == "Tây Ninh" and rec["extra"]["location_source"] == "project_name"
    rec = {"name": "The Opera Duplex", "province": "Hà Nội", "district": "Tây Hồ",
           "extra": {"meta_line": "Công trình thực tế do Kiến Phong thi công · TP. HCM · 8 ảnh"}}
    parsers.reapply_work_location(rec)
    assert (rec["province"], rec["district"]) == ("TP. Hồ Chí Minh", None)
    assert rec["extra"]["location_source"] == "page_meta"


# ------------------------------------------------------------------ snapshot đã làm sạch
def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def test_snapshot_is_already_recleaned():
    """Snapshot trong repo = kết quả của parser hiện hành (chạy lại không đổi gì)."""
    data = _snapshot()
    assert reclean_snapshot(copy.deepcopy(data)) == {"contractors": [], "projects": []}


def test_snapshot_records_fixed():
    data = _snapshot()
    by_slug = {c["slug"]: c for c in data["contractors"]}
    an_phu, khp = by_slug["an-phu"], by_slug["khp"]
    assert (an_phu["rough_price_min"], an_phu["rough_price_max"]) == (None, 4_200_000)
    assert khp["finish_price_min"] is None and khp["finish_price_max"] is None
    assert all(c.get("_source", {}).get("raw_hash") for c in data["contractors"])
    for c in data["contractors"]:
        for kind, floor in (("rough_price", 2_500_000), ("finish_price", 4_000_000)):
            assert all(v is None or v >= floor for v in (c[f"{kind}_min"], c[f"{kind}_max"])), c["slug"]
    provinces = {p["name"]: p["province"] for p in data["projects"]}
    assert provinces["Nhà phố số 30, Điện Biên Phủ, Ninh Hòa, Ninh Thạnh, Tây Ninh"] == "Tây Ninh"
    assert provinces["Biệt thự ấp Lập Khánh, Khánh Hòa, Tây Ninh"] == "Tây Ninh"
    assert "Điện Biên" not in provinces.values() and "Khánh Hòa" not in provinces.values()
