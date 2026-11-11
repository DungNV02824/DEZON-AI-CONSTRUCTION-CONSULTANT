"""Engine phân tích báo giá so với khái toán (V2 — quotes) — thuần, không cần DB."""
import re
from datetime import date

import pytest
from pydantic import BaseModel

from app.engines.estimate import calculate_estimate
from app.engines.quote_analysis import (
    DISCLAIMER,
    LINES_AREA_INCOMPLETE_WARNING,
    LINES_AREA_WARNING,
    NO_COST_WARNING,
    NO_LINES_WARNING,
    NOT_VERIFIED_WARNING,
    compare_quote,
    outside_gap,
    range_position,
    relative_gap,
)
from app.evidence import EvidenceValue, VerificationStatus
from app.formatting import NO_DATA
from app.schemas.quote_analysis import QuoteData, QuoteLine
from tests.test_estimate_engine import make_input, make_rate, make_rules

TODAY = date(2026, 11, 2)


def _estimate(rate=True, **kw):
    # Nhà phố 100 m², 4 tầng -> móng cọc 0,4 + mái BTCT 0,5: A = 400 + 40 + 50 = 490 m²; 4,2–4,8 triệu/m².
    return calculate_estimate(make_input(**kw), make_rules(), make_rate() if rate else None, today=TODAY)


def _quote(**kw) -> QuoteData:
    base = dict(quote_id=1, contractor_name="Công ty Kiến Phong", received_at=date(2026, 11, 1))
    base.update(kw)
    return QuoteData(**base)


def _walk(obj):
    if isinstance(obj, EvidenceValue):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk(v)
    elif isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            yield from _walk(getattr(obj, name))


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def test_estimate_fixture():
    est = _estimate()
    assert est.converted_area_m2 == 490 and (est.cost_min, est.cost_max) == (2_058_000_000, 2_352_000_000)


# ------------------------------------------------------------------ công thức
def test_relative_gap_and_position():
    assert relative_gap(110, 90, 110) == pytest.approx(0.1)
    assert relative_gap(None, 1, 2) is None and relative_gap(5, 0, 0) is None
    assert range_position(5, 4, 6) == "within" and range_position(4, 4, 6) == "within"
    assert range_position(3, 4, 6) == "below" and range_position(7, 6, 4) == "above"
    assert range_position(None, 4, 6) == "not_comparable"
    assert outside_gap(5, 4, 6) is None
    assert outside_gap(6.6, 4, 6) == pytest.approx(0.1) and outside_gap(3, 4, 6) == pytest.approx(0.25)


# ------------------------------------------------------------------ tổng tiền
def test_quote_within_range_derives_price():
    res = compare_quote(_estimate(), _quote(total_amount=2_200_000_000, converted_area_m2=490), today=TODAY)
    t = res.total
    assert t.position == "within" and not t.notable and not res.notable
    assert t.estimate.display == "2,058–2,352 tỷ" and t.quote.display == "2,2 tỷ"
    assert t.gap_pct.value == pytest.approx((2.2 - 2.205) / 2.205, abs=1e-4) and t.gap_pct.display == "-0,2%"
    assert t.outside_pct.display == NO_DATA
    assert t.note_vi == "Trong khoảng khái toán; lệch -0,2% so với điểm giữa khoảng khái toán."
    # Đơn giá suy ra = 2,2 tỷ / 490 m²
    assert res.price_per_m2.quote.value == round(2_200_000_000 / 490) and res.price_per_m2.position == "within"
    assert any("suy ra" in w for w in res.warnings)
    assert res.area.position == "within" and res.area.note_vi == "Nhà thầu tính diện tích quy đổi bằng khái toán."


def test_quote_above_range_neutral_wording():
    res = compare_quote(_estimate(), _quote(total_amount=2_500_000_000, price_per_m2=5_000_000,
                                            converted_area_m2=500), today=TODAY)
    t = res.total
    assert t.position == "above" and t.notable and res.notable
    assert t.outside_pct.display == "6,3%"  # (2,5 − 2,352) / 2,352
    assert t.gap_pct.display == "+13,4%"  # (2,5 − 2,205) / 2,205
    assert t.note_vi.startswith("Cao hơn khoảng khái toán 6,3% (so với mức cao của khoảng)")
    p = res.price_per_m2
    assert p.position == "above" and p.estimate.display == "4,2–4,8 triệu/m²" and p.quote.display == "5 triệu/m²"
    assert p.outside_pct.display == "4,2%"
    assert res.area.position == "above" and res.area.gap_pct.display == "+2%"
    assert res.area.note_vi == "Nhà thầu tính diện tích quy đổi lớn hơn khái toán 2%."


def test_quote_below_range():
    res = compare_quote(_estimate(), _quote(total_amount=1_900_000_000), today=TODAY)
    assert res.total.position == "below" and res.total.notable
    assert res.total.note_vi.startswith("Thấp hơn khoảng khái toán 7,7% (so với mức thấp của khoảng)")
    # Không có diện tích hay đơn giá -> chưa so được đơn giá/m², không tách chênh lệch
    assert res.price_per_m2.position == "not_comparable" and res.price_per_m2.quote.display == NO_DATA
    assert res.breakdown is None


def test_breakdown_adds_up():
    est = _estimate()
    res = compare_quote(est, _quote(total_amount=2_600_000_000, price_per_m2=5_000_000, converted_area_m2=520),
                        today=TODAY)
    b = res.breakdown
    assert b is not None
    # ΔC = 2,6 tỷ − 2,205 tỷ = 395 triệu; diện tích: 30 m² × 4,5 triệu = 135 triệu; đơn giá: 520 × 0,5 triệu = 260 triệu
    assert b.total_gap.value == 395_000_000 and b.total_gap.display == "+395 triệu"
    assert b.area_effect.value == 135_000_000 and b.price_effect.value == 260_000_000
    assert b.other_effect.value == 0
    assert b.total_gap.value == b.area_effect.value + b.price_effect.value + b.other_effect.value
    assert "chủ yếu do đơn giá/m² khác" in b.note_vi


def test_no_rate_gives_unavailable():
    res = compare_quote(_estimate(rate=False), _quote(total_amount=2_200_000_000, converted_area_m2=480),
                        today=TODAY)
    assert res.total.estimate.display == NO_DATA and res.total.position == "not_comparable"
    assert res.total.gap_pct == EvidenceValue.unavailable()
    assert res.total.note_vi == "Khái toán chưa có chi phí."
    assert res.price_per_m2.estimate.display == NO_DATA
    assert NO_COST_WARNING in res.warnings
    assert res.area.position == "below"  # diện tích vẫn so được
    assert res.breakdown is None


# ------------------------------------------------------------------ hạng mục
def test_no_lines_only_totals():
    res = compare_quote(_estimate(), _quote(total_amount=2_200_000_000), today=TODAY)
    assert not res.lines_compared and NO_LINES_WARNING in res.warnings
    assert {ln.group for ln in res.lines} == {"tang", "mong", "mai"}
    assert all(ln.position == "not_comparable" and ln.quote_amount.display == NO_DATA for ln in res.lines)
    tang = next(ln for ln in res.lines if ln.group == "tang")
    assert tang.estimate_area.display == "400 m²" and tang.estimate_cost.display == "1,68–1,92 tỷ"


def test_lines_compared_by_group():
    lines = [
        QuoteLine(group="tang", label="Tầng trệt + 3 lầu", area_m2=400, coefficient=1, amount_vnd=2_000_000_000),
        QuoteLine(group="mong", label="Móng cọc", rule_code="foundation_pile", area_m2=100, coefficient=0.5,
                  amount_vnd=200_000_000),
        QuoteLine(group="khac", label="Ép cọc", amount_vnd=150_000_000),
        QuoteLine(group="khac", label="Hồ cá Koi", amount_vnd=50_000_000),
    ]
    res = compare_quote(_estimate(), _quote(total_amount=2_400_000_000, lines=lines), today=TODAY)
    by = {ln.label_vi: ln for ln in res.lines}
    tang = by["Tầng (trệt, lầu, lửng)"]
    assert tang.position == "above" and tang.notable  # 2 tỷ > 1,92 tỷ
    assert tang.quote_amount.display == "2 tỷ" and tang.quote_area.display == "400 m²"
    mong = by["Móng"]
    # 40 m² × 4,2–4,8 triệu = 168–192 triệu; 200 triệu -> cao hơn; hệ số 0,5 so với 0,4
    assert mong.estimate_cost.display == "168–192 triệu" and mong.position == "above"
    assert "Móng cọc (chưa gồm ép cọc): nhà thầu tính hệ số 0,5, khái toán tính 0,4." in mong.notes
    assert "Nhà thầu tính diện tích quy đổi lớn hơn khái toán 25%." in mong.notes
    assert by["Mái"].position == "missing_in_quote" and not by["Mái"].notable
    ep = by["Ép cọc"]
    assert ep.position == "extra_in_quote" and ep.estimate_cost.display == NO_DATA
    assert ep.notes[0].startswith("Khái toán không có hạng mục này; có thể thuộc phần khái toán chưa bao gồm: "
                                  "Chi phí ép cọc")
    assert by["Hồ cá Koi"].notes == ["Khái toán không có hạng mục này."]
    assert res.lines_compared and NO_LINES_WARNING not in res.warnings
    joined = " ".join(res.summary)
    assert "2 hạng mục ngoài khoảng khái toán: Móng, Tầng (trệt, lầu, lửng)." in joined
    assert "Báo giá có 2 hạng mục khái toán không có: Ép cọc, Hồ cá Koi." in joined
    assert "chưa tách riêng 1 hạng mục của khái toán: Mái." in joined
    # Dòng không phủ nhóm Mái -> không cộng diện tích quy đổi từ các dòng (400 + 50 sẽ nhỏ giả tạo)
    assert res.area.quote.display == NO_DATA and res.area.position == "not_comparable"
    assert LINES_AREA_INCOMPLETE_WARNING in res.warnings and LINES_AREA_WARNING not in res.warnings


def test_partial_lines_do_not_give_quote_area():
    # Nhân viên chỉ ghi các dòng khác hệ số (Móng, Mái) và bỏ trống diện tích quy đổi, đơn giá/m².
    lines = [QuoteLine(group="mong", label="Móng cọc (chưa gồm ép cọc)", rule_code="foundation_pile", area_m2=100,
                       coefficient=0.5),
             QuoteLine(group="mai", label="Mái bê tông cốt thép", rule_code="roof_concrete", area_m2=100,
                       coefficient=0.7)]
    res = compare_quote(_estimate(), _quote(total_amount=2_300_000_000, lines=lines), today=TODAY)
    assert res.total.position == "within"
    assert res.area.position == "not_comparable" and res.area.quote.display == NO_DATA and not res.area.notable
    assert res.price_per_m2.position == "not_comparable" and not res.price_per_m2.notable
    assert res.breakdown is None
    assert LINES_AREA_INCOMPLETE_WARNING in res.warnings
    assert not any("suy ra" in w or "cộng từ các dòng" in w for w in res.warnings)
    assert not any("chủ yếu do" in s for s in res.summary)


def test_lines_covering_every_item_give_quote_area():
    lines = [QuoteLine(group="tang", label="Tầng trệt + 3 lầu", area_m2=400, coefficient=1),
             QuoteLine(group="mong", label="Móng cọc", rule_code="foundation_pile", area_m2=100, coefficient=0.4),
             QuoteLine(group="mai", label="Mái BTCT", rule_code="roof_concrete", area_m2=100, coefficient=0.5),
             QuoteLine(group="khac", label="Ép cọc", amount_vnd=150_000_000)]
    res = compare_quote(_estimate(), _quote(total_amount=2_200_000_000, lines=lines), today=TODAY)
    assert res.area.quote.value == 490 and res.area.position == "within"
    assert LINES_AREA_WARNING in res.warnings and LINES_AREA_INCOMPLETE_WARNING not in res.warnings
    assert res.price_per_m2.quote.value == round(2_200_000_000 / 490) and res.breakdown is not None


def test_group_with_some_items_compares_matching_share():
    # Nhóm "tang" có 4 hạng mục (Tầng trệt, Lầu 1–3); chỉ dòng Tầng trệt có số.
    lines = [QuoteLine(group="tang", label="Tầng trệt", rule_code="floor_standard", amount_vnd=420_000_000)]
    res = compare_quote(_estimate(), _quote(total_amount=2_200_000_000, converted_area_m2=490, lines=lines),
                        today=TODAY)
    tang = next(ln for ln in res.lines if ln.group == "tang")
    assert tang.missing_items == ["Lầu 1", "Lầu 2", "Lầu 3"]
    assert tang.estimate_area.display == "100 m²" and tang.estimate_cost.display == "420–480 triệu"
    assert tang.position == "within" and not tang.notable
    assert tang.notes[0].startswith("Báo giá chỉ có dòng cho 1/4 hạng mục của nhóm nên so với phần khái toán tương "
                                    "ứng (Tầng trệt); chưa có dòng: Lầu 1, Lầu 2, Lầu 3.")
    assert not res.notable and not any("ngoài khoảng khái toán:" in s for s in res.summary)
    assert any("chưa có: Lầu 1, Lầu 2, Lầu 3" in s for s in res.summary)
    # Dòng tự do (không có mã hệ số) coi như phủ cả nhóm -> so với cả nhóm như trước
    lines = [QuoteLine(group="tang", label="Phần thân", amount_vnd=420_000_000)]
    tang = next(ln for ln in compare_quote(_estimate(), _quote(total_amount=2_200_000_000, lines=lines),
                                           today=TODAY).lines if ln.group == "tang")
    assert tang.missing_items == [] and tang.position == "below" and tang.estimate_cost.display == "1,68–1,92 tỷ"


def test_extra_line_exclusion_hint_uses_keywords():
    lines = [QuoteLine(group="khac", label="Điện nước", amount_vnd=80_000_000),
             QuoteLine(group="khac", label="Xin giấy phép xây dựng", amount_vnd=20_000_000),
             QuoteLine(group="khac", label="Khoan cọc nhồi", amount_vnd=100_000_000)]
    res = compare_quote(_estimate(), _quote(total_amount=2_200_000_000, lines=lines), today=TODAY)
    by = {ln.label_vi: ln for ln in res.lines}
    assert by["Điện nước"].notes == ["Khái toán không có hạng mục này."]
    assert by["Xin giấy phép xây dựng"].notes == [
        "Khái toán không có hạng mục này; có thể thuộc phần khái toán chưa bao gồm: Chi phí xin giấy phép xây dựng "
        "và các thủ tục pháp lý."]
    assert "Chi phí ép cọc hoặc khoan cọc" in by["Khoan cọc nhồi"].notes[0]


def test_line_with_area_only_compares_area():
    lines = [QuoteLine(group="mai", label="Mái BTCT", area_m2=100, coefficient=0.5)]
    res = compare_quote(_estimate(), _quote(total_amount=2_200_000_000, converted_area_m2=490, lines=lines),
                        today=TODAY)
    mai = next(ln for ln in res.lines if ln.group == "mai")
    assert mai.position == "within" and mai.quote_amount.display == NO_DATA
    assert mai.notes == ["Nhà thầu tính diện tích quy đổi bằng khái toán."]


# ------------------------------------------------------------------ nguồn, trạng thái, câu chữ
def test_status_and_source():
    res = compare_quote(_estimate(), _quote(total_amount=2_200_000_000, file_url="https://zalo.me/f/abc"),
                        today=TODAY)
    assert res.quote_status == VerificationStatus.reference and NOT_VERIFIED_WARNING in res.warnings
    assert res.quote_source.label == "Báo giá của Công ty Kiến Phong nhận ngày 01/11/2026"
    assert res.quote_source.url == "https://zalo.me/f/abc"
    assert res.total.quote.status == VerificationStatus.reference

    res = compare_quote(_estimate(), _quote(total_amount=2_200_000_000, verified=True, file_url="javascript:x"),
                        today=TODAY)
    assert res.quote_status == VerificationStatus.verified and NOT_VERIFIED_WARNING not in res.warnings
    assert res.total.quote.status == VerificationStatus.verified
    assert res.quote_source.url is None  # chỉ nhận link http(s)
    # Kết luận chênh lệch = trạng thái yếu nhất (đơn giá khái toán là dữ liệu tham khảo)
    assert res.total.gap_pct.status == VerificationStatus.reference


def test_every_value_is_evidence_and_wording_neutral():
    lines = [QuoteLine(group="tang", label="Tầng", amount_vnd=1_000_000_000), QuoteLine(label="Khác")]
    for est in (_estimate(), _estimate(rate=False)):
        res = compare_quote(est, _quote(total_amount=900_000_000, price_per_m2=3_000_000, lines=lines),
                            today=TODAY)
        for ev in _walk(res):
            if ev.value is None:
                assert ev.display == NO_DATA and ev.status == VerificationStatus.unavailable
            else:
                assert ev.display and ev.display != NO_DATA
        text = " ".join(_strings(res.model_dump(mode="json"))).lower()
        assert "tốt nhất" not in text
        assert not re.search(r"\b(đắt|rẻ)\b", text)
        assert res.disclaimer == DISCLAIMER and "không phải báo giá" in res.disclaimer
