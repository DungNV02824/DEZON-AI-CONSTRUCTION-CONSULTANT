"""Review sửa lỗi — nhóm matching (#1, #16, #20, #22, #30). Test thuần, không cần DB."""
from datetime import date

import pytest

from app.ai import blocks
from app.engines.matching import (
    CapabilityFact,
    ContractorFacts,
    FieldSource,
    ProjectFact,
    build_cautions,
    build_evidence,
    build_reasons,
    score_contractor,
)
from app.engines.matching.scorer import (
    FLAG_OVER_BUDGET,
    FLAG_SCOPE_NO_PRICE,
    PRICE_KIND_VI,
    SCOPE_PRICE_FIELD,
    budget_score,
    estimate_cost,
    format_cost,
)
from app.evidence import SourceRef
from app.formatting import NO_DATA
from app.schemas.matching import MatchCriteria
from app.services import contractor_service as svc

HCM = "TP. Hồ Chí Minh"
HN = "Hà Nội"
WEIGHTS = {"location": 0.20, "building_type": 0.20, "budget_fit": 0.20, "experience": 0.15,
           "similar_projects": 0.15, "profile_quality": 0.10}
TODAY = date(2026, 11, 2)
FINISH_LABEL = "Đơn giá trọn gói (hoàn thiện cơ bản)"


def _facts(**over) -> ContractorFacts:
    src = FieldSource(ref=SourceRef(label="Hồ sơ nhà thầu", url="https://dezon.vn/nha-thau/x",
                                    updated_at=date(2026, 10, 31)))
    data = dict(id=1, name="Kiến Phong", slug="kien-phong", province=HCM, years_experience=8,
                staff_workers=20, staff_office=12, rough_price_min=4_300_000, rough_price_max=4_700_000,
                finish_price_min=7_300_000, finish_price_max=9_500_000, dezon_project_count=7,
                capabilities=[CapabilityFact("nha_pho", None, HCM)],
                projects=[ProjectFact("nha_pho", HCM, "Quận 7", 3)], default_source=src)
    data.update(over)
    return ContractorFacts(**data)


def _crit(**over) -> MatchCriteria:
    data = dict(province=HCM, building_type="nha_pho", work_scope="xay_tho")
    data.update(over)
    return MatchCriteria(**data)


# --- #1 / #16: Hoàn thiện không dùng đơn giá trọn gói ------------------------------------------
def test_hoan_thien_scope_has_no_contractor_price():
    assert SCOPE_PRICE_FIELD["hoan_thien"] is None and SCOPE_PRICE_FIELD["tron_goi"] == "finish"
    kp = _facts()
    assert estimate_cost(kp, "hoan_thien", 300) == (None, None)
    crit = _crit(work_scope="hoan_thien", budget_vnd=1_500_000_000, converted_area_m2=300)
    s = score_contractor(kp, crit, WEIGHTS)
    bf = s.criterion("budget_fit")
    assert bf.score == 0.5 and FLAG_SCOPE_NO_PRICE in s.flags
    assert s.estimated_cost is None and s.price_kind is None
    assert "hoàn thiện" in bf.note and "trung tính" in bf.note
    assert svc._estimated_cost_evidence(s, 300).display == NO_DATA

    ev = build_evidence(kp, today=TODAY)
    reasons = build_reasons(s, ev, crit)
    assert all(r.evidence not in ("finish_price", "rough_price") for r in reasons)
    cautions = [c.text for c in build_cautions(s, ev, crit)]
    assert any("chưa công bố đơn giá riêng cho phạm vi hoàn thiện" in t for t in cautions)
    assert not any("nội thất" in t for t in cautions)


def test_noi_that_caution_names_its_scope():
    crit = _crit(work_scope="noi_that", budget_vnd=1_500_000_000, converted_area_m2=300)
    s = score_contractor(_facts(), crit, WEIGHTS)
    cautions = [c.text for c in build_cautions(s, build_evidence(_facts(), today=TODAY), crit)]
    assert any("phạm vi nội thất" in t for t in cautions)


def test_finish_price_is_labelled_tron_goi():
    assert PRICE_KIND_VI["finish"] == "trọn gói (hoàn thiện cơ bản)"
    kp = _facts()
    crit = _crit(work_scope="tron_goi", converted_area_m2=300)
    s = score_contractor(kp, crit, WEIGHTS)
    texts = [r.text for r in build_reasons(s, build_evidence(kp, today=TODAY), crit)]
    assert "Đơn giá trọn gói (hoàn thiện cơ bản) 7,3–9,5 triệu/m²" in texts
    assert dict(svc.COMPARISON_ROWS)["finish_price"] == FINISH_LABEL
    assert dict(blocks._OVERVIEW_ROWS)["finish_price"] == FINISH_LABEL
    label = svc._estimated_cost_evidence(s, 300).source.label
    assert "đơn giá trọn gói (hoàn thiện cơ bản)" in label


# --- #20: khớp quận qua dự án của nhà thầu ngoại tỉnh không vượt nhà thầu sở tại -----------------
def test_project_only_district_match_scores_below_local_contractor():
    outsider = _facts(id=1, name="Kiến Phong", province=HCM, capabilities=[CapabilityFact("nha_pho", None, HCM)],
                      projects=[ProjectFact("nha_pho", HN, "Cầu Giấy")])
    local = _facts(id=2, name="Hà Nội Local", province=HN, capabilities=[CapabilityFact("nha_pho", None, HN)],
                   projects=[])
    crit = _crit(province=HN, district="Cầu Giấy")
    s_out, s_loc = score_contractor(outsider, crit, WEIGHTS), score_contractor(local, crit, WEIGHTS)
    assert (s_out.location_kind, s_out.criterion("location").score) == ("project", 0.6)
    assert "Cầu Giấy" in s_out.criterion("location").note
    assert (s_loc.location_kind, s_loc.criterion("location").score) == ("province", 0.8)
    assert s_loc.criterion("location").score > s_out.criterion("location").score


def test_registered_contractor_project_in_district_still_district_match():
    reg = _facts(province=HN, capabilities=[CapabilityFact("nha_pho", None, HN)],
                 projects=[ProjectFact("nha_pho", HN, "Cầu Giấy")])
    s = score_contractor(reg, _crit(province=HN, district="Cầu Giấy"), WEIGHTS)
    assert (s.location_kind, s.criterion("location").score) == ("district", 1.0)
    cap = _facts(province=HCM, capabilities=[CapabilityFact("nha_pho", None, HN, "Cầu Giấy")], projects=[])
    s = score_contractor(cap, _crit(province=HN, district="Cầu Giấy"), WEIGHTS)
    assert (s.location_kind, s.criterion("location").score) == ("district", 1.0)


# --- #22 / #30: chi phí từ đơn giá chỉ có cận trên hiển thị là mức tối đa ------------------------
def _upper_only():
    return _facts(id=9, name="Smartcons Tech", rough_price_min=None, rough_price_max=4_300_000)


def test_format_cost_upper_only():
    assert format_cost((1_290_000_000, 1_290_000_000), upper_only=True) == "tối đa 1,29 tỷ"
    assert format_cost((1_290_000_000, 1_410_000_000)) == "1,29–1,41 tỷ"


def test_upper_only_cost_shown_as_maximum_on_card_and_note():
    crit = _crit(budget_vnd=1_300_000_000, converted_area_m2=300)
    s = score_contractor(_upper_only(), crit, WEIGHTS)
    assert s.cost_upper_only and s.estimated_cost == (1_290_000_000, 1_290_000_000)
    bf = s.criterion("budget_fit")
    assert bf.score == 1.0 and bf.note == "Chi phí ước tối đa 1,29 tỷ phù hợp ngân sách 1,3 tỷ"
    ev = svc._estimated_cost_evidence(s, 300)
    assert ev.display == "tối đa 1,29 tỷ" and ev.value == [1_290_000_000, 1_290_000_000]
    assert "tối đa" in ev.source.label
    # Nhà thầu khai khoảng cụ thể: không có "tối đa".
    precise = score_contractor(_facts(), crit, WEIGHTS)
    assert not precise.cost_upper_only
    assert svc._estimated_cost_evidence(precise, 300).display == "1,29–1,41 tỷ"


def test_upper_only_ceiling_above_budget_is_neutral_not_over_budget():
    assert budget_score((1_290e6, 1_290e6), 1_200e6, upper_only=True) == (0.5, "")
    assert budget_score((1_290e6, 1_290e6), 1_300e6, upper_only=True) == (1.0, "")
    assert budget_score((1_000e6, 1_000e6), 2_000e6, upper_only=True) == (0.8, "under_budget")
    s, flag = budget_score((1_290e6, 1_290e6), 1_200e6)  # không phải upper_only: vẫn theo quy tắc cũ
    assert flag == FLAG_OVER_BUDGET and s == pytest.approx(1 - 0.075 / 0.4)

    crit = _crit(budget_vnd=1_200_000_000, converted_area_m2=300)
    up = _upper_only()
    s = score_contractor(up, crit, WEIGHTS)
    bf = s.criterion("budget_fit")
    assert bf.score == 0.5 and FLAG_OVER_BUDGET not in s.flags
    assert "tối đa 1,29 tỷ" in bf.note and "vượt" not in bf.note
    cautions = [c.text for c in build_cautions(s, build_evidence(up, today=TODAY), crit)]
    assert any("mức đơn giá tối đa" in t for t in cautions)
    assert not any("vượt ngân sách" in t for t in cautions)
