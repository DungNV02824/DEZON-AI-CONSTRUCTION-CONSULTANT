"""Unit test gợi ý điều chỉnh ngân sách (đặc tả mục 6.5, nghiệm thu mục 13) — thuần, không DB."""
from app.engines.estimate import apply_changes, calculate_estimate, suggest_adjustments
from app.schemas.estimate import EstimateInput
from tests.test_estimate_engine import TODAY, make_input, make_rate, make_rules

# Đơn giá test theo (work_scope, quality_tier) — VND/m²
PRICES = {
    ("xay_tho", "tiet_kiem"): (3_200_000, 3_800_000),
    ("xay_tho", "trung_binh"): (3_550_000, 4_200_000),
    ("xay_tho", "kha"): (4_100_000, 4_850_000),
    ("tron_goi", "trung_binh"): (5_750_000, 7_150_000),
    ("tron_goi", "tiet_kiem"): (5_200_000, 6_450_000),
}


def lookup(inp: EstimateInput):
    p = PRICES.get((str(inp.work_scope), str(inp.quality_tier)))
    if p is None:
        return None
    return make_rate(*p, work_scope=str(inp.work_scope), quality_tier=str(inp.quality_tier))


def run(inp: EstimateInput, target: int, rules=None):
    rules = rules or make_rules()
    current = calculate_estimate(inp, rules, lookup(inp), today=TODAY)
    return current, suggest_adjustments(inp, rules, lookup, target, current, today=TODAY)


def by_code(options):
    return {o.code: o for o in options}


def test_typical_case_has_at_least_two_options_each_computed_by_engine():
    inp = make_input()
    current, options = run(inp, 1_200_000_000)
    assert current.cost_min == 1_740_000_000  # 490 × 3,55 triệu (làm tròn triệu)
    assert len(options) >= 2
    rules = make_rules()
    for o in options:
        if o.code == "combined":
            continue
        new_inp = apply_changes(inp, o.changes)
        expected = calculate_estimate(new_inp, rules, lookup(new_inp), today=TODAY)
        assert (o.cost_min, o.cost_max, o.converted_area_m2) == (
            expected.cost_min, expected.cost_max, expected.converted_area_m2)
        assert o.cost.display == expected.cost.display
        assert o.saving_min == current.cost_min - o.cost_min > 0
        assert o.fits_budget is (o.cost_max <= 1_200_000_000)
    mins = [o.cost_min for o in options if o.code != "combined"]
    assert mins == sorted(mins)
    codes = by_code(options)
    assert {"reduce_floor", "lower_quality", "change_roof"} <= set(codes)


def test_reduce_floor_recomputes_default_foundation():
    _, options = run(make_input(), 1_200_000_000)
    o = by_code(options)["reduce_floor"]
    assert o.changes == {"floors": 3}
    assert o.converted_area_m2 == 400  # móng mặc định chuyển sang băng
    assert "móng băng" in o.description


def test_explicit_foundation_kept_when_reducing_floor():
    _, options = run(make_input(foundation_type="foundation_pile"), 1_200_000_000)
    o = by_code(options)["reduce_floor"]
    assert o.converted_area_m2 == 390
    assert "Móng tính lại" not in o.description


def test_combined_option_when_no_single_option_fits():
    _, options = run(make_input(), 1_000_000_000)
    assert not any(o.fits_budget for o in options if o.code != "combined")
    combo = by_code(options).get("combined")
    assert combo is not None
    singles = [o for o in options if o.code != "combined"]
    assert combo.cost_min < min(o.cost_min for o in singles)
    assert combo.label_vi.startswith("Kết hợp:")
    # W-F9: phương án kết hợp đứng sau các phương án thành phần, mô tả nêu đúng tên hai phương án.
    assert options[-1].code == "combined"
    names = [o.label_vi for o in singles if o.label_vi in combo.description]
    assert len(names) == 2
    assert "hai phương án trên" not in combo.description


def test_quality_tier_names_lowercase_mid_sentence():
    _, options = run(make_input(), 1_000_000_000)
    lq = by_code(options)["lower_quality"]
    assert lq.label_vi == "Hạ mức chất lượng xuống tiết kiệm"
    assert "Tiết kiệm" not in lq.description and "Trung bình" not in lq.description
    combo = by_code(options)["combined"]
    assert "Tiết kiệm" not in combo.label_vi


def test_fits_budget_true_when_target_is_close():
    _, options = run(make_input(), 1_700_000_000)  # giảm 1 tầng 1,42–1,68 tỷ vừa trọn
    assert any(o.fits_budget for o in options)
    assert "combined" not in by_code(options)


def test_all_feature_options_present():
    inp = make_input(work_scope="tron_goi", basement=True, basement_depth_m=1.5, has_mezzanine=True,
                     rooftop_terrace="covered", rooftop_area_m2=50, roof_type="roof_tile_on_concrete")
    current, options = run(inp, 1_000_000_000)
    codes = by_code(options)
    assert {"reduce_floor", "lower_quality", "remove_basement", "change_roof", "open_rooftop",
            "remove_mezzanine", "phase_rough_first"} <= set(codes)
    assert codes["change_roof"].changes == {"roof_type": "roof_tile_on_truss"}
    assert codes["phase_rough_first"].changes == {"work_scope": "xay_tho"}
    assert codes["remove_basement"].changes["basement"] is False
    rb = apply_changes(inp, codes["remove_basement"].changes)
    assert not any(it.group_name == "ham" for it in calculate_estimate(rb, make_rules(), lookup(rb)).items)


def test_lower_quality_skipped_when_rate_missing_or_already_cheapest():
    _, options = run(make_input(quality_tier="tiet_kiem"), 1_000_000_000)
    assert "lower_quality" not in by_code(options)
    # Không có đơn giá cho mức thấp hơn -> bỏ phương án (không tự suy ra giá)
    def no_cheap(inp):
        return None if str(inp.quality_tier) == "tiet_kiem" else lookup(inp)

    inp = make_input()
    rules = make_rules()
    current = calculate_estimate(inp, rules, no_cheap(inp), today=TODAY)
    opts2 = suggest_adjustments(inp, rules, no_cheap, 1_000_000_000, current, today=TODAY)
    assert "lower_quality" not in by_code(opts2)
    assert "reduce_floor" in by_code(opts2)


def test_minimal_house_may_have_no_options():
    inp = make_input(floors=1, quality_tier="tiet_kiem", roof_type="roof_metal")
    _, options = run(inp, 100_000_000)
    assert options == []


def test_no_current_cost_saving_is_none():
    inp = make_input()
    rules = make_rules()
    current = calculate_estimate(inp, rules, None, today=TODAY)
    options = suggest_adjustments(inp, rules, lookup, 1_200_000_000, current, today=TODAY)
    assert options and all(o.saving_min is None for o in options)


def test_apply_changes_revalidates():
    new = apply_changes(make_input(), {"quality_tier": "kha", "floors": 2})
    assert new.floors == 2 and new.quality_tier == "kha"
    assert "quality_tier" in new.model_fields_set
    assert new.foundation_type is None  # mặc định để engine tính lại


def test_fits_budget_requires_upper_bound_within_budget():
    """Phương án 1,28–1,52 tỷ KHÔNG được gắn "Vừa ngân sách" với ngân sách 1,3 tỷ (cận trên vượt)."""
    _, options = run(make_input(), 1_300_000_000)
    for o in options:
        assert o.fits_budget is (o.cost_max is not None and o.cost_max <= 1_300_000_000)
