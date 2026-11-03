"""Unit test Matching Engine thuần (đặc tả mục 7) với 3 fixture dựng tay giống mục 11.3. Không cần DB."""
from datetime import date

import pytest

from app.engines.matching import (
    FLAG_VERIFY_PRICE,
    CapabilityFact,
    ContractorFacts,
    FieldSource,
    ProjectFact,
    build_cautions,
    build_evidence,
    build_reasons,
    profile_completeness,
    rank_contractors,
    score_contractor,
    verified_ratio,
)
from app.engines.matching.criteria import missing_important_fields, norm_place
from app.engines.matching.scorer import budget_score, estimate_cost
from app.evidence import SourceRef
from app.formatting import NO_DATA
from app.schemas.matching import FIT_HIGH, FIT_OK, FIT_REVIEW, MatchCriteria

HCM = "TP. Hồ Chí Minh"
HN = "Hà Nội"
WEIGHTS = {"location": 0.20, "building_type": 0.20, "budget_fit": 0.20, "experience": 0.15,
           "similar_projects": 0.15, "profile_quality": 0.10}
NEIGHBORS_HCM = ["Bình Dương", "Đồng Nai", "Long An"]
OBSERVED = date(2026, 10, 31)
TODAY = date(2026, 11, 2)


def _src(name: str, status: str = "on_dezon", updated: date = OBSERVED) -> FieldSource:
    return FieldSource(ref=SourceRef(label=f"Hồ sơ nhà thầu {name}", url=f"https://dezon.vn/nha-thau/{name}",
                                     updated_at=updated), status=status)


def kien_phong(**over) -> ContractorFacts:
    src = _src("kien-phong")
    data = dict(
        id=1, name="Kiến Phong", slug="kien-phong", profile_url="https://dezon.vn/nha-thau/kien-phong",
        province=HCM, founded_year=2018, years_experience=8, staff_workers=20, staff_office=12,
        rough_price_min=4_300_000, rough_price_max=4_700_000, finish_price_min=7_300_000,
        finish_price_max=9_500_000, typical_project_value=25_000_000_000, dezon_project_count=7,
        has_warranty_info=None, description="Thiết kế và thi công nhà phố, biệt thự.",
        avatar_url="https://placehold.co/160",
        capabilities=[CapabilityFact("nha_pho", None, HCM), CapabilityFact("biet_thu", None, HCM),
                      CapabilityFact("can_ho", None, HCM)],
        projects=[ProjectFact("biet_thu", HN), ProjectFact("biet_thu", "Vĩnh Long"),
                  ProjectFact("biet_thu", HCM, "Quận 7")],
        default_source=src,
    )
    data.update(over)
    return ContractorFacts(**data)


def smartcons(**over) -> ContractorFacts:
    data = dict(id=2, name="Smartcons Tech", slug="smartcons-tech", province=HCM, dezon_project_count=8,
                description="Đơn vị thi công xây dựng nhà ở.", avatar_url="https://placehold.co/160",
                capabilities=[CapabilityFact("nha_pho", None, HCM), CapabilityFact("biet_thu", None, HCM)],
                default_source=_src("smartcons-tech"))
    data.update(over)
    return ContractorFacts(**data)


def huy_hoang(**over) -> ContractorFacts:
    data = dict(id=3, name="Huy Hoàng", slug="huy-hoang", province=HCM, dezon_project_count=6,
                avatar_url="https://placehold.co/160", capabilities=[CapabilityFact("nha_pho", None, HCM)],
                default_source=_src("huy-hoang"))
    data.update(over)
    return ContractorFacts(**data)


def crit(**over) -> MatchCriteria:
    data = dict(province=HCM, building_type="nha_pho", work_scope="xay_tho")
    data.update(over)
    return MatchCriteria(**data)


def _c(scored, key):
    return scored.criterion(key)


# --- profile_completeness / verified -----------------------------------------------------------
def test_profile_completeness_formula():
    assert profile_completeness(kien_phong()) == 0.9  # thiếu has_warranty_info
    assert profile_completeness(kien_phong(has_warranty_info=True)) == 1.0
    assert profile_completeness(smartcons()) == 0.3  # description, avatar_url, dezon_project_count>0
    assert profile_completeness(huy_hoang()) == 0.2
    assert profile_completeness(huy_hoang(dezon_project_count=0)) == 0.1  # count = 0 không tính
    assert profile_completeness(huy_hoang(has_warranty_info=False)) == 0.2


def test_verified_ratio_counts_only_verified_fields():
    assert verified_ratio(kien_phong()) == 0.0
    kp = kien_phong(default_source=_src("kien-phong", status="verified"))
    assert verified_ratio(kp) == 0.9
    kp2 = kien_phong(field_sources={"founded_year": _src("x", "verified"), "years_experience": _src("x", "verified")})
    assert verified_ratio(kp2) == 0.2


def test_missing_important_fields():
    assert missing_important_fields(kien_phong()) == []
    assert missing_important_fields(smartcons()) == ["price", "years_experience"]
    assert missing_important_fields(huy_hoang(years_experience=5)) == ["price"]


def test_norm_place():
    assert norm_place("TP. Hồ Chí Minh") == norm_place("Hồ Chí Minh")
    assert norm_place("TP. Thủ Đức") == norm_place("Thủ Đức")
    assert norm_place("Quận 7") == "7"


# --- Từng tiêu chí -----------------------------------------------------------------------------
def test_location_scores():
    kp = kien_phong()
    assert _c(score_contractor(kp, crit(district="Quận 7"), WEIGHTS), "location").score == 1.0
    assert _c(score_contractor(kp, crit(), WEIGHTS), "location").score == 0.8
    assert _c(score_contractor(kp, crit(district="Quận 1"), WEIGHTS), "location").score == 0.8
    # Kiến Phong (hồ sơ TP HCM) chỉ có 1 công trình ở Hà Nội -> thấp hơn nhà thầu sở tại Hà Nội
    s_hn = score_contractor(kp, crit(province=HN), WEIGHTS)
    assert (_c(s_hn, "location").score, s_hn.location_kind) == (0.6, "project")
    hn_local = huy_hoang(province=HN, capabilities=[CapabilityFact("nha_pho", None, HN)])
    assert _c(score_contractor(hn_local, crit(province=HN), WEIGHTS), "location").score == 0.8
    bd = huy_hoang(province="Bình Dương", capabilities=[CapabilityFact("nha_pho", None, "Bình Dương")])
    s = score_contractor(bd, crit(), WEIGHTS, NEIGHBORS_HCM)
    assert _c(s, "location").score == 0.4 and s.relaxed_location
    assert _c(score_contractor(bd, crit(), WEIGHTS, []), "location").score == 0.0
    for c in score_contractor(kp, crit(), WEIGHTS).breakdown:
        assert c.note  # mỗi tiêu chí có giải thích


def test_building_type_scores():
    kp = kien_phong()
    assert _c(score_contractor(kp, crit(building_type="biet_thu"), WEIGHTS), "building_type").score == 1.0
    assert _c(score_contractor(kp, crit(building_type="nha_pho"), WEIGHTS), "building_type").score == 0.7
    only_proj = huy_hoang(capabilities=[CapabilityFact(None, None, HCM)], projects=[ProjectFact("nha_pho", HCM)])
    assert _c(score_contractor(only_proj, crit(), WEIGHTS), "building_type").score == 0.7
    assert _c(score_contractor(kp, crit(building_type="khach_san"), WEIGHTS), "building_type").score == 0.0


def test_estimate_cost_uses_scope_price():
    kp = kien_phong()
    assert estimate_cost(kp, "xay_tho", 490) == ((2_107_000_000, 2_303_000_000), "rough")
    assert estimate_cost(kp, "tron_goi", 490) == ((3_577_000_000, 4_655_000_000), "finish")
    assert estimate_cost(kp, "hoan_thien", 490) == (None, None)  # trọn gói ≠ giá riêng phần hoàn thiện
    assert estimate_cost(kp, "noi_that", 490) == (None, None)
    assert estimate_cost(kp, "xay_tho", None) == (None, "rough")
    assert estimate_cost(smartcons(), "xay_tho", 490) == (None, "rough")


def test_budget_score_rule():
    assert budget_score((2_107e6, 2_303e6), 2_200e6) == (1.0, "")  # giao [0,9B;1,1B], có phần ≤ B
    s, flag = budget_score((2_107e6, 2_303e6), 2_000e6)  # vượt 5,35%
    assert flag == "over_budget" and s == pytest.approx(1 - 0.0535 / 0.4, abs=1e-3)
    assert budget_score((1_400e6, 1_500e6), 1_000e6)[0] == 0.0  # vượt 40% -> 0
    assert budget_score((1_420e6, 1_500e6), 1_000e6)[0] == 0.0  # vượt > 40% -> 0
    assert budget_score((1_200e6, 1_300e6), 1_000e6)[0] == pytest.approx(0.5)  # vượt 20% -> 0,5
    assert budget_score((1_000e6, 1_100e6), 2_000e6) == (0.8, "under_budget")  # thấp hơn nhiều


def test_budget_fit_criterion_cases():
    kp = kien_phong()
    ok = score_contractor(kp, crit(budget_vnd=2_200_000_000, converted_area_m2=490), WEIGHTS)
    assert _c(ok, "budget_fit").score == 1.0 and ok.estimated_cost == (2_107_000_000, 2_303_000_000)
    over = score_contractor(kp, crit(budget_vnd=1_500_000_000, converted_area_m2=490), WEIGHTS)
    assert _c(over, "budget_fit").score == 0.0 and "over_budget" in over.flags
    # Không có ngân sách / không có diện tích quy đổi -> trung tính 0,5 kèm note
    nb = score_contractor(kp, crit(converted_area_m2=490), WEIGHTS)
    assert _c(nb, "budget_fit").score == 0.5 and "ngân sách" in _c(nb, "budget_fit").note
    assert nb.estimated_cost == (2_107_000_000, 2_303_000_000)  # vẫn ước được chi phí
    na = score_contractor(kp, crit(budget_vnd=2_000_000_000), WEIGHTS)
    assert _c(na, "budget_fit").score == 0.5 and "diện tích quy đổi" in _c(na, "budget_fit").note
    # Không có đơn giá -> 0,5 + cờ "Cần xác minh mức giá"
    sc = score_contractor(smartcons(), crit(budget_vnd=2_000_000_000, converted_area_m2=490), WEIGHTS)
    assert _c(sc, "budget_fit").score == 0.5 and FLAG_VERIFY_PRICE in sc.flags
    # Nội thất: đơn giá thô/hoàn thiện không áp dụng -> trung tính
    nt = score_contractor(kp, crit(work_scope="noi_that", budget_vnd=2_000_000_000, converted_area_m2=490), WEIGHTS)
    assert _c(nt, "budget_fit").score == 0.5 and nt.estimated_cost is None


def test_experience_formula():
    assert _c(score_contractor(kien_phong(), crit(), WEIGHTS), "experience").score == pytest.approx(0.88)
    assert _c(score_contractor(smartcons(), crit(), WEIGHTS), "experience").score == 0.0
    s = score_contractor(huy_hoang(years_experience=15, staff_workers=10), crit(), WEIGHTS)
    assert _c(s, "experience").score == pytest.approx(0.6 + 10 / 30 * 0.4, abs=1e-3)


def test_similar_projects_formula():
    projs = [ProjectFact("nha_pho", HCM, None, 4)] * 3 + [ProjectFact("nha_pho", HCM, None, 5),
                                                          ProjectFact("nha_pho", HCM, None, 7),
                                                          ProjectFact("nha_pho", HN, None, 4),
                                                          ProjectFact("biet_thu", HCM, None, 4),
                                                          ProjectFact("nha_pho", HCM, None, None)]
    kp = kien_phong(projects=projs)
    # 4 dự án khớp ±1 tầng + 1 dự án không có số tầng (tính 0,5) = 4,5 -> 0,9
    assert _c(score_contractor(kp, crit(floors=4), WEIGHTS), "similar_projects").score == pytest.approx(0.9)
    # Không có số tầng của khách -> bỏ điều kiện tầng: 6 dự án -> min(6/5,1)=1
    assert _c(score_contractor(kp, crit(), WEIGHTS), "similar_projects").score == 1.0
    assert _c(score_contractor(kien_phong(), crit(floors=4), WEIGHTS), "similar_projects").score == 0.0


def test_profile_quality_formula():
    assert _c(score_contractor(kien_phong(), crit(), WEIGHTS), "profile_quality").score == pytest.approx(0.63)
    kp = kien_phong(has_warranty_info=True, default_source=_src("kp", "verified"))
    assert _c(score_contractor(kp, crit(), WEIGHTS), "profile_quality").score == pytest.approx(1.0)


# --- Tổng điểm và nhãn -------------------------------------------------------------------------
def test_total_score_and_breakdown_weighted():
    s = score_contractor(kien_phong(), crit(), WEIGHTS)
    # 0,8×20 + 0,7×20 + 0,5×20 + 0,88×15 + 0×15 + 0,63×10 = 59,5 -> 60
    assert s.score == 60
    assert [c.key for c in s.breakdown] == ["location", "building_type", "budget_fit", "experience",
                                           "similar_projects", "profile_quality"]
    assert sum(c.weighted for c in s.breakdown) == pytest.approx(59.5, abs=0.01)
    assert s.fit_label == FIT_REVIEW  # < 70


def test_weights_are_injected_not_hardcoded():
    only_exp = {"location": 0, "building_type": 0, "budget_fit": 0, "experience": 1, "similar_projects": 0,
                "profile_quality": 0}
    assert score_contractor(kien_phong(), crit(), only_exp).score == 88
    # Trọng số không chuẩn hóa (tổng 2) được chuẩn hóa về 1
    doubled = {k: v * 2 for k, v in WEIGHTS.items()}
    assert score_contractor(kien_phong(), crit(), doubled).score == 60
    with pytest.raises(ValueError):
        score_contractor(kien_phong(), crit(), {})


def test_fit_labels():
    strong = kien_phong(has_warranty_info=True, default_source=_src("kp", "verified"),
                        capabilities=[CapabilityFact("nha_pho", None, HCM, "Quận 7")],
                        projects=[ProjectFact("nha_pho", HCM, "Quận 7", 4)] * 5)
    s = score_contractor(strong, crit(district="Quận 7", floors=4, budget_vnd=2_200_000_000,
                                      converted_area_m2=490), WEIGHTS)
    assert s.score >= 85 and s.fit_label == FIT_HIGH
    mid = score_contractor(kien_phong(projects=[ProjectFact("nha_pho", HCM, None, 4)]),
                           crit(floors=4, budget_vnd=2_200_000_000, converted_area_m2=490), WEIGHTS)
    assert 70 <= mid.score <= 84 and mid.fit_label == FIT_OK
    # Smartcons Tech, Huy Hoàng: thiếu kinh nghiệm + đơn giá -> "Cần đánh giá thêm"
    for f in (smartcons(), huy_hoang()):
        assert score_contractor(f, crit(), WEIGHTS).fit_label == FIT_REVIEW
    # Thiếu ≥ 2 trường quan trọng -> "Cần đánh giá thêm" kể cả khi điểm cao
    rich_but_missing = smartcons(capabilities=[CapabilityFact("nha_pho", None, HCM, "Quận 7")],
                                 projects=[ProjectFact("nha_pho", HCM, "Quận 7", 4)] * 5,
                                 staff_workers=40, default_source=_src("s", "verified"))
    s2 = score_contractor(rich_but_missing, crit(district="Quận 7", floors=4), {"location": 0.5,
                                                                                "similar_projects": 0.5})
    assert s2.score == 100 and s2.fit_label == FIT_REVIEW


# --- Bộ lọc cứng, nới tỉnh lân cận, xếp hạng ---------------------------------------------------
def test_rank_three_fixtures_hcm():
    r = rank_contractors([kien_phong(), smartcons(), huy_hoang()], crit(), WEIGHTS, NEIGHBORS_HCM)
    assert r.total_candidates == 3 and not r.relaxed_to_neighbors
    assert [s.facts.name for s in r.ranked][0] == "Kiến Phong"
    assert len(r.ranked) == 3
    assert [s.score for s in r.ranked] == sorted([s.score for s in r.ranked], reverse=True)


def test_hard_filter_active_and_province():
    inactive = huy_hoang(id=4, is_active=False)
    hanoi = huy_hoang(id=5, name="HN Co", province=HN, capabilities=[CapabilityFact("nha_pho", None, HN)])
    r = rank_contractors([kien_phong(), smartcons(), huy_hoang(), inactive, hanoi], crit(), WEIGHTS, NEIGHBORS_HCM)
    assert {s.facts.id for s in r.ranked} == {1, 2, 3}
    # Chỉ có dự án tại tỉnh (không có capability) vẫn qua bộ lọc
    proj_only = huy_hoang(id=6, province=None, capabilities=[], projects=[ProjectFact("nha_pho", HCM)])
    r2 = rank_contractors([proj_only], crit(), WEIGHTS, [])
    assert [s.facts.id for s in r2.ranked] == [6]


def test_relax_to_neighbor_provinces_when_fewer_than_three():
    bd = huy_hoang(id=7, name="Bình Dương Build", province="Bình Dương",
                   capabilities=[CapabilityFact("nha_pho", None, "Bình Dương")])
    far = huy_hoang(id=8, name="Đà Nẵng Co", province="Đà Nẵng",
                    capabilities=[CapabilityFact("nha_pho", None, "Đà Nẵng")])
    r = rank_contractors([kien_phong(), bd, far], crit(), WEIGHTS, NEIGHBORS_HCM)
    assert r.relaxed_to_neighbors and r.total_candidates == 2
    assert {s.facts.id for s in r.ranked} == {1, 7}
    relaxed = next(s for s in r.ranked if s.facts.id == 7)
    assert relaxed.relaxed_location and relaxed.location_kind == "neighbor"
    assert not next(s for s in r.ranked if s.facts.id == 1).relaxed_location
    assert any("tỉnh lân cận" in n for n in r.notes)
    # Đủ 3 ứng viên tại tỉnh -> không nới
    r2 = rank_contractors([kien_phong(), smartcons(), huy_hoang(), bd], crit(), WEIGHTS, NEIGHBORS_HCM)
    assert not r2.relaxed_to_neighbors and 7 not in {s.facts.id for s in r2.ranked}


def test_limit_top_n():
    many = [huy_hoang(id=10 + i, name=f"NT {i}") for i in range(7)]
    assert len(rank_contractors(many, crit(limit=3), WEIGHTS).ranked) == 3
    assert len(rank_contractors(many, crit(), WEIGHTS).ranked) == 5


# --- Evidence, lý do, lưu ý --------------------------------------------------------------------
def test_build_evidence_displays():
    ev = build_evidence(kien_phong(), today=TODAY)
    assert ev["province"].display == HCM
    assert ev["building_types"].display == "Nhà phố · Biệt thự · Căn hộ"
    assert ev["years_experience"].display == "8 năm"
    assert ev["founded_year"].display == "2018"
    assert ev["staff"].display == "20 thợ, 12 văn phòng"
    assert ev["rough_price"].display == "4,3–4,7 triệu/m²" and ev["rough_price"].value == [4_300_000, 4_700_000]
    assert ev["finish_price"].display == "7,3–9,5 triệu/m²"
    assert ev["typical_project_value"].display == "25 tỷ/công trình"
    assert ev["dezon_project_count"].display == "7 công trình ghi nhận trên Dezon"
    assert ev["warranty"].display == NO_DATA and ev["warranty"].status == "unavailable"
    assert ev["rough_price"].status == "on_dezon" and ev["rough_price"].source.url.endswith("kien-phong")
    assert not ev["rough_price"].is_stale
    sc = build_evidence(smartcons(), today=TODAY)
    for k in ("years_experience", "founded_year", "staff", "rough_price", "finish_price", "typical_project_value"):
        assert sc[k].display == NO_DATA and sc[k].value is None and sc[k].source is None


def _check_reasons(scored, ev, criteria):
    reasons = build_reasons(scored, ev, criteria)
    assert len(reasons) >= 2
    for r in reasons:
        assert r.evidence in ev and ev[r.evidence].has_value, r
    return reasons


@pytest.mark.parametrize("factory", [kien_phong, smartcons, huy_hoang])
def test_reasons_at_least_two_with_evidence(factory):
    c = crit(budget_vnd=2_200_000_000, converted_area_m2=490)
    s = score_contractor(factory(), c, WEIGHTS)
    _check_reasons(s, build_evidence(factory(), today=TODAY), c)


def test_reason_texts_use_evidence_display():
    c = crit(budget_vnd=2_200_000_000, converted_area_m2=490)
    kp = kien_phong()
    ev = build_evidence(kp, today=TODAY)
    texts = [r.text for r in _check_reasons(score_contractor(kp, c, WEIGHTS), ev, c)]
    assert "7 công trình ghi nhận trên Dezon" in texts
    assert any(t.startswith("Đơn giá xây thô 4,3–4,7 triệu/m²") for t in texts)
    assert f"Hoạt động tại {HCM}" in texts


def test_reasons_minimal_profile_falls_back():
    bare = huy_hoang(dezon_project_count=None, capabilities=[CapabilityFact(None, None, HCM)], province=None)
    s = score_contractor(bare, crit(), WEIGHTS)
    _check_reasons(s, build_evidence(bare, today=TODAY), crit())


def test_cautions():
    c = crit(budget_vnd=2_000_000_000, converted_area_m2=490)
    sc = smartcons()
    texts = [x.text for x in build_cautions(score_contractor(sc, c, WEIGHTS), build_evidence(sc, today=TODAY), c)]
    assert "Chưa có thông tin đơn giá — cần đối chiếu" in texts
    assert "Chưa có thông tin kinh nghiệm" in texts
    assert "Chưa có thông tin bảo hành" in texts
    # Vượt ngân sách
    over = crit(budget_vnd=1_500_000_000, converted_area_m2=490)
    kp = kien_phong()
    t2 = [x.text for x in build_cautions(score_contractor(kp, over, WEIGHTS), build_evidence(kp, today=TODAY), over)]
    assert any("vượt ngân sách" in t for t in t2)
    # Dữ liệu cũ
    old = build_evidence(kp, today=date(2027, 12, 1), stale_days=180)
    assert old["rough_price"].is_stale
    t3 = [x.text for x in build_cautions(score_contractor(kp, c, WEIGHTS), old, c)]
    assert any("31/10/2026" in t and "có thể đã cũ" in t for t in t3)
    # Chỉ khớp tỉnh lân cận
    bd = huy_hoang(province="Bình Dương", capabilities=[CapabilityFact("nha_pho", None, "Bình Dương")])
    s = score_contractor(bd, c, WEIGHTS, NEIGHBORS_HCM)
    t4 = [x.text for x in build_cautions(s, build_evidence(bd, today=TODAY), c)]
    assert any("tỉnh lân cận" in t for t in t4)


def test_no_best_claims_anywhere():
    c = crit(budget_vnd=2_000_000_000, converted_area_m2=490, district="Quận 7", floors=4)
    r = rank_contractors([kien_phong(), smartcons(), huy_hoang()], c, WEIGHTS, NEIGHBORS_HCM)
    blobs = list(r.notes)
    for s in r.ranked:
        ev = build_evidence(s.facts, today=TODAY)
        blobs += [x.text for x in build_reasons(s, ev, c)] + [x.text for x in build_cautions(s, ev, c)]
        blobs += [b.note for b in s.breakdown] + [e.display for e in ev.values()]
    assert blobs and not any("tốt nhất" in b.lower() for b in blobs)


# --- Bổ sung sau review ------------------------------------------------------------------------
def test_norm_place_accents_aliases_and_district_forms():
    hcm = norm_place(HCM)
    for variant in ("Hồ Chí Minh", "Ho Chi Minh", "ho chi minh", "TP.HCM", "TP HCM", "tphcm", "HCM",
                    "Sài Gòn", "Sai Gon", "Thành phố Hồ Chí Minh",
                    __import__("unicodedata").normalize("NFD", "TP. Hồ Chí Minh")):
        assert norm_place(variant) == hcm, variant
    assert norm_place("Quận 7") == norm_place("Q7") == norm_place("Q.7") == norm_place("quan 7") == "7"
    assert norm_place("TP. Thủ Đức") == norm_place("Thu Duc")
    assert norm_place("Bà Rịa - Vũng Tàu") == norm_place("Ba Ria Vung Tau")
    assert norm_place("Quảng Nam") == "quang nam"  # không cắt nhầm tiền tố "Q"
    assert norm_place("Quan Hóa") == "quan hoa"  # "quan" không dấu chỉ cắt khi theo sau là số
    assert norm_place(None) == norm_place("") == ""


def test_unaccented_criteria_still_match_location_and_filter():
    c = crit(province="Ho Chi Minh", district="Q7")
    kp = kien_phong()
    assert _c(score_contractor(kp, c, WEIGHTS), "location").score == 1.0
    r = rank_contractors([kien_phong(), smartcons(), huy_hoang()], crit(province="Sài Gòn"), WEIGHTS)
    assert r.total_candidates == 3


def test_non_positive_budget_or_area_is_neutral():
    kp = kien_phong()
    for bad in (0, -1_000_000_000):
        s = score_contractor(kp, crit(budget_vnd=bad, converted_area_m2=490), WEIGHTS)
        bf = _c(s, "budget_fit")
        assert bf.score == 0.5 and "ngân sách" in bf.note
        texts = [r.text for r in build_reasons(s, build_evidence(kp, today=TODAY), crit(budget_vnd=bad))]
        assert not any("phù hợp ngân sách" in t for t in texts)
    assert estimate_cost(kp, "xay_tho", 0) == (None, "rough")
    assert estimate_cost(kp, "xay_tho", -10) == (None, "rough")
    with pytest.raises(ValueError):
        budget_score((1e9, 2e9), 0)


def test_budget_clause_only_when_budget_fit_full():
    kp = kien_phong()
    ev = build_evidence(kp, today=TODAY)
    ok = crit(budget_vnd=2_200_000_000, converted_area_m2=490)
    assert any("phù hợp ngân sách" in r.text for r in build_reasons(score_contractor(kp, ok, WEIGHTS), ev, ok))
    under = crit(budget_vnd=5_000_000_000, converted_area_m2=490)
    assert not any("phù hợp ngân sách" in r.text
                   for r in build_reasons(score_contractor(kp, under, WEIGHTS), ev, under))


def test_interior_scope_has_no_contradicting_price_reason():
    c = crit(work_scope="noi_that", budget_vnd=2_000_000_000, converted_area_m2=490)
    kp = kien_phong()
    s = score_contractor(kp, c, WEIGHTS)
    ev = build_evidence(kp, today=TODAY)
    reasons = _check_reasons(s, ev, c)
    assert not any(r.evidence in ("rough_price", "finish_price") for r in reasons)
    assert any("nội thất" in x.text for x in build_cautions(s, ev, c))


def test_project_only_contractor_gets_province_evidence_and_two_reasons():
    proj_only = huy_hoang(id=6, province=None, capabilities=[], dezon_project_count=None,
                          projects=[ProjectFact(None, HCM)])
    r = rank_contractors([proj_only], crit(), WEIGHTS, [])
    assert [s.facts.id for s in r.ranked] == [6]
    ev = build_evidence(proj_only, today=TODAY)
    assert ev["province"].has_value and ev["province"].display == HCM
    _check_reasons(r.ranked[0], ev, crit())


def test_province_reason_when_main_province_differs():
    hn_based = huy_hoang(id=9, province=HN, capabilities=[CapabilityFact("nha_pho", None, HCM)])
    s = score_contractor(hn_based, crit(), WEIGHTS)
    texts = [r.text for r in _check_reasons(s, build_evidence(hn_based, today=TODAY), crit())]
    assert any(t.startswith(f"Hoạt động tại {HN}") and HCM in t for t in texts)


def test_zero_years_experience_is_not_a_reason():
    hh = huy_hoang(years_experience=0)
    s = score_contractor(hh, crit(), WEIGHTS)
    reasons = _check_reasons(s, build_evidence(hh, today=TODAY), crit())
    assert all(r.evidence != "years_experience" for r in reasons)


def test_upper_bound_only_price_is_costed_at_upper_bound_and_counts_as_price():
    """Cận dưới mặc định "2 tr" của form bị bỏ (crawler) -> chỉ còn cận trên: chi phí ước tính theo cận trên,
    không được lợi thế "vừa ngân sách" so với nhà thầu khai khoảng cụ thể; hồ sơ vẫn tính là có đơn giá thô."""
    precise = kien_phong()
    upper_only = kien_phong(id=9, slug="x", name="X", rough_price_min=None, rough_price_max=4_300_000)
    crit = MatchCriteria(province=HCM, building_type="nha_pho", work_scope="xay_tho", budget_vnd=1_200_000_000,
                         converted_area_m2=300)
    assert estimate_cost(upper_only, "xay_tho", 300) == ((1_290_000_000, 1_290_000_000), "rough")
    s_precise = score_contractor(precise, crit, WEIGHTS).criterion("budget_fit").score
    s_upper = score_contractor(upper_only, crit, WEIGHTS).criterion("budget_fit").score
    assert s_upper <= s_precise < 1.0
    assert profile_completeness(upper_only) == profile_completeness(precise)
    assert build_evidence(upper_only, TODAY)["rough_price"].display == "tối đa 4,3 triệu/m²"
