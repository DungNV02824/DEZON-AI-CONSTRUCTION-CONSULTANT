"""Test output guardrail validator (đặc tả mục 2.1, 13)."""
import uuid
from datetime import date

import pytest

from app.ai.validator import (
    AREA,
    CONTRACTOR_COUNT,
    MONEY,
    PERCENT,
    PROJECT_COUNT,
    RULE_BEST,
    RULE_ESTIMATE_QUOTE,
    RULE_PROJECT_LABEL,
    RULE_UNTRACEABLE,
    SCORE,
    STAFF,
    UNIT_PRICE,
    YEAR,
    YEARS,
    Violation,
    build_retry_message,
    check,
    collect_allowed,
    extract_claims,
    parse_number,
    record_violations,
    run_guarded,
)
from app.evidence import EvidenceValue, SourceRef, VerificationStatus
from app.formatting import NO_DATA
from app.schemas.estimate import EstimateInput, EstimateItem, EstimateResult
from app.schemas.matching import (
    FIT_HIGH,
    FIT_REVIEW,
    Caution,
    ContractorCard,
    ContractorProfile,
    CriterionScore,
    MatchCriteria,
    MatchedContractor,
    MatchResult,
    ProjectSummary,
    Reason,
)

OBS = date(2026, 10, 31)


def ev(value, display, label="Hồ sơ nhà thầu Kiến Phong", status=VerificationStatus.on_dezon):
    return EvidenceValue(value=value, display=display, status=status,
                         source=SourceRef(label=label, url="https://dezon.vn/nha-thau/kien-phong", updated_at=OBS))


NA = EvidenceValue.unavailable


# --------------------------------------------------------------------------------------------------
# Fixture dựng bằng schema thật
# --------------------------------------------------------------------------------------------------
def make_estimate() -> EstimateResult:
    rate_src = "Đơn giá tham chiếu (seed dev — chờ Dezon cung cấp bảng giá)"
    return EstimateResult(
        estimate_session_id=uuid.uuid4(),
        input=EstimateInput(building_type="nha_pho", province="TP. Hồ Chí Minh", land_area_m2=100, floors=4,
                            foundation_type="foundation_pile", roof_type="roof_concrete", work_scope="xay_tho"),
        items=[
            EstimateItem(rule_code="foundation_pile", label_vi="Móng cọc", group_name="foundation", area_m2=100,
                         coefficient=0.4, converted_m2=40),
            EstimateItem(rule_code="floor", label_vi="Sàn các tầng", group_name="floor", area_m2=400,
                         coefficient=1.0, converted_m2=400),
            EstimateItem(rule_code="roof_concrete", label_vi="Mái BTCT", group_name="roof", area_m2=100,
                         coefficient=0.5, converted_m2=50),
        ],
        converted_area_m2=490,
        cost_min=2_058_000_000,
        cost_max=2_352_000_000,
        cost=ev([2_058_000_000, 2_352_000_000], "2,058–2,352 tỷ", label=rate_src,
                status=VerificationStatus.reference),
        rate=ev([4_200_000, 4_800_000], "4,2–4,8 triệu/m²", label=rate_src, status=VerificationStatus.reference),
        rate_id=1,
        rules_version=1,
        rules_status=VerificationStatus.reference,
        assumptions=["Móng cọc theo số tầng", "Mái bê tông cốt thép"],
        exclusions=["Ép cọc", "Xin phép xây dựng", "Thiết kế", "Nội thất rời"],
        cost_drivers=["Hẻm nhỏ", "Đất yếu"],
        disclaimer=("Đây là khái toán sơ bộ dựa trên đơn giá tham chiếu và các giả định nêu trên, chưa tính điều "
                    "kiện thực tế của khu đất. Không phải báo giá thi công. Vui lòng yêu cầu báo giá từ nhà thầu "
                    "để có con số chính xác."),
    )


def _card(cid, slug, name, evidence, badge=None):
    return ContractorCard(id=cid, slug=slug, name=name, badge=badge,
                          profile_url=f"https://dezon.vn/nha-thau/{slug}", evidence=evidence)


def kien_phong_card() -> ContractorCard:
    return _card(1, "kien-phong", "Kiến Phong", {
        "province": ev("TP. Hồ Chí Minh", "TP. Hồ Chí Minh"),
        "building_types": ev(["nha_pho", "biet_thu", "can_ho"], "Nhà phố · Biệt thự · Căn hộ"),
        "years_experience": ev(8, "8 năm"),
        "founded_year": ev(2018, "2018"),
        "staff": ev({"workers": 20, "office": 12}, "20 thợ, 12 văn phòng"),
        "rough_price": ev([4_300_000, 4_700_000], "4,3–4,7 triệu/m²"),
        "finish_price": ev([7_300_000, 9_500_000], "7,3–9,5 triệu/m²"),
        "typical_project_value": ev(25_000_000_000, "25 tỷ/công trình"),
        "dezon_project_count": ev(7, "7 công trình"),
        "warranty": NA(),
    }, badge="Nhà thầu tiềm năng")


def missing_card(cid, slug, name, count) -> ContractorCard:
    return _card(cid, slug, name, {
        "province": ev("TP. Hồ Chí Minh", "TP. Hồ Chí Minh", label=f"Hồ sơ nhà thầu {name}"),
        "years_experience": NA(), "founded_year": NA(), "staff": NA(),
        "rough_price": NA(), "finish_price": NA(),
        "dezon_project_count": ev(count, f"{count} công trình", label=f"Hồ sơ nhà thầu {name}"),
        "warranty": NA(),
    })


def _breakdown(score_loc=1.0):
    return [
        CriterionScore(key="location", label_vi="Khu vực", score=score_loc, weight=0.2, weighted=20 * score_loc,
                       note="Cùng tỉnh"),
        CriterionScore(key="building_type", label_vi="Loại công trình", score=1.0, weight=0.2, weighted=20,
                       note="Có kinh nghiệm loại công trình"),
    ]


def make_match() -> MatchResult:
    kp = MatchedContractor(
        rank=1, score=88, fit_label=FIT_HIGH, breakdown=_breakdown(),
        reasons=[Reason(text="Có kinh nghiệm thi công nhà phố, biệt thự", evidence="building_types"),
                 Reason(text="7 công trình được ghi nhận trên Dezon", evidence="dezon_project_count"),
                 Reason(text="Đơn giá xây thô 4,3–4,7 triệu/m²", evidence="rough_price")],
        cautions=[Caution(text="Chưa có thông tin bảo hành", evidence="warranty")],
        estimated_cost=ev([2_107_000_000, 2_303_000_000], "2,107–2,303 tỷ"),
        contractor=kien_phong_card(),
    )
    sm = MatchedContractor(
        rank=2, score=64, fit_label=FIT_REVIEW, breakdown=_breakdown(),
        reasons=[Reason(text="Hoạt động tại TP. Hồ Chí Minh", evidence="province"),
                 Reason(text="8 công trình được ghi nhận trên Dezon", evidence="dezon_project_count")],
        cautions=[Caution(text="Chưa có thông tin đơn giá — cần đối chiếu", evidence="rough_price")],
        estimated_cost=NA(), contractor=missing_card(2, "smartcons-tech", "Smartcons Tech", 8),
    )
    hh = MatchedContractor(
        rank=3, score=58, fit_label=FIT_REVIEW, breakdown=_breakdown(),
        reasons=[Reason(text="Hoạt động tại TP. Hồ Chí Minh", evidence="province"),
                 Reason(text="6 công trình được ghi nhận trên Dezon", evidence="dezon_project_count")],
        cautions=[Caution(text="Chưa có thông tin kinh nghiệm", evidence="years_experience")],
        estimated_cost=NA(), contractor=missing_card(3, "huy-hoang", "Huy Hoàng", 6),
    )
    return MatchResult(recommendation_log_id=11, weights_version=1,
                       criteria=MatchCriteria(province="TP. Hồ Chí Minh", building_type="nha_pho",
                                              work_scope="xay_tho", budget_vnd=2_000_000_000, floors=4),
                       total_candidates=3, results=[kp, sm, hh])


def make_huy_hoang_profile() -> ContractorProfile:
    card = missing_card(3, "huy-hoang", "Huy Hoàng", 6)
    return ContractorProfile(
        card=card, description=NA(),
        info={"founded_year": NA(), "staff_workers": NA(), "staff_office": NA(), "rough_price": NA(),
              "finish_price": NA(), "typical_project_value": NA(),
              "dezon_project_count": card.evidence["dezon_project_count"], "warranty": NA()},
        projects=[], images=[], total_images=0,
    )


def make_kien_phong_profile() -> ContractorProfile:
    card = kien_phong_card()
    return ContractorProfile(
        card=card, description=ev("Đơn vị thiết kế và thi công nhà phố", "Đơn vị thiết kế và thi công nhà phố"),
        info={"founded_year": card.evidence["founded_year"], "staff_workers": ev(20, "20 thợ"),
              "staff_office": ev(12, "12 văn phòng"), "rough_price": card.evidence["rough_price"],
              "finish_price": card.evidence["finish_price"],
              "typical_project_value": card.evidence["typical_project_value"],
              "dezon_project_count": card.evidence["dezon_project_count"], "warranty": NA()},
        projects=[ProjectSummary(id=1, slug="france-indochine-22-ha-noi", name="France Indochine 22 Hà Nội",
                                 province="Hà Nội", project_url="https://dezon.vn/du-an/france-indochine-22-ha-noi"),
                  ProjectSummary(id=3, slug="indochine-villa-quan-7", name="Indochine Villa Quận 7 Sài Gòn",
                                 province="TP. Hồ Chí Minh", district="Quận 7",
                                 project_url="https://dezon.vn/du-an/indochine-villa-quan-7")],
        images=[], total_images=3,
    )


@pytest.fixture
def estimate() -> EstimateResult:
    return make_estimate()


@pytest.fixture
def match() -> MatchResult:
    return make_match()


@pytest.fixture
def huy_hoang_profile() -> ContractorProfile:
    return make_huy_hoang_profile()


@pytest.fixture
def kien_phong_profile() -> ContractorProfile:
    return make_kien_phong_profile()


# --------------------------------------------------------------------------------------------------
# parse_number / extract_claims
# --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("s,expected", [
    ("4,3", [4.3]), ("4.3", [4.3]), ("100", [100.0]), ("2.058", [2058.0, 2.058]), ("2,058", [2058.0, 2.058]),
    ("1.234.567", [1234567.0]), ("4,300,000", [4300000.0]), ("1.234,5", [1234.5]), ("1,234.5", [1234.5]),
])
def test_parse_number(s, expected):
    assert parse_number(s) == pytest.approx(expected)


def _kv(text):
    return [(c.kind, pytest.approx(c.values)) for c in extract_claims(text)]


def test_extract_money_and_ranges():
    assert _kv("Chi phí sơ bộ 2,058–2,352 tỷ") == [(MONEY, [2.058e12, 2.058e9]), (MONEY, [2.352e12, 2.352e9])]
    assert _kv("khoảng 850 triệu–1,2 tỷ") == [(MONEY, [8.5e8]), (MONEY, [1.2e9])]
    assert _kv("ngân sách 1 tỷ 8 cho phần thô") == [(MONEY, [1.8e9])]
    assert _kv("tổng 2 tỷ 58 triệu") == [(MONEY, [2.058e9])]
    assert _kv("khoảng 1 tỉ rưỡi") == [(MONEY, [1.5e9])]
    assert _kv("chi phí 2.500.000.000") == [("number", [2.5e9])]


def test_extract_unit_price_variants():
    for t in ["4,3–4,7 triệu/m²", "4.3-4.7 tr/m2", "từ 4,3 đến 4,7 triệu/m²", "4,3 - 4,7 triệu trên m2"]:
        assert _kv(t) == [(UNIT_PRICE, [4.3e6]), (UNIT_PRICE, [4.7e6])], t
    assert _kv("4.300.000 đ/m²") == [(UNIT_PRICE, [4.3e6])]
    assert _kv("4.300.000 đồng/m2") == [(UNIT_PRICE, [4.3e6])]


def test_extract_counts():
    claims = _kv("Có 3 nhà thầu; Kiến Phong có 7 công trình, 8 năm kinh nghiệm, 20 thợ, 12 nhân viên văn phòng, "
                 "thành lập năm 2018, điểm 88/100, 90 điểm, trọng số 30%, diện tích quy đổi 490 m², đất 100m2.")
    assert claims == [
        (CONTRACTOR_COUNT, [3]), (PROJECT_COUNT, [7]), (YEARS, [8]), (STAFF, [20]), (STAFF, [12]),
        (YEAR, [2018]), (SCORE, [88]), (SCORE, [90]), (PERCENT, [30]), (AREA, [490]), (AREA, [100]),
    ]


def test_extract_ignores_non_metric_numbers():
    text = ("1. Kiến Phong\n2. Smartcons Tech\nBước 2: chọn nhà thầu. Tầng 1 làm garage, nhà 4 tầng, "
            "Quận 7, phương án 2, khởi công tháng 6 năm 2027, cập nhật ngày 31/10/2026, "
            "xem https://dezon.vn/du-an/france-indochine-22-ha-noi, hotline support@dezon.vn")
    assert extract_claims(text) == []


# --------------------------------------------------------------------------------------------------
# collect_allowed
# --------------------------------------------------------------------------------------------------
def test_collect_allowed_from_tool_json(estimate, match):
    allowed = collect_allowed([estimate, match], user_texts=["nhà phố 5x20, ngân sách 2 tỷ"])
    assert 2_058_000_000 in allowed.generic  # cost_min
    assert any(abs(v - 4.3e6) < 1 for v in allowed.money)  # parse từ display "4,3–4,7 triệu/m²"
    assert 490 in allowed.generic
    assert 3 in allowed.by_kind[CONTRACTOR_COUNT]  # len(results)
    assert 100 in allowed.user  # 5x20 do khách nói
    assert 2e9 in allowed.money  # "2 tỷ" do khách nói
    assert 1 not in allowed.generic and 11 not in allowed.generic  # id / recommendation_log_id bị loại
    assert 88 in allowed.scores


def test_collect_allowed_ignores_rag_numbers():
    rag = {"chunks": [{"content": "Đơn giá xây thô khoảng 5,2 triệu/m²", "source": {"label": "Blog"}}]}
    assert not check("Đơn giá xây thô 5,2 triệu/m².", [("search_knowledge", rag)]).ok
    assert check("Đơn giá xây thô 5,2 triệu/m².", [("get_reference_rates", rag)]).ok


# --------------------------------------------------------------------------------------------------
# check — câu đúng
# --------------------------------------------------------------------------------------------------
def test_correct_estimate_answer_ok(estimate):
    answer = ("Dạ, với nhà phố 4 tầng trên 100 m² đất, diện tích sàn quy đổi là 490 m², đơn giá tham chiếu "
              "4,2–4,8 triệu/m² nên chi phí sơ bộ khoảng 2,058–2,352 tỷ. Đây là khái toán sơ bộ, không phải báo "
              "giá thi công. Vui lòng yêu cầu báo giá từ nhà thầu để có con số chính xác.")
    res = check(answer, [estimate])
    assert res.ok, res.violations


@pytest.mark.parametrize("answer", [
    "Chi phí sơ bộ khoảng 2,06 tỷ đến 2,35 tỷ.",  # làm tròn trong ±1%
    "Chi phí từ 2.058 đến 2.352 tỷ.",  # dấu chấm
    "Chi phí khoảng 2 tỷ 58 triệu – 2 tỷ 352 triệu.",
    "Chi phí từ 2.058.000.000 đến 2.352.000.000 đồng.",
    "Đơn giá tham chiếu 4.2–4.8 triệu/m².",
])
def test_rounding_and_formats_ok(estimate, answer):
    res = check(answer, [estimate])
    assert res.ok, res.violations


def test_correct_match_answer_ok(match):
    answer = ("Em tìm được 3 nhà thầu phù hợp với tiêu chí của anh/chị. Em đề xuất Kiến Phong vì đơn vị có kinh "
              "nghiệm thi công nhà phố, biệt thự tại TP. Hồ Chí Minh, có 7 công trình được ghi nhận trên Dezon, "
              "8 năm kinh nghiệm, 20 thợ và 12 văn phòng, thành lập năm 2018, đơn giá xây thô 4.3–4.7 triệu/m² "
              "(điểm phù hợp 88/100). Smartcons Tech có 8 công trình ghi nhận trên Dezon, Huy Hoàng có 6 công "
              "trình; đơn giá của hai đơn vị này: Chưa có dữ liệu.")
    res = check(answer, [match])
    assert res.ok, res.violations


def test_missing_price_answer_ok(huy_hoang_profile):
    answer = "Dạ, đơn giá xây thô của Huy Hoàng hiện Chưa có dữ liệu trên Dezon. Huy Hoàng có 6 công trình ghi nhận trên Dezon."
    assert check(answer, [huy_hoang_profile]).ok


# --------------------------------------------------------------------------------------------------
# check — câu bịa số
# --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("answer,bad", [
    ("Đơn giá xây thô của Kiến Phong là 5,2 triệu/m².", "5,2 triệu/m²"),
    ("Kiến Phong đã có 15 công trình trên Dezon.", "15 công trình"),
    ("Kiến Phong có 10 năm kinh nghiệm.", "10 năm"),
    ("Chi phí khoảng 2,2 tỷ.", "2,2 tỷ"),  # không khớp giá trị nào trong tool (±1%)
    ("Kiến Phong có 50 kỹ sư.", "50 kỹ sư"),
    ("Có 4 nhà thầu phù hợp.", "4 nhà thầu"),
])
def test_fabricated_numbers_blocked(match, estimate, answer, bad):
    res = check(answer, [estimate, match])
    assert not res.ok
    assert [v.rule for v in res.violations] == [RULE_UNTRACEABLE]
    assert res.violations[0].text == bad


def test_fabricated_price_for_contractor_without_price(huy_hoang_profile):
    res = check("Đơn giá xây thô của Huy Hoàng khoảng 4,5 triệu/m².", [huy_hoang_profile])
    assert not res.ok and res.violations[0].rule == RULE_UNTRACEABLE


def test_ids_and_internal_numbers_not_allowed(match):
    # id=2, rank=2 trong JSON không được dùng làm nguồn cho "2 công trình"
    assert not check("Smartcons Tech có 2 công trình.", [match]).ok


# --------------------------------------------------------------------------------------------------
# Cụm cấm
# --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("answer", [
    "Kiến Phong là nhà thầu tốt nhất tại TP. Hồ Chí Minh.",
    "Kiến Phong là đơn vị uy tín nhất.",
    "Kiến Phong là nhà thầu số 1 hiện nay.",
])
def test_best_claim_blocked(match, answer):
    res = check(answer, [match])
    assert [v.rule for v in res.violations] == [RULE_BEST]


@pytest.mark.parametrize("answer", [
    "Em không thể khẳng định nhà thầu nào tốt nhất vì Dezon chưa có dữ liệu đánh giá sau bàn giao.",
    "Dezon chưa có dữ liệu để kết luận đơn vị tốt nhất; Kiến Phong phù hợp nhất với tiêu chí của anh/chị.",
    "Kiến Phong phù hợp nhất với tiêu chí của anh/chị.",
    "Tốt nhất anh/chị nên xem hồ sơ dự án thực tế trước khi quyết định.",
    "Phương án số 1 là giảm một tầng.",
])
def test_best_claim_allowed(match, answer):
    res = check(answer, [match])
    assert res.ok, res.violations


def test_project_count_label(match):
    res = check("Kiến Phong có 7 công trình đã hoàn thành.", [match])
    assert [v.rule for v in res.violations] == [RULE_PROJECT_LABEL]
    res = check("Kiến Phong đã thi công xong 7 công trình.", [match])
    assert [v.rule for v in res.violations] == [RULE_PROJECT_LABEL]
    ok = check("Kiến Phong có 7 công trình ghi nhận trên Dezon (không đồng nghĩa đã hoàn thành).", [match])
    assert ok.ok, ok.violations


@pytest.mark.parametrize("answer", [
    "Đây là báo giá chính xác cho công trình của anh/chị: 2,058–2,352 tỷ.",
    "Chi phí chính xác là 2,058 tỷ.",
    "Khái toán này là báo giá thi công.",
])
def test_estimate_as_quote_blocked(estimate, answer):
    res = check(answer, [estimate])
    assert RULE_ESTIMATE_QUOTE in [v.rule for v in res.violations]
    assert RULE_UNTRACEABLE not in [v.rule for v in res.violations]


@pytest.mark.parametrize("answer", [
    "Đây là khái toán sơ bộ, không phải báo giá thi công.",
    "Khái toán này không phải là báo giá; vui lòng yêu cầu báo giá từ nhà thầu để có con số chính xác.",
    "Bước 2: anh/chị gửi yêu cầu báo giá để nhà thầu khảo sát.",
])
def test_estimate_disclaimer_allowed(estimate, answer):
    res = check(answer, [estimate])
    assert res.ok, res.violations


# --------------------------------------------------------------------------------------------------
# False positive: số thứ tự, tầng, số khách tự nói
# --------------------------------------------------------------------------------------------------
def test_false_positives_ordinals_and_user_numbers(match):
    user = ["Tôi muốn xây nhà phố 5x20 ở Thủ Đức, 1 trệt 3 lầu, xây thô, ngân sách 2 tỷ, gia đình 5 người"]
    answer = ("Dạ, em tóm tắt nhu cầu của anh/chị:\n1. Nhà phố 4 tầng (1 trệt 3 lầu), đất 100 m².\n"
              "2. Ngân sách 2 tỷ, gia đình 5 người.\nBước 2: em tìm 3 nhà thầu phù hợp. Tầng 1 có thể làm garage.")
    res = check(answer, [match], user_texts=user)
    assert res.ok, res.violations


def test_no_tool_results_any_metric_blocked():
    res = check("Đơn giá xây thô hiện nay khoảng 5 triệu/m².", [])
    assert not res.ok
    assert check("Dạ, anh/chị cho em biết loại công trình và số tầng dự kiến ạ?", []).ok


# --------------------------------------------------------------------------------------------------
# Retry message
# --------------------------------------------------------------------------------------------------
def test_build_retry_message():
    msg = build_retry_message([
        Violation(rule=RULE_UNTRACEABLE, text="5,2 triệu/m²", detail="x"),
        Violation(rule=RULE_BEST, text="tốt nhất", detail="y"),
    ])
    assert "5,2 triệu/m²" in msg and "tốt nhất" in msg
    assert "kết quả tool" in msg and NO_DATA in msg and "phù hợp nhất với tiêu chí của anh/chị" in msg


# --------------------------------------------------------------------------------------------------
# Vòng sinh lại với LLM mock
# --------------------------------------------------------------------------------------------------
class MockLLM:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str | None] = []

    def __call__(self, retry: str | None) -> str:
        self.prompts.append(retry)
        return self.answers[len(self.prompts) - 1]


def test_run_guarded_first_try_ok(match):
    llm = MockLLM(["Kiến Phong có 7 công trình ghi nhận trên Dezon."])
    res = run_guarded(llm, [match])
    assert res.text == llm.answers[0] and res.attempts == 1 and not res.used_fallback and not res.violations
    assert llm.prompts == [None]


def test_run_guarded_retry_then_ok(match):
    llm = MockLLM(["Đơn giá Kiến Phong là 5,2 triệu/m².", "Đơn giá xây thô của Kiến Phong là 4,3–4,7 triệu/m²."])
    logged = []
    res = run_guarded(llm, [("search_contractors", match)],
                      on_violation=lambda v, a, n: logged.append((n, [x.text for x in v])))
    assert res.text == llm.answers[1]
    assert res.attempts == 2 and not res.used_fallback
    assert llm.prompts[0] is None and "5,2 triệu/m²" in llm.prompts[1]
    assert logged == [(1, ["5,2 triệu/m²"])]


def test_run_guarded_falls_back_after_two_failures(match, estimate):
    llm = MockLLM(["Kiến Phong là nhà thầu tốt nhất, 15 công trình.", "Kiến Phong có 10 năm kinh nghiệm."])
    tools = [("calculate_estimate", estimate), ("search_contractors", match)]
    res = run_guarded(llm, tools)
    assert res.used_fallback and res.attempts == 2
    assert len(res.attempt_violations) == 2 and len(res.violations) >= 3
    assert "2,058–2,352 tỷ" in res.text and "Kiến Phong" in res.text
    assert "khái toán sơ bộ, không phải báo giá" in res.text
    assert check(res.text, tools).ok


def test_run_guarded_fallback_without_tools():
    llm = MockLLM(["Đơn giá 5 triệu/m².", "Đơn giá 6 triệu/m²."])
    res = run_guarded(llm, [])
    assert res.used_fallback
    assert extract_claims(res.text) == []
    assert "anh/chị" in res.text.lower()


# --------------------------------------------------------------------------------------------------
# record_violations (cần DB)
# --------------------------------------------------------------------------------------------------
@pytest.mark.db
def test_record_violations(db):
    from sqlalchemy import select

    from app.db.models import GuardrailViolation

    sid = uuid.uuid4()
    vs = [Violation(rule=RULE_UNTRACEABLE, text="15 công trình", detail="không có trong tool"),
          Violation(rule=RULE_BEST, text="tốt nhất", detail="cấm")]
    record_violations(db, str(sid), vs, answer="Kiến Phong tốt nhất, 15 công trình", attempt=1)
    rows = db.scalars(select(GuardrailViolation).where(GuardrailViolation.session_id == sid)
                      .order_by(GuardrailViolation.id)).all()
    assert [r.rule for r in rows] == [RULE_UNTRACEABLE, RULE_BEST]
    assert rows[0].detail == {"text": "15 công trình", "detail": "không có trong tool",
                              "answer": "Kiến Phong tốt nhất, 15 công trình", "attempt": 1}
    record_violations(db, None, [], answer="", attempt=2)  # không có vi phạm -> không ghi gì


# --------------------------------------------------------------------------------------------------
# Regression (review): các lỗ hổng / báo nhầm đã sửa
# --------------------------------------------------------------------------------------------------
USER_FULL = ["nhà phố 5x20, 1 trệt 3 lầu, ngân sách 2 tỷ, gia đình 5 người"]


@pytest.mark.parametrize("answer,bad", [
    ("Kiến Phong có 5 năm kinh nghiệm.", "5 năm"),  # 5 chỉ có trong "5x20"/"5 người" của khách
    ("Kiến Phong có 3 kỹ sư.", "3 kỹ sư"),  # 3 chỉ có trong "3 lầu"
    ("Có 1 nhà thầu phù hợp.", "1 nhà thầu"),  # 1 chỉ có trong "1 trệt"
])
def test_user_bare_numbers_do_not_validate_counts(match, answer, bad):
    res = check(answer, [match], user_texts=USER_FULL)
    assert [v.text for v in res.violations] == [bad]


def test_user_numbers_still_usable_for_area_money_and_typed_counts(match):
    ok = check("Đất 100 m², ngân sách 2 tỷ, gia đình 5 người.", [match], user_texts=USER_FULL)
    assert ok.ok, ok.violations


def test_score_and_percent_not_matched_against_arbitrary_numbers(estimate, match):
    # land_area_m2 = 100, staff workers = 20, converted_m2 = 40 không phải nguồn cho điểm/phần trăm.
    assert not check("Kiến Phong đạt 100/100 điểm.", [estimate, match]).ok
    assert not check("Chi phí có thể giảm 37%.", [estimate, match]).ok
    assert check("Kiến Phong đạt 88/100 điểm; tiêu chí khu vực chiếm 20%.", [estimate, match]).ok


@pytest.mark.parametrize("answer,expected", [
    ("Kiến Phong có mười năm kinh nghiệm.", "mười năm"),
    ("Kiến Phong có chín công trình ghi nhận trên Dezon.", "chín công trình"),
    ("Don gia xay tho la 5,2 trieu/m2.", "5,2 trieu/m2"),
    ("Kien Phong co 15 cong trinh.", "15 cong trinh"),
])
def test_word_numbers_and_unaccented_units_blocked(match, answer, expected):
    res = check(answer, [match])
    assert [v.text for v in res.violations] == [expected]


def test_unaccented_correct_numbers_ok(match):
    res = check("Kien Phong co 7 cong trinh, don gia 4,3-4,7 trieu/m2, 8 nam kinh nghiem.", [match])
    assert res.ok, res.violations


@pytest.mark.parametrize("answer", [
    "Hai đơn vị còn lại chưa có đơn giá; cả ba nhà thầu đều hoạt động tại TP. Hồ Chí Minh.",
    "Kiến Phong là một đơn vị thi công nhà phố.",
    "Đầu tư công trình nhà phố cần cân nhắc kỹ.",
    "Tiêu chí ưu tiên hàng đầu của anh/chị là ngân sách.",
    "Em hỗ trợ 24/7, anh/chị khởi công dự kiến 15/11.",
    "Kiến Phong không phải nhà thầu duy nhất phù hợp.",
    "Em không thể khẳng định Kiến Phong, Smartcons Tech hay Huy Hoàng là tốt nhất.",
    "Kiến Phong có 7 công trình ghi nhận trên Dezon, nhưng em không thể khẳng định đây là nhà thầu tốt nhất.",
    "Anh/chị có thể gửi yêu cầu báo giá: em sẽ chuyển tới 3 nhà thầu.",
    "Báo giá từ nhà thầu sẽ chính xác hơn khái toán.",
])
def test_more_false_positive_cases_ok(match, answer):
    res = check(answer, [match])
    assert res.ok, res.violations


@pytest.mark.parametrize("answer,rule", [
    ("Anh/chị hỏi về Kiến Phong: đây là nhà thầu tốt nhất.", RULE_BEST),  # phủ định không vượt dấu ":"
    ("Không phải ngẫu nhiên mà Kiến Phong là nhà thầu tốt nhất.", RULE_BEST),
    ("Dezon chưa có dữ liệu đánh giá, nhưng Kiến Phong là nhà thầu tốt nhất.", RULE_BEST),
    ("Kiến Phong là nhà thầu hàng đầu tại TP. Hồ Chí Minh.", RULE_BEST),
    ("Kiến Phong là đơn vị xuất sắc nhất.", RULE_BEST),
    ("Theo yêu cầu của anh/chị, đây là báo giá chính xác cho công trình.", RULE_ESTIMATE_QUOTE),
    ("Báo giá cho công trình của anh/chị là 2,058–2,352 tỷ.", RULE_ESTIMATE_QUOTE),
    ("Báo giá sơ bộ: 2,058–2,352 tỷ.", RULE_ESTIMATE_QUOTE),
])
def test_phrase_bypasses_blocked(estimate, match, answer, rule):
    res = check(answer, [estimate, match])
    assert [v.rule for v in res.violations] == [rule], res.violations


def test_rag_chunks_without_tool_name_not_a_number_source():
    from app.schemas.knowledge import KnowledgeChunk

    chunks = [KnowledgeChunk(entity_type="contractor", entity_id=1, content="Đơn giá trọn gói 6,5 triệu/m².",
                             score=0.9)]
    assert not check("Đơn giá trọn gói 6,5 triệu/m².", [chunks]).ok


def make_overview():
    from app.schemas.matching import ContractorOverview, OverviewContractor

    src = "Hồ sơ nhà thầu trên Dezon"
    m = make_match()
    entries = [OverviewContractor(card=r.contractor, has_projects_of_type=i < 2) for i, r in enumerate(m.results)]
    return ContractorOverview(
        province="TP. Hồ Chí Minh", building_type="biet_thu",
        contractor_count=ev(3, "3 nhà thầu", label=src),
        contractors_with_projects=ev(2, "2/3 nhà thầu có công trình biệt thự trong danh sách dự án trên Dezon",
                                     label=src),
        total_dezon_projects=ev(21, "21 công trình ghi nhận trên Dezon (tổng mọi loại công trình của các nhà thầu "
                                    "trên)", label=src),
        rough_price_range=ev([4_300_000, 4_700_000], "4,3–4,7 triệu/m²", label=src),
        finish_price_range=NA(),
        with_warranty_info=ev(0, "0/3 nhà thầu có thông tin bảo hành", label=src),
        verified_profiles=ev(0, "0/3 hồ sơ đã xác minh", label=src),
        contractors=entries,
    )


def test_fraction_counts_checked_against_overview():
    ov = make_overview()
    assert extract_claims("2/3 nhà thầu")[0].kind == CONTRACTOR_COUNT
    ok = check("Trong 3 nhà thầu, 2 nhà thầu (2/3 nhà thầu) có công trình biệt thự thực tế, tổng 21 công trình ghi "
               "nhận trên Dezon; 0/3 hồ sơ đã xác minh.", [ov])
    assert ok.ok, ok.violations
    bad = check("Có 4/5 nhà thầu có công trình biệt thự.", [ov])
    assert sorted(v.text for v in bad.violations) == ["4", "5 nhà thầu"]
