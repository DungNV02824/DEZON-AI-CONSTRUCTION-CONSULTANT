"""Test câu trả lời dự phòng (đặc tả mục 2.1): luôn dựng từ dữ liệu tool và luôn qua validator."""
import uuid

import pytest

from app.ai.fallback import (
    ESTIMATE_NOTE,
    GENERIC_APOLOGY,
    PROJECT_COUNT_NOTE,
    build_fallback_text,
    ensure_named,
    infer_tool_name,
)
from app.ai.validator import check, extract_claims
from app.evidence import EvidenceValue, SourceRef, VerificationStatus
from app.formatting import NO_DATA
from app.schemas.estimate import BudgetAdjustmentResult, BudgetOption
from app.schemas.matching import ComparedContractor, ComparisonMatrix, ComparisonRow, MatchResult
from tests.test_validator import (
    OBS,
    ev,
    make_estimate,
    make_huy_hoang_profile,
    make_kien_phong_profile,
    make_match,
)

NA = EvidenceValue.unavailable


def make_budget() -> BudgetAdjustmentResult:
    est = make_estimate()
    return BudgetAdjustmentResult(
        estimate_session_id=est.estimate_session_id,
        target_budget_vnd=1_500_000_000,
        target_budget=EvidenceValue(value=1_500_000_000, display="1,5 tỷ", status=VerificationStatus.reference,
                                    source=SourceRef(label="Khách cung cấp")),
        current_cost=est.cost,
        options=[
            BudgetOption(code="reduce_floor", label_vi="Giảm 1 tầng", description="Còn 3 tầng",
                         changes={"floors": 3}, converted_area_m2=390, cost_min=1_638_000_000,
                         cost_max=1_872_000_000, cost=ev([1_638_000_000, 1_872_000_000], "1,638–1,872 tỷ"),
                         saving_min=420_000_000, fits_budget=False),
            BudgetOption(code="phase_rough_first", label_vi="Chia giai đoạn, xây thô trước",
                         description="Làm phần thô các tầng trước", changes={"floors": 2}, converted_area_m2=290,
                         cost_min=1_218_000_000, cost_max=1_392_000_000,
                         cost=ev([1_218_000_000, 1_392_000_000], "1,218–1,392 tỷ"), fits_budget=True),
        ],
    )


def make_comparison() -> ComparisonMatrix:
    m = make_match()
    cards = [r.contractor for r in m.results]
    keys = [("area", "Khu vực", "province"), ("dezon_project_count", "Công trình trên Dezon", "dezon_project_count"),
            ("experience", "Kinh nghiệm", "years_experience"), ("rough_price", "Đơn giá thô", "rough_price"),
            ("finish_price", "Đơn giá hoàn thiện", "finish_price"), ("warranty", "Bảo hành", "warranty")]
    rows = [ComparisonRow(key=k, label_vi=label, cells=[c.evidence.get(e, NA()) for c in cards])
            for k, label, e in keys]
    rows.append(ComparisonRow(key="fit", label_vi="Mức phù hợp",
                              cells=[ev(r.fit_label, r.fit_label) for r in m.results]))
    return ComparisonMatrix(
        contractors=[ComparedContractor(id=c.id, slug=c.slug, name=c.name, profile_url=c.profile_url) for c in cards],
        rows=rows)


def make_requirements() -> dict:
    return {"slots": {"building_type": "nha_pho", "province": "TP. Hồ Chí Minh", "land_area_m2": 100, "floors": None,
                      "work_scope": None, "budget_vnd": 2_000_000_000, "confirmed": False},
            "missing_required": ["floors", "work_scope"]}


def make_rates() -> dict:
    return {"rate": ev([4_200_000, 4_800_000], "4,2–4,8 triệu/m²", label="Đơn giá tham chiếu (seed dev)",
                       status=VerificationStatus.reference).model_dump(mode="json"),
            "rate_data": {"id": 1, "building_type": "nha_pho", "province": "*", "work_scope": "xay_tho",
                          "quality_tier": "trung_binh", "price_min": 4_200_000, "price_max": 4_800_000,
                          "effective_from": "2026-10-01", "is_fallback": True}}


def make_knowledge() -> dict:
    return {"chunks": [
        {"content": "Kiến Phong là đơn vị thiết kế và thi công nhà phố, biệt thự theo phong cách Indochine. "
                    "Đơn giá trọn gói khoảng 6,5 triệu/m² tùy công trình. Đơn vị nhận thi công tại TP. Hồ Chí Minh.",
         "source": {"label": "Hồ sơ nhà thầu Kiến Phong", "url": "https://dezon.vn/nha-thau/kien-phong"}},
    ]}


def make_quote() -> dict:
    return {"ids": [str(uuid.uuid4()), str(uuid.uuid4())], "code": "DZ-20261102-0007"}


TOOL_CASES = [
    ("update_project_requirements", make_requirements),
    ("calculate_estimate", make_estimate),
    ("suggest_budget_adjustments", make_budget),
    ("search_contractors", make_match),
    ("get_contractor_profile", make_kien_phong_profile),
    ("get_contractor_profile", make_huy_hoang_profile),
    ("compare_contractors", make_comparison),
    ("get_reference_rates", make_rates),
    ("search_knowledge", make_knowledge),
    ("create_quote_request", make_quote),
]


@pytest.mark.parametrize("as_dict", [False, True])
@pytest.mark.parametrize("name,builder", TOOL_CASES)
def test_fallback_passes_validator(name, builder, as_dict):
    result = builder()
    if as_dict and hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    tools = [(name, result)]
    text = build_fallback_text(tools)
    assert text and text != GENERIC_APOLOGY
    res = check(text, tools)
    assert res.ok, (text, res.violations)
    assert "Dạ" in text
    assert "tốt nhất" not in text


def test_fallback_estimate_content():
    text = build_fallback_text([("calculate_estimate", make_estimate())])
    assert "2,058–2,352 tỷ" in text and "4,2–4,8 triệu/m²" in text
    assert ESTIMATE_NOTE in text and "khái toán sơ bộ, không phải báo giá" in text
    assert "31/10/2026" in text  # ngày cập nhật đơn giá
    assert "Ép cọc" in text


def test_fallback_estimate_without_rate():
    est = make_estimate()
    est = est.model_copy(update={"cost_min": None, "cost_max": None, "cost": NA(), "rate": NA()})
    text = build_fallback_text([("calculate_estimate", est)])
    assert NO_DATA in text
    assert not [c for c in extract_claims(text) if c.kind in ("money", "unit_price")]
    assert check(text, [est]).ok


def test_fallback_match_content():
    text = build_fallback_text([("search_contractors", make_match())])
    assert "3 nhà thầu" in text and "Kiến Phong" in text and "Huy Hoàng" in text
    assert "7 công trình được ghi nhận trên Dezon" in text
    assert PROJECT_COUNT_NOTE in text
    assert "https://dezon.vn/nha-thau/kien-phong" in text


def test_fallback_empty_match():
    m = make_match()
    empty = MatchResult(weights_version=1, criteria=m.criteria, total_candidates=0, results=[])
    text = build_fallback_text([("search_contractors", empty)])
    assert "chưa tìm thấy" in text and check(text, [empty]).ok


def test_fallback_profile_missing_data():
    p = make_huy_hoang_profile()
    text = build_fallback_text([("get_contractor_profile", p)])
    assert "Đơn giá xây thô: Chưa có dữ liệu" in text
    assert "Công trình ghi nhận trên Dezon: 6 công trình" in text
    assert check(text, [p]).ok


def test_fallback_knowledge_drops_numbers():
    text = build_fallback_text([("search_knowledge", make_knowledge())])
    assert "6,5 triệu" not in text
    assert extract_claims(text) == []
    assert "Indochine" in text


def test_fallback_comparison_has_no_blank_cells():
    text = build_fallback_text([("compare_contractors", make_comparison())])
    assert "Đơn giá thô: Kiến Phong: 4,3–4,7 triệu/m²; Smartcons Tech: Chưa có dữ liệu; Huy Hoàng: Chưa có dữ liệu" in text
    assert "3 nhà thầu" in text


def test_fallback_multiple_tools_and_validator():
    tools = [("calculate_estimate", make_estimate()), ("search_contractors", make_match()),
             ("create_quote_request", make_quote())]
    text = build_fallback_text(tools)
    assert "2,058–2,352 tỷ" in text and "Kiến Phong" in text and "DZ-20261102-0007" in text
    assert check(text, tools).ok


def test_fallback_no_tools():
    assert build_fallback_text([]) == GENERIC_APOLOGY
    assert extract_claims(GENERIC_APOLOGY) == []
    assert check(GENERIC_APOLOGY, []).ok
    assert build_fallback_text([("unknown_tool", {"x": 1})]) == GENERIC_APOLOGY


@pytest.mark.parametrize("name,builder", TOOL_CASES)
def test_infer_tool_name(name, builder):
    result = builder()
    assert infer_tool_name(result) == name
    assert ensure_named(result) == (name, result)
    if hasattr(result, "model_dump"):
        assert infer_tool_name(result.model_dump(mode="json")) == name


def test_stale_rate_note():
    r = make_rates()
    r["rate"]["is_stale"] = True
    text = build_fallback_text([("get_reference_rates", r)])
    assert "có thể đã cũ" in text and str(OBS.year) in text


# --------------------------------------------------------------------------------------------------
# Regression (review)
# --------------------------------------------------------------------------------------------------
def test_fallback_overview_passes_validator():
    from tests.test_validator import make_overview

    ov = make_overview()
    for tools in ([("get_contractor_overview", ov)], [("whatever_name", ov.model_dump(mode="json"))]):
        text = build_fallback_text(tools)
        assert "2/3 nhà thầu có công trình biệt thự" in text and "Đơn giá hoàn thiện tham khảo: Chưa có dữ liệu" in text
        assert PROJECT_COUNT_NOTE in text and "tốt nhất" not in text
        res = check(text, tools)
        assert res.ok, (text, res.violations)
    assert infer_tool_name(ov) == "get_contractor_overview"


def test_fallback_reference_rates_service_tuple():
    est = make_estimate()
    for result in ((est.rate, None), (NA(), None)):
        tools = [("get_reference_rates", result)]
        text = build_fallback_text(tools)
        assert text != GENERIC_APOLOGY and check(text, tools).ok
    assert "4,2–4,8 triệu/m²" in build_fallback_text([("get_reference_rates", (est.rate, None))])
    assert NO_DATA in build_fallback_text([("get_reference_rates", (NA(), None))])
    assert infer_tool_name((est.rate, None)) == "get_reference_rates"


def test_fallback_knowledge_chunk_list():
    from app.schemas.knowledge import KnowledgeChunk

    chunks = [KnowledgeChunk(entity_type="contractor", entity_id=1, entity_name="Kiến Phong",
                             content="Kiến Phong thi công nhà phố phong cách Indochine. Đơn giá 6,5 triệu/m².",
                             score=0.9, url="https://dezon.vn/nha-thau/kien-phong")]
    assert infer_tool_name(chunks) == "search_knowledge"
    text = build_fallback_text([ensure_named(chunks)])
    assert "Indochine" in text and "6,5" not in text and "Kiến Phong" in text
    assert check(text, [chunks]).ok


def test_fallback_template_error_does_not_raise():
    # Kết quả hỏng hình dạng (vd options không phải list dict) -> bỏ qua, không làm hỏng lượt chat.
    assert build_fallback_text([("suggest_budget_adjustments", {"options": [1, 2]})]) == GENERIC_APOLOGY


def test_source_note_non_iso_date():
    r = make_rates()
    r["rate"]["source"]["updated_at"] = "31/10/2026"
    text = build_fallback_text([("get_reference_rates", r)])
    assert "4,2–4,8 triệu/m²" in text and check(text, [("get_reference_rates", r)]).ok
