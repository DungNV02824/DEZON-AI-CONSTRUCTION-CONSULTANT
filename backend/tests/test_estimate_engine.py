"""Unit test Calculation Engine (đặc tả mục 6, nghiệm thu mục 13) — thuần, không cần DB."""
from datetime import date

import pytest

from app.engines.estimate import RULES_NOT_APPROVED_WARNING, build_disclaimer, calculate_estimate
from app.engines.estimate import rules as R
from app.evidence import SourceRef, VerificationStatus
from app.formatting import NO_DATA
from app.schemas.estimate import EstimateInput, RateData, RuleData, RuleSet

TODAY = date(2026, 11, 2)
HCM = "TP. Hồ Chí Minh"

# Bảng hệ số mục 6.2 (độc lập với seed để test không phụ thuộc DB).
RULES_6_2 = [
    ("floor_standard", "tang", "Tầng trệt / lầu (có mái che)", 1.00),
    ("mezzanine", "tang", "Tầng lửng", 1.00),
    ("foundation_single", "mong", "Móng đơn", 0.30),
    ("foundation_strip", "mong", "Móng băng", 0.50),
    ("foundation_pile", "mong", "Móng cọc (chưa gồm ép cọc)", 0.40),
    ("basement_lt_1_3", "ham", "Hầm sâu < 1,3 m", 1.50),
    ("basement_1_3_1_7", "ham", "Hầm sâu 1,3–1,7 m", 1.70),
    ("basement_1_7_2_0", "ham", "Hầm sâu 1,7–2,0 m", 2.00),
    ("basement_gt_2_0", "ham", "Hầm sâu > 2,0 m", 2.50),
    ("roof_metal", "mai", "Mái tôn", 0.30),
    ("roof_concrete", "mai", "Mái bê tông cốt thép", 0.50),
    ("roof_tile_on_truss", "mai", "Mái ngói kèo thép", 0.70),
    ("roof_tile_on_concrete", "mai", "Mái ngói đổ BTCT", 1.00),
    ("rooftop_covered", "san_thuong", "Sân thượng có mái che", 0.70),
    ("rooftop_open", "san_thuong", "Sân thượng không mái", 0.50),
    ("yard", "san", "Sân trước/sau", 0.50),
]


def make_rules(approved: bool = False, exclude: tuple[str, ...] = ()) -> RuleSet:
    src = SourceRef(label="Hệ số khái toán (test)")
    return RuleSet(version=1, rules={
        code: RuleData(code=code, group_name=g, label_vi=label, coefficient=k, base="x", approved=approved,
                       version=1, source=src)
        for code, g, label, k in RULES_6_2 if code not in exclude
    })


def make_rate(pmin: int = 4_200_000, pmax: int = 4_800_000, *, province: str = HCM, is_fallback: bool = False,
              status: VerificationStatus = VerificationStatus.reference, updated: date | None = date(2026, 10, 31),
              work_scope: str = "xay_tho", quality_tier: str = "trung_binh", building_type: str = "nha_pho",
              label: str = "Đơn giá tham khảo (test)") -> RateData:
    return RateData(id=7, building_type=building_type, province="*" if is_fallback else province,
                    work_scope=work_scope, quality_tier=quality_tier, price_min=pmin, price_max=pmax,
                    effective_from=date(2026, 1, 1), is_fallback=is_fallback, status=status,
                    source=SourceRef(label=label, updated_at=updated))


def make_input(**kw) -> EstimateInput:
    base = dict(building_type="nha_pho", province=HCM, land_area_m2=100, floors=4, work_scope="xay_tho")
    base.update(kw)
    return EstimateInput(**base)


def calc(inp=None, rules=None, rate="default", **kw):
    inp = inp or make_input(**kw)
    return calculate_estimate(inp, rules or make_rules(), make_rate() if rate == "default" else rate, today=TODAY)


def codes(res) -> list[str]:
    return [it.rule_code for it in res.items]


# --- Nghiệm thu mục 13 ------------------------------------------------------------------------

def test_acceptance_case_section_13():
    inp = make_input(foundation_type="foundation_pile", roof_type="roof_concrete")
    res = calc(inp)
    assert res.converted_area_m2 == 490
    assert res.cost_min == 2_058_000_000
    assert res.cost_max == 2_352_000_000
    assert res.cost.display == "2,058–2,352 tỷ"
    assert res.cost.value == [2_058_000_000, 2_352_000_000]
    assert res.rate.display == "4,2–4,8 triệu/m²"
    assert res.rate.value == [4_200_000, 4_800_000]
    assert res.rate_id == 7
    assert res.cost.source.label == "Đơn giá tham khảo (test)"
    assert codes(res) == ["foundation_pile"] + ["floor_standard"] * 4 + ["roof_concrete"]
    assert [it.label_vi for it in res.items[1:5]] == ["Tầng trệt", "Lầu 1", "Lầu 2", "Lầu 3"]
    pile = res.items[0]
    assert (pile.area_m2, pile.coefficient, pile.converted_m2) == (100, 0.4, 40)


def test_defaults_four_floors_use_pile_and_concrete_roof_and_are_recorded():
    res = calc()
    assert res.converted_area_m2 == 490
    assert res.input.foundation_type == "foundation_pile"
    assert res.input.roof_type == "roof_concrete"
    assert res.input.building_area_m2 == 100
    assert res.input.roof_area_m2 == 100
    joined = "\n".join(res.assumptions)
    assert "Diện tích xây dựng mỗi tầng = diện tích đất 100 m² (anh/chị chưa cung cấp)" in res.assumptions
    assert "Móng tính theo thông lệ: móng cọc cho nhà ≥ 4 tầng nổi" in joined
    assert "Mái: mái bê tông cốt thép (anh/chị chưa có thông tin loại mái, em tạm tính)" in res.assumptions
    assert not any("Mức chất lượng" in a for a in res.assumptions)  # không còn phân mức chất lượng
    assert "Không có tầng hầm (anh/chị chưa có thông tin, em tạm tính không có hầm)" in res.assumptions


def test_default_strip_foundation_for_three_floors():
    res = calc(floors=3)
    assert res.input.foundation_type == "foundation_strip"
    assert res.converted_area_m2 == 400  # 50 + 3×100 + 50
    assert any("móng băng cho nhà ≤ 3 tầng nổi" in a for a in res.assumptions)


def test_foundation_single_explicit_no_default_assumption():
    res = calc(floors=2, foundation_type="foundation_single")
    assert res.converted_area_m2 == 280  # 30 + 200 + 50
    assert not any(a.startswith("Móng:") for a in res.assumptions)


def test_width_length_and_building_area():
    res = calc(land_area_m2=None, land_width_m=5, land_length_m=20, building_area_m2=80, floors=3)
    assert res.input.land_area_m2 == 100
    assert res.converted_area_m2 == 40 + 240 + 40
    assert not any("diện tích đất" in a and "Diện tích xây dựng mỗi tầng" in a for a in res.assumptions)
    assert "Chưa tính sân trước/sau (anh/chị chưa cung cấp diện tích sân)" in res.assumptions


def test_building_area_larger_than_land_warns():
    res = calc(building_area_m2=120)
    assert any("lớn hơn diện tích đất" in w for w in res.warnings)


@pytest.mark.parametrize("depth,code,k", [
    (1.0, "basement_lt_1_3", 1.5),
    (1.29, "basement_lt_1_3", 1.5),
    (1.3, "basement_1_3_1_7", 1.7),
    (1.7, "basement_1_3_1_7", 1.7),
    (1.8, "basement_1_7_2_0", 2.0),
    (2.0, "basement_1_7_2_0", 2.0),
    (2.5, "basement_gt_2_0", 2.5),
])
def test_basement_rule_by_depth(depth, code, k):
    res = calc(basement=True, basement_depth_m=depth)
    assert code in codes(res)
    assert res.converted_area_m2 == pytest.approx(490 + 100 * k)
    assert any("Diện tích hầm = diện tích xây dựng 100 m²" in a for a in res.assumptions)


def test_basement_without_depth_uses_default_rule_and_given_area():
    res = calc(basement=True, basement_area_m2=80)
    assert R.DEFAULT_BASEMENT_RULE in codes(res)
    assert res.converted_area_m2 == 490 + 80 * 2.0
    assert any("chưa cho độ sâu" in a and "Hầm sâu 1,7–2,0 m" in a for a in res.assumptions)
    assert any("Tầng hầm" in d for d in res.cost_drivers)


def test_basement_inferred_from_depth():
    res = calc(basement=False, basement_depth_m=1.5)
    assert res.input.basement is True
    assert "basement_1_3_1_7" in codes(res)
    assert any("đã cung cấp độ sâu hoặc diện tích hầm" in a for a in res.assumptions)


def test_mezzanine_default_and_given_area():
    res = calc(floors=3, has_mezzanine=True)
    assert res.converted_area_m2 == 500
    assert any("Diện tích tầng lửng = diện tích xây dựng 100 m²" in a for a in res.assumptions)
    res2 = calc(floors=3, mezzanine_area_m2=60)
    assert res2.input.has_mezzanine is True
    assert res2.converted_area_m2 == 460
    assert any("Tầng lửng" in d for d in res2.cost_drivers)


def test_rooftop_covered_default_replaces_roof():
    res = calc(floors=3, rooftop_terrace="covered")
    assert "rooftop_covered" in codes(res)
    assert not any(c.startswith("roof_") for c in codes(res))
    assert res.converted_area_m2 == 50 + 300 + 70
    assert any("Sân thượng phủ toàn bộ mặt bằng mái" in a for a in res.assumptions)
    assert any("Sân thượng có mái che" in d for d in res.cost_drivers)


def test_rooftop_open_partial_area_roof_takes_remainder():
    res = calc(floors=3, rooftop_terrace="open", rooftop_area_m2=40)
    assert res.converted_area_m2 == 50 + 300 + 40 * 0.5 + 60 * 0.5
    assert res.input.roof_area_m2 == 60
    assert any("trừ sân thượng 40 m² = 60 m²" in a for a in res.assumptions)


def test_rooftop_with_explicit_roof_area():
    res = calc(floors=3, rooftop_terrace="open", roof_area_m2=30, roof_type="roof_metal")
    assert res.converted_area_m2 == 50 + 300 + 50 + 30 * 0.3


def test_roof_none_and_tile_roofs():
    res = calc(floors=3, roof_type="none")
    assert res.converted_area_m2 == 350
    assert "Không tính hạng mục mái theo thông tin anh/chị cung cấp" in res.assumptions
    tile = calc(floors=3, roof_type="roof_tile_on_concrete")
    assert tile.converted_area_m2 == 450
    assert any("Mái ngói" in d for d in tile.cost_drivers)
    truss = calc(floors=3, roof_type="roof_tile_on_truss")
    assert truss.converted_area_m2 == 420


def test_yard():
    res = calc(floors=3, building_area_m2=80, yard_area_m2=20)
    assert "yard" in codes(res)
    assert res.converted_area_m2 == 40 + 240 + 40 + 10
    assert not any("Chưa tính sân" in a for a in res.assumptions)


def test_no_rate_gives_unavailable_cost():
    res = calc(rate=None)
    assert res.cost_min is None and res.cost_max is None
    assert res.cost.display == NO_DATA and res.cost.status == "unavailable" and res.cost.value is None
    assert res.rate.display == NO_DATA and res.rate.status == "unavailable"
    assert res.converted_area_m2 == 490
    assert any("chưa thể khái toán chi phí" in w for w in res.warnings)
    # Không có đơn giá -> ghi chú không được nói "dựa trên đơn giá tham chiếu"
    assert res.disclaimer == (
        "Đây là khái toán sơ bộ: Dezon chưa có đủ đơn giá do nhà thầu công bố cho phạm vi này nên em chỉ tính diện "
        "tích sàn quy đổi, chưa tính được chi phí. Không phải báo giá thi công. "
        "Vui lòng yêu cầu báo giá từ nhà thầu để có con số chính xác.")
    assert "đơn giá tham chiếu" not in res.disclaimer
    assert res.rate_id is None


def test_fallback_rate_is_reference_with_assumption():
    rate = make_rate(is_fallback=True, status=VerificationStatus.verified,
                     label="Tổng hợp đơn giá xây thô do 5 nhà thầu nhà phố trên toàn quốc công bố trên Dezon")
    res = calc(make_input(province="Cần Thơ"), rules=make_rules(approved=True), rate=rate)
    assert res.rate.status == "reference"
    assert res.cost.status == "reference"
    assert ("Dùng đơn giá mặc định toàn quốc (tổng hợp đơn giá do các nhà thầu trên Dezon công bố) vì chưa đủ "
            "dữ liệu đơn giá nhà thầu tại Cần Thơ (cần ít nhất 3 hồ sơ nhà thầu có công bố giá)") in res.assumptions


def test_unapproved_rules_make_reference_and_warn():
    res = calc(rate=make_rate(status=VerificationStatus.verified))
    assert res.rules_status == "reference"
    assert RULES_NOT_APPROVED_WARNING in res.warnings
    assert res.rate.status == "verified"
    assert res.cost.status == "reference"


def test_approved_rules_status_is_weakest_of_rate_and_rules():
    res = calc(rules=make_rules(approved=True), rate=make_rate(status=VerificationStatus.verified))
    assert res.rules_status == "verified" and res.cost.status == "verified"
    assert RULES_NOT_APPROVED_WARNING not in res.warnings
    res2 = calc(rules=make_rules(approved=True), rate=make_rate(status=VerificationStatus.on_dezon))
    assert res2.cost.status == "on_dezon"


def test_missing_rule_is_skipped_with_warning():
    res = calc(rules=make_rules(exclude=("foundation_pile",)))
    assert "foundation_pile" not in codes(res)
    assert res.converted_area_m2 == 450
    assert any("foundation_pile" in w for w in res.warnings)


def test_empty_ruleset_cannot_estimate():
    res = calc(rules=RuleSet(version=0, rules={}))
    assert res.converted_area_m2 == 0
    assert res.cost_min is None and res.cost.status == "unavailable"
    assert res.rules_status == "reference"
    assert any("Chưa đủ hệ số" in w for w in res.warnings)


def test_stale_rate_warns():
    res = calc(rate=make_rate(updated=date(2025, 1, 1)))
    assert res.rate.is_stale is True and res.cost.is_stale is True
    assert any("01/01/2025" in w and "có thể đã cũ" in w for w in res.warnings)


def test_disclaimer_template():
    res = calc()
    assert res.disclaimer == (
        "Đây là khái toán sơ bộ dựa trên đơn giá tham chiếu (Đơn giá tham khảo (test), cập nhật 31/10/2026) "
        "và các giả định nêu trên, chưa tính điều kiện thực tế của khu đất. Không phải báo giá thi công. "
        "Vui lòng yêu cầu báo giá từ nhà thầu để có con số chính xác.")
    assert "ngày cập nhật: Chưa có dữ liệu" in build_disclaimer(SourceRef(label="X"))


def test_apartment_no_foundation_roof_basement():
    inp = make_input(building_type="can_ho", land_area_m2=75, floors=1, work_scope="hoan_thien",
                     basement=True, roof_type="roof_concrete", yard_area_m2=10, quality_tier="kha")
    res = calc(inp, rate=make_rate(work_scope="hoan_thien", building_type="can_ho"))
    assert codes(res) == ["floor_standard"]
    assert res.items[0].label_vi == "Sàn căn hộ"
    assert res.converted_area_m2 == 75
    assert any("Căn hộ: không tính móng" in a for a in res.assumptions)
    assert "Không tính tầng hầm vì nằm ngoài phạm vi khái toán này" in res.assumptions
    assert not any("Mức chất lượng" in a for a in res.assumptions)
    assert any("ban quản lý tòa nhà" in e for e in res.exclusions)
    assert not any("Mức chất lượng" in d for d in res.cost_drivers)
    assert any("thang máy" in d for d in res.cost_drivers)
    big = calc(make_input(building_type="can_ho", floors=5, work_scope="hoan_thien"))
    assert any("căn duplex" in w for w in big.warnings)
    assert big.items[1].label_vi == "Sàn căn hộ — tầng 2"


def test_interior_scope_only_floor_area():
    inp = make_input(work_scope="noi_that", floors=3, has_mezzanine=True, mezzanine_area_m2=50,
                     basement=True, rooftop_terrace="covered")
    res = calc(inp, rate=make_rate(2_000_000, 4_000_000, work_scope="noi_that"))
    assert set(codes(res)) == {"floor_standard", "mezzanine"}
    assert res.converted_area_m2 == 350
    assert res.input.foundation_type is None
    assert any("Phạm vi Nội thất" in a for a in res.assumptions)
    assert any("Phần xây thô và hoàn thiện" in e for e in res.exclusions)
    assert not any("ép cọc" in e for e in res.exclusions)


@pytest.mark.parametrize("scope,expected", [
    ("xay_tho", "Vật tư hoàn thiện"),
    ("hoan_thien", "Phần xây thô"),
    ("tron_goi", "Thiết bị đặc thù"),
])
def test_exclusions_by_scope(scope, expected):
    res = calc(work_scope=scope, rate=make_rate(work_scope=scope))
    assert any(expected in e for e in res.exclusions)
    assert any("giấy phép xây dựng" in e for e in res.exclusions)
    assert any("Nội thất rời" in e for e in res.exclusions)
    pile_excl = any("ép cọc" in e for e in res.exclusions)
    assert pile_excl == (scope != "hoan_thien")


def test_quality_tier_ignored_in_drivers():
    res = calc(work_scope="tron_goi", quality_tier="cao_cap", rate=make_rate(work_scope="tron_goi"))
    assert not any("Mức chất lượng" in d for d in res.cost_drivers)
    assert any("Hẻm nhỏ" in d for d in res.cost_drivers)
    assert any("Đất yếu" in d for d in res.cost_drivers)


def test_no_forbidden_wording_in_output():
    res = calc(basement=True, has_mezzanine=True, rooftop_terrace="covered", rate=None)
    text = " ".join(res.assumptions + res.exclusions + res.cost_drivers + res.warnings + [res.disclaimer])
    assert "tốt nhất" not in text.lower()


def test_rules_helpers():
    assert R.weakest_status() == "unavailable"
    assert R.weakest_status("verified", "on_dezon", "reference") == "reference"
    assert R.basement_rule_for_depth(None) == R.DEFAULT_BASEMENT_RULE
    assert R.default_foundation(3) == "foundation_strip"
    assert R.default_foundation(4) == "foundation_pile"


# --- Bổ sung sau review ------------------------------------------------------------------------

def test_apartment_with_pile_input_has_no_pile_exclusion():
    """Căn hộ không tính móng -> không được nhắc 'ép cọc' dù input có foundation_type."""
    inp = make_input(building_type="can_ho", floors=1, land_area_m2=80, foundation_type="foundation_pile")
    res = calc(inp, rate=make_rate(building_type="can_ho"))
    assert codes(res) == ["floor_standard"]
    assert not any("ép cọc" in e for e in res.exclusions)
    assert not any("cọc" in d for d in res.cost_drivers)


def test_finishing_scope_drivers_do_not_mention_rough_work():
    res = calc(work_scope="hoan_thien", basement=True, rate=make_rate(work_scope="hoan_thien"))
    text = " ".join(res.cost_drivers)
    assert "ép/khoan cọc" not in text and "đào đất" not in text and "gia cố móng" not in text
    assert "Tầng hầm được tính hệ số quy đổi cao hơn sàn thường" in res.cost_drivers
    assert any("giàn giáo" in d for d in res.cost_drivers)
    rough = calc(basement=True)
    assert any("đào đất" in d for d in rough.cost_drivers)
    assert any("ép/khoan cọc" in d for d in rough.cost_drivers)


def test_blank_province_label_in_fallback_and_no_rate():
    res = calc(make_input(province="  "), rate=make_rate(is_fallback=True))
    assert any(a.startswith("Dùng đơn giá mặc định toàn quốc") and "tại khu vực của anh/chị" in a
               for a in res.assumptions)
    none = calc(make_input(province=""), rate=None)
    assert any("tại khu vực của anh/chị" in w for w in none.warnings)


def test_rate_not_matching_input_warns_and_is_reference():
    res = calc(make_input(work_scope="tron_goi", quality_tier="kha"),
               rules=make_rules(approved=True), rate=make_rate(status=VerificationStatus.verified))
    warn = next(w for w in res.warnings if "không khớp thông tin công trình" in w)
    assert "phạm vi Xây thô" in warn and "mức" not in warn
    assert res.rate.status == "reference" and res.cost.status == "reference"
    ok = calc(rules=make_rules(approved=True), rate=make_rate(status=VerificationStatus.verified))
    assert not any("không khớp" in w for w in ok.warnings)
    # Mức chất lượng không còn được so khớp (đơn giá không phân mức)
    tier = calc(make_input(quality_tier="cao_cap"), rules=make_rules(approved=True),
                rate=make_rate(status=VerificationStatus.verified))
    assert not any("không khớp" in w for w in tier.warnings)
    assert ok.cost.status == "verified"


def test_finishing_scope_does_not_add_foundation_area():
    """C-F12: phạm vi Hoàn thiện không cộng hệ số móng (móng thuộc phần xây thô) và ghi rõ trong assumptions."""
    rough = calc(make_input(floors=4), rate=make_rate())
    fin = calc(make_input(floors=4, work_scope="hoan_thien"), rate=make_rate(work_scope="hoan_thien"))
    assert any(c.startswith("foundation") for c in codes(rough))
    assert not any(c.startswith("foundation") for c in codes(fin))
    assert fin.input.foundation_type is None
    assert not any("Móng: " in a for a in fin.assumptions)
    assert any("không tính diện tích móng" in a for a in fin.assumptions)
    assert any("Phần xây thô" in e for e in fin.exclusions)
    assert fin.converted_area_m2 < rough.converted_area_m2


@pytest.mark.parametrize("scope", ["hoan_thien", "noi_that"])
def test_scope_without_declared_prices_explains_and_suggests_other_scopes(scope):
    res = calc(make_input(work_scope=scope), rate=None)
    assert res.cost.display == NO_DATA and res.cost_min is None
    warn = next(w for w in res.warnings if "chưa công bố đơn giá" in w)
    assert "Dezon" in warn and "Xây thô hoặc Trọn gói" in warn and "mức" not in warn
    rough = calc(make_input(work_scope="xay_tho"), rate=None)
    assert not any("chưa công bố đơn giá" in w for w in rough.warnings)
    assert not any("mức " in w for w in rough.warnings)


def test_answered_no_basement_no_mezzanine_is_not_listed_as_assumption():
    """Khách đã trả lời "không hầm, không lửng" -> không ghi là "chưa có thông tin"; chưa trả lời -> ghi rõ."""
    answered = calc(basement=False, has_mezzanine=False)
    assert not any("tầng hầm" in a.lower() or "tầng lửng" in a.lower() for a in answered.assumptions)
    unknown = calc()
    assert "Không có tầng lửng (anh/chị chưa có thông tin, em tạm tính không có lửng)" in unknown.assumptions
    assert answered.converted_area_m2 == unknown.converted_area_m2
