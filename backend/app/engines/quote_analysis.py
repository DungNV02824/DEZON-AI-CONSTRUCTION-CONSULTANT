"""Phân tích báo giá nhà thầu so với khái toán (V2 — quotes).

Hàm thuần: không DB, không mạng, không LLM. Đầu vào là snapshot `EstimateResult` (estimate_service.get_estimate)
và `QuoteData` do nhân viên nhập; mọi con số đầu ra do code tính và đóng gói thành EvidenceValue.

- Tổng tiền so với [cost_min, cost_max]; đơn giá/m² so với [P_min, P_max]; diện tích quy đổi so với A_quy_doi.
- "Đáng chú ý" (notable) = nằm ngoài khoảng [min, max] của khái toán; % chênh so với điểm giữa luôn được tính.
- Tách chênh lệch tổng tiền: ΔC = (A_q − A_e)·P_mid + A_q·(P_q − P_mid) + phần còn lại.
- Theo nhóm hạng mục (estimate_rules.group_name): chi phí nhóm = Σ A_i×k_i × [P_min, P_max] (làm tròn triệu).

Câu chữ trung tính ("cao hơn khoảng khái toán X%"); không đánh giá, không xếp hạng nhà thầu theo giá.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from app.engines.estimate.rules import weakest_status
from app.evidence import EvidenceValue, SourceRef, VerificationStatus, make_evidence
from app.formatting import (
    format_m2,
    format_price_per_m2_range,
    format_vnd,
    format_vnd_range,
    round_to_million,
    vi_number,
)
from app.schemas.estimate import EstimateItem, EstimateResult
from app.schemas.quote_analysis import (
    QUOTE_GROUP_VI,
    GapBreakdown,
    Position,
    QuoteAnalysisResult,
    QuoteComparison,
    QuoteData,
    QuoteLine,
    QuoteLineAnalysis,
)

DISCLAIMER = ("Khái toán là con số sơ bộ, không phải báo giá thi công; chênh lệch có thể do phạm vi công việc, "
              "vật liệu, điều kiện khu đất và cách tính diện tích khác nhau. Phân tích chỉ để đối chiếu với khái "
              "toán, không đánh giá hay xếp hạng nhà thầu.")
NO_LINES_WARNING = ("Báo giá chưa nhập dòng hạng mục nên chỉ so được tổng tiền, đơn giá/m² và diện tích quy đổi.")
NOT_VERIFIED_WARNING = "Báo giá chưa được đối chiếu với file gốc nên chỉ là dữ liệu tham khảo."
REFERENCE_RATE_WARNING = ("Đơn giá dùng trong khái toán là dữ liệu tham khảo nên kết luận chênh lệch chỉ mang tính "
                          "tham khảo.")
NO_COST_WARNING = ("Khái toán chưa có chi phí (chưa có đơn giá cho phạm vi/khu vực này) nên chỉ so được diện tích "
                   "quy đổi và hạng mục.")
DERIVED_PRICE_WARNING = "Đơn giá/m² của báo giá được suy ra = tổng tiền / diện tích quy đổi của nhà thầu."
DERIVED_AREA_WARNING = "Diện tích quy đổi của báo giá được suy ra = tổng tiền / đơn giá/m² của nhà thầu."
LINES_AREA_WARNING = "Diện tích quy đổi của báo giá được cộng từ các dòng hạng mục."
LINES_AREA_INCOMPLETE_WARNING = (
    "Dòng hạng mục chưa phủ đủ mọi hạng mục của khái toán (hoặc có dòng thiếu diện tích quy đổi) nên không cộng diện "
    "tích quy đổi từ các dòng; nhập diện tích quy đổi của nhà thầu để so được diện tích và đơn giá/m².")
_EPS = 1e-9

# Từ khóa (cả cụm từ trong diễn giải dòng báo giá) gắn với từng câu "chưa bao gồm" của khái toán
# (app/engines/estimate/explain.py). Khóa = cụm có trong câu chưa bao gồm; chỉ khớp theo từ khóa, không khớp chuỗi tự do.
_EXCLUSION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ép cọc", ("ép cọc", "khoan cọc", "cọc khoan nhồi", "cọc ép", "cọc bê tông", "ép cừ", "cừ larsen")),
    ("giấy phép", ("giấy phép", "xin phép", "cấp phép", "pháp lý")),
    ("xin phép", ("giấy phép", "xin phép", "cấp phép")),
    ("thiết kế", ("thiết kế", "bản vẽ")),
    ("đấu nối", ("điện nước ngoài nhà", "ngoài nhà", "đấu nối", "hạ tầng")),
    ("vật tư hoàn thiện", ("vật tư hoàn thiện", "ốp lát", "lát nền", "gạch ốp", "gạch lát", "sơn", "sơn nước",
                           "trần thạch cao", "đóng trần", "thiết bị vệ sinh", "thiết bị điện", "cửa", "cửa nhôm")),
    ("nội thất rời", ("nội thất", "giường", "tủ bếp", "tủ quần áo", "sofa", "rèm")),
    ("tháo dỡ", ("tháo dỡ", "phá dỡ", "gia cố nhà lân cận")),
    ("thiết bị đặc thù", ("thang máy", "điều hòa", "máy lạnh", "năng lượng mặt trời", "pin mặt trời", "hồ bơi")),
    ("phần xây thô", ("xây thô", "phần thô", "kết cấu")),
    ("thiết bị điện tử", ("thiết bị điện tử", "đồ gia dụng")),
    ("ban quản lý", ("ban quản lý",)),
)


# ------------------------------------------------------------------ công thức dùng chung
def relative_gap(value: float | int | None, lo: float | int | None, hi: float | int | None) -> float | None:
    """(value − điểm giữa [lo, hi]) / điểm giữa; dương = cao hơn. Thiếu số hoặc điểm giữa ≤ 0 -> None.

    Dùng chung với chỉ số "sai số trung vị" (metrics_service.estimate_accuracy)."""
    if value is None or lo is None or hi is None:
        return None
    mid = (float(lo) + float(hi)) / 2
    if mid <= 0:
        return None
    return (float(value) - mid) / mid


def range_position(value: float | int | None, lo: float | int | None, hi: float | int | None) -> Position:
    if value is None or lo is None or hi is None:
        return "not_comparable"
    lo_f, hi_f = sorted((float(lo), float(hi)))
    v = float(value)
    if v < lo_f - _EPS:
        return "below"
    if v > hi_f + _EPS:
        return "above"
    return "within"


def outside_gap(value: float | int | None, lo: float | int | None, hi: float | int | None) -> float | None:
    """Phần vượt ra ngoài khoảng, luôn dương: trên cận trên -> (v − hi)/hi; dưới cận dưới -> (lo − v)/lo."""
    pos = range_position(value, lo, hi)
    if pos == "not_comparable" or pos == "within":
        return None
    lo_f, hi_f = sorted((float(lo), float(hi)))  # type: ignore[arg-type]
    v = float(value)  # type: ignore[arg-type]
    if pos == "above":
        return (v - hi_f) / hi_f if hi_f > 0 else None
    return (lo_f - v) / lo_f if lo_f > 0 else None


def _pct(r: float) -> str:
    return f"{vi_number(abs(r) * 100, 1)}%"


def _signed_pct(r: float) -> str:
    sign = "+" if r > 0.0005 else "-" if r < -0.0005 else ""
    return f"{sign}{_pct(r)}"


def _signed_vnd(x: float) -> str:
    sign = "+" if x > 0 else "-" if x < 0 else ""
    return f"{sign}{format_vnd(abs(round_to_million(x)))}"


def _price(x: float) -> str:
    return format_price_per_m2_range(x, x)


def _line_converted(line: QuoteLine) -> float | None:
    if line.converted_m2 is not None:
        return float(line.converted_m2)
    if line.area_m2 is not None and line.coefficient is not None:
        return round(float(line.area_m2) * float(line.coefficient), 2)
    return None


def _sum_or_none(values: list[float | int | None]) -> float | None:
    got = [float(v) for v in values if v is not None]
    return sum(got) if got else None


# ------------------------------------------------------------------ câu chữ trung tính
def _range_note(pos: Position, gap: float | None, outside: float | None, *, reason: str = "") -> str:
    if pos == "not_comparable" or gap is None:
        return reason or "Chưa so được (thiếu số liệu)."
    mid = f"lệch {_signed_pct(gap)} so với điểm giữa khoảng khái toán"
    if pos == "above":
        return f"Cao hơn khoảng khái toán {_pct(outside or 0)} (so với mức cao của khoảng); {mid}."
    if pos == "below":
        return f"Thấp hơn khoảng khái toán {_pct(outside or 0)} (so với mức thấp của khoảng); {mid}."
    return f"Trong khoảng khái toán; {mid}."


def _area_note(gap: float | None, pos: Position, who: str = "Nhà thầu tính diện tích quy đổi") -> str:
    if gap is None or pos == "not_comparable":
        return "Chưa so được diện tích quy đổi (thiếu số liệu)."
    if pos == "within":
        return f"{who} bằng khái toán."
    word = "lớn hơn" if pos == "above" else "nhỏ hơn"
    return f"{who} {word} khái toán {_pct(gap)}."


# ------------------------------------------------------------------ engine
def compare_quote(estimate: EstimateResult, quote: QuoteData, today: date | None = None) -> QuoteAnalysisResult:
    """So một báo giá nhà thầu với khái toán. Thiếu số -> EvidenceValue.unavailable() ("Chưa có dữ liệu")."""
    warnings: list[str] = []
    q_status = VerificationStatus.verified if quote.verified else VerificationStatus.reference
    if not quote.verified:
        warnings.append(NOT_VERIFIED_WARNING)
    name = (quote.contractor_name or "").strip() or "nhà thầu"
    when = f" nhận ngày {quote.received_at.strftime('%d/%m/%Y')}" if quote.received_at else ""
    url = quote.file_url if quote.file_url and quote.file_url.lower().startswith(("http://", "https://")) else None
    q_ref = SourceRef(label=f"Báo giá của {name}{when}", url=url, updated_at=quote.received_at)

    def q_ev(value: object, display: str) -> EvidenceValue:
        return make_evidence(value, display, source=q_ref, status=q_status, today=today)

    def gap_ev(r: float | None, est_status: VerificationStatus | str, display: str | None = None) -> EvidenceValue:
        if r is None:
            return EvidenceValue.unavailable()
        return make_evidence(round(r, 4), display or _signed_pct(r), source=q_ref,
                             status=weakest_status(est_status, q_status), today=today)

    rate = estimate.rate.value if estimate.rate.has_value else None
    p_lo, p_hi = (float(rate[0]), float(rate[1])) if rate else (None, None)
    p_mid = (p_lo + p_hi) / 2 if rate else None  # type: ignore[operator]
    c_lo, c_hi = (estimate.cost_min, estimate.cost_max) if estimate.cost.has_value else (None, None)
    a_e = float(estimate.converted_area_m2)
    if c_lo is None:
        warnings.append(NO_COST_WARNING)
    elif estimate.cost.status == VerificationStatus.reference:
        warnings.append(REFERENCE_RATE_WARNING)

    # --- diện tích & đơn giá của báo giá (điền suy ra khi thiếu, kèm cảnh báo) ------------------------
    groups, q_groups = _group_items(estimate), _group_lines(quote)
    a_q: float | None = float(quote.converted_area_m2) if quote.converted_area_m2 else None
    if a_q is None:
        # Chỉ cộng từ các dòng khi các dòng phủ đủ mọi hạng mục của khái toán; dòng chỉ ghi phần khác hệ số
        # (vd chỉ Móng + Mái) sẽ cho diện tích nhỏ giả tạo -> đơn giá/m² suy ra sai.
        a_q, incomplete = _lines_area(groups, q_groups, quote.lines)
        if a_q is not None:
            warnings.append(LINES_AREA_WARNING)
        elif incomplete:
            warnings.append(LINES_AREA_INCOMPLETE_WARNING)
    p_q: float | None = float(quote.price_per_m2) if quote.price_per_m2 else None
    total = float(quote.total_amount) if quote.total_amount is not None else None
    if p_q is None and total and a_q:
        p_q = round(total / a_q)
        warnings.append(DERIVED_PRICE_WARNING)
    elif a_q is None and total and p_q:
        a_q = round(total / p_q, 2)
        warnings.append(DERIVED_AREA_WARNING)

    # --- tổng tiền ----------------------------------------------------------------------------------
    pos = range_position(total, c_lo, c_hi)
    gap = relative_gap(total, c_lo, c_hi)
    out = outside_gap(total, c_lo, c_hi)
    reason = ("Báo giá chưa có tổng tiền." if total is None else "Khái toán chưa có chi phí." if c_lo is None else "")
    total_cmp = QuoteComparison(
        label_vi="Tổng tiền", estimate=estimate.cost,
        quote=q_ev(int(total), format_vnd(total)) if total is not None else EvidenceValue.unavailable(),
        position=pos, gap_pct=gap_ev(gap, estimate.cost.status),
        outside_pct=gap_ev(out, estimate.cost.status, _pct(out)) if out is not None else EvidenceValue.unavailable(),
        notable=pos in ("above", "below"), note_vi=_range_note(pos, gap, out, reason=reason))

    # --- đơn giá/m² -----------------------------------------------------------------------------------
    pos = range_position(p_q, p_lo, p_hi)
    gap = relative_gap(p_q, p_lo, p_hi)
    out = outside_gap(p_q, p_lo, p_hi)
    reason = ("Báo giá chưa có đơn giá/m² (và không suy ra được)." if p_q is None
              else "Khái toán chưa có đơn giá." if p_lo is None else "")
    price_cmp = QuoteComparison(
        label_vi="Đơn giá/m² (theo diện tích quy đổi)", estimate=estimate.rate,
        quote=q_ev(int(p_q), _price(p_q)) if p_q is not None else EvidenceValue.unavailable(),
        position=pos, gap_pct=gap_ev(gap, estimate.rate.status),
        outside_pct=gap_ev(out, estimate.rate.status, _pct(out)) if out is not None else EvidenceValue.unavailable(),
        notable=pos in ("above", "below"), note_vi=_range_note(pos, gap, out, reason=reason))

    # --- diện tích quy đổi ----------------------------------------------------------------------------
    est_area = make_evidence(a_e, format_m2(a_e), status=estimate.rules_status, today=today) if a_e > 0 \
        else EvidenceValue.unavailable()
    pos = range_position(round(a_q, 2), a_e, a_e) if a_q is not None and a_e > 0 else "not_comparable"
    gap = relative_gap(a_q, a_e, a_e) if a_e > 0 else None
    area_cmp = QuoteComparison(
        label_vi="Diện tích sàn quy đổi", estimate=est_area,
        quote=q_ev(a_q, format_m2(a_q)) if a_q is not None else EvidenceValue.unavailable(),
        position=pos, gap_pct=gap_ev(gap, estimate.rules_status),
        outside_pct=gap_ev(abs(gap), estimate.rules_status, _pct(gap))
        if gap is not None and pos != "within" else EvidenceValue.unavailable(),
        notable=pos in ("above", "below"), note_vi=_area_note(gap, pos))

    # --- tách chênh lệch tổng tiền ----------------------------------------------------------------------
    breakdown = None
    if total is not None and c_lo is not None and p_mid is not None and a_q is not None and p_q is not None:
        c_mid = (float(c_lo) + float(c_hi)) / 2  # type: ignore[arg-type]
        d_total = total - c_mid
        d_area = (a_q - a_e) * p_mid
        d_price = a_q * (p_q - p_mid)
        d_other = d_total - d_area - d_price
        st = weakest_status(estimate.cost.status, q_status)

        def ev(x: float) -> EvidenceValue:
            return make_evidence(round_to_million(x), _signed_vnd(x), source=q_ref, status=st, today=today)

        parts = {"diện tích quy đổi khác": d_area, "đơn giá/m² khác": d_price,
                 "phần còn lại (phạm vi, hạng mục, làm tròn)": d_other}
        main = max(parts, key=lambda k: abs(parts[k]))
        note = ("Tổng tiền gần bằng điểm giữa khoảng khái toán." if abs(round_to_million(d_total)) == 0
                else f"Chênh lệch so với điểm giữa khoảng khái toán ({_signed_vnd(d_total)}) chủ yếu do {main}.")
        breakdown = GapBreakdown(total_gap=ev(d_total), area_effect=ev(d_area), price_effect=ev(d_price),
                                 other_effect=ev(d_other), note_vi=note)

    # --- theo nhóm hạng mục -------------------------------------------------------------------------
    lines = _compare_lines(estimate, quote, groups, q_groups, rate, q_ev, gap_ev, today)
    if not quote.lines:
        warnings.append(NO_LINES_WARNING)

    notable = total_cmp.notable or price_cmp.notable or any(ln.notable for ln in lines)
    summary = [f"{total_cmp.label_vi}: {total_cmp.note_vi}", f"{price_cmp.label_vi}: {price_cmp.note_vi}",
               f"{area_cmp.label_vi}: {area_cmp.note_vi}"]
    if breakdown is not None:
        summary.append(breakdown.note_vi)
    outside = [ln.label_vi for ln in lines if ln.notable]
    if outside:
        summary.append(f"{len(outside)} hạng mục ngoài khoảng khái toán: {', '.join(outside)}.")
    extra = [ln.label_vi for ln in lines if ln.position == "extra_in_quote"]
    if extra:
        summary.append(f"Báo giá có {len(extra)} hạng mục khái toán không có: {', '.join(extra)}.")
    missing = [ln.label_vi for ln in lines if ln.position == "missing_in_quote"]
    if missing:
        summary.append(f"Báo giá chưa tách riêng {len(missing)} hạng mục của khái toán: {', '.join(missing)}.")
    partial = [f"{ln.label_vi} (chưa có: {', '.join(ln.missing_items)})" for ln in lines if ln.missing_items]
    if partial:
        summary.append(f"Báo giá chỉ có dòng cho một phần hạng mục của nhóm, đã so với phần khái toán tương ứng: "
                       f"{'; '.join(partial)}.")

    return QuoteAnalysisResult(
        estimate_session_id=estimate.estimate_session_id, quote_id=quote.quote_id,
        contractor_name=quote.contractor_name, quote_status=q_status, quote_source=q_ref,
        total=total_cmp, price_per_m2=price_cmp, area=area_cmp, breakdown=breakdown, lines=lines,
        lines_compared=bool(quote.lines), scope_text=quote.scope_text, exclusions=list(estimate.exclusions),
        notable=notable, summary=summary, disclaimer=DISCLAIMER, warnings=warnings)


def _norm(text: str | None) -> str:
    return " ".join(unicodedata.normalize("NFC", text or "").lower().split())


def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _exclusion_for(label: str, exclusions: list[str]) -> str | None:
    """Câu "chưa bao gồm" của khái toán mà diễn giải dòng có thể thuộc về — theo từ khóa gắn với từng câu
    (`_EXCLUSION_KEYWORDS`), không khớp chuỗi tự do (vd "Điện nước" không khớp "Điện nước ngoài nhà…")."""
    lab = _norm(label)
    if not lab:
        return None
    for exc in exclusions:
        e = _norm(exc)
        for marker, keywords in _EXCLUSION_KEYWORDS:
            if marker in e and any(_has_phrase(lab, kw) for kw in keywords):
                return exc
    return None


def _group_items(estimate: EstimateResult) -> dict[str, list[EstimateItem]]:
    groups: dict[str, list[EstimateItem]] = {}
    for it in estimate.items:
        groups.setdefault(it.group_name, []).append(it)
    return groups


def _group_lines(quote: QuoteData) -> dict[str, list[QuoteLine]]:
    q_groups: dict[str, list[QuoteLine]] = {}
    for ln in quote.lines:
        q_groups.setdefault(ln.group, []).append(ln)
    return q_groups


def _match_group(items: list[EstimateItem], qlines: list[QuoteLine]
                 ) -> tuple[list[tuple[EstimateItem, QuoteLine]], list[QuoteLine], list[EstimateItem]]:
    """Ghép dòng báo giá với hạng mục khái toán trong một nhóm: theo rule_code + diễn giải, rồi theo rule_code.

    Trả (cặp đã ghép, dòng tự do, hạng mục chưa có dòng). Dòng tự do = không có rule_code hoặc mã không thuộc nhóm
    (nhân viên tự thêm, vd "Tầng trệt + 3 lầu"): không biết dòng gộp những hạng mục nào nên coi như phủ phần còn lại
    của nhóm -> khi có dòng tự do thì không có hạng mục "chưa có dòng"."""
    left = list(range(len(items)))
    pairs: list[tuple[EstimateItem, QuoteLine]] = []
    free: list[QuoteLine] = []
    codes = {it.rule_code for it in items}
    for ln in qlines:
        code = ln.rule_code or ""
        if not code or code not in codes:
            free.append(ln)
            continue
        same_label = [i for i in left if items[i].rule_code == code and _norm(items[i].label_vi) == _norm(ln.label)]
        same_code = [i for i in left if items[i].rule_code == code]
        pick = (same_label or same_code or [None])[0]
        if pick is None:  # thêm một dòng nữa cho hạng mục đã ghép -> gắn với hạng mục đầu tiên cùng mã
            pairs.append((next(it for it in items if it.rule_code == code), ln))
            continue
        left.remove(pick)
        pairs.append((items[pick], ln))
    missing = [] if free else [items[i] for i in left]
    return pairs, free, missing


def _lines_area(groups: dict[str, list[EstimateItem]], q_groups: dict[str, list[QuoteLine]],
                lines: list[QuoteLine]) -> tuple[float | None, bool]:
    """Σ diện tích quy đổi các dòng, chỉ khi các dòng phủ đủ mọi hạng mục của khái toán và mọi dòng thuộc nhóm của
    khái toán đều có diện tích quy đổi. Trả (diện tích | None, True nếu có số nhưng không đủ điều kiện để cộng)."""
    total = _sum_or_none([_line_converted(ln) for ln in lines])
    if total is None:
        return None, False
    for g, items in groups.items():
        qlines = q_groups.get(g, [])
        if not qlines or _match_group(items, qlines)[2] or any(_line_converted(ln) is None for ln in qlines):
            return None, True
    return total, False


def _coef_notes(items: list[EstimateItem], pairs: list[tuple[EstimateItem, QuoteLine]],
                free: list[QuoteLine]) -> list[str]:
    notes: list[str] = []
    checks = list(pairs)
    if not pairs and len(items) == 1 and len(free) == 1:
        checks.append((items[0], free[0]))
    for item, ln in checks:
        if ln.coefficient is not None and abs(float(ln.coefficient) - float(item.coefficient)) > 1e-6:
            notes.append(f"{item.label_vi}: nhà thầu tính hệ số {vi_number(ln.coefficient, 3)}, khái toán tính "
                         f"{vi_number(item.coefficient, 3)}.")
    return notes


def _compare_lines(estimate: EstimateResult, quote: QuoteData, groups: dict[str, list[EstimateItem]],
                   q_groups: dict[str, list[QuoteLine]], rate: list | None, q_ev, gap_ev,
                   today: date | None) -> list[QuoteLineAnalysis]:
    q_groups = dict(q_groups)
    cost_status = estimate.cost.status if estimate.cost.has_value else estimate.rate.status
    out: list[QuoteLineAnalysis] = []

    for g, items in groups.items():
        qlines = q_groups.pop(g, [])
        pairs, free, missing = _match_group(items, qlines) if qlines else ([], [], [])
        # Nhóm chỉ có dòng cho một phần hạng mục -> so với phần khái toán tương ứng, không so với cả nhóm.
        compared = [it for it in items if not any(it is m for m in missing)]
        conv = round(sum(float(i.converted_m2) for i in compared), 2)
        est_area = make_evidence(conv, format_m2(conv), status=estimate.rules_status, today=today)
        lo = hi = None
        est_cost = EvidenceValue.unavailable()
        if rate and estimate.cost.has_value and conv > 0:
            lo, hi = round_to_million(conv * float(rate[0])), round_to_million(conv * float(rate[1]))
            est_cost = make_evidence([lo, hi], format_vnd_range(lo, hi), source=estimate.rate.source,
                                     status=estimate.cost.status, today=today)
        label = QUOTE_GROUP_VI.get(g, g)
        row = QuoteLineAnalysis(group=g, label_vi=label, estimate_area=est_area,
                                quote_area=EvidenceValue.unavailable(), estimate_cost=est_cost,
                                quote_amount=EvidenceValue.unavailable())
        if not quote.lines:
            row.notes = ["Báo giá chưa nhập dòng hạng mục."]
            out.append(row)
            continue
        if not qlines:
            row.position = "missing_in_quote"
            row.notes = ["Báo giá chưa tách riêng hạng mục này (có thể đã gộp vào hạng mục khác)."]
            out.append(row)
            continue
        notes: list[str] = []
        if missing:
            row.missing_items = [it.label_vi for it in missing]
            notes.append(f"Báo giá chỉ có dòng cho {len(compared)}/{len(items)} hạng mục của nhóm nên so với phần "
                         f"khái toán tương ứng ({', '.join(it.label_vi for it in compared)}); chưa có dòng: "
                         f"{', '.join(row.missing_items)}.")
        q_conv = _sum_or_none([_line_converted(ln) for ln in qlines])
        q_amt = _sum_or_none([ln.amount_vnd for ln in qlines])
        if q_conv is not None:
            row.quote_area = q_ev(round(q_conv, 2), format_m2(round(q_conv, 2)))
        if q_amt is not None:
            row.quote_amount = q_ev(int(q_amt), format_vnd(q_amt))
        if q_amt is not None and lo is not None:
            row.position = range_position(q_amt, lo, hi)
            gap = relative_gap(q_amt, lo, hi)
            row.gap_pct = gap_ev(gap, cost_status)
            notes.append(_range_note(row.position, gap, outside_gap(q_amt, lo, hi)))
        elif q_conv is not None and conv > 0:
            row.position = range_position(round(q_conv, 2), conv, conv)
            gap = relative_gap(q_conv, conv, conv)
            row.gap_pct = gap_ev(gap, estimate.rules_status)
            notes.append(_area_note(gap, row.position))
        else:
            notes.append("Chưa so được (dòng báo giá chưa có thành tiền hoặc diện tích quy đổi).")
        if q_conv is not None and q_amt is not None and lo is not None and conv > 0:
            notes.append(_area_note(relative_gap(q_conv, conv, conv),
                                    range_position(round(q_conv, 2), conv, conv)))
        notes.extend(_coef_notes(items, pairs, free))
        row.notes = notes
        row.notable = row.position in ("above", "below")
        out.append(row)

    for g, qlines in q_groups.items():  # nhóm có trong báo giá mà khái toán không có (gồm "khac")
        for ln in qlines:
            label = ln.label.strip() or QUOTE_GROUP_VI.get(g, g)
            conv = _line_converted(ln)
            exc = _exclusion_for(ln.label, estimate.exclusions)
            out.append(QuoteLineAnalysis(
                group=g, label_vi=label, estimate_area=EvidenceValue.unavailable(),
                quote_area=q_ev(conv, format_m2(conv)) if conv is not None else EvidenceValue.unavailable(),
                estimate_cost=EvidenceValue.unavailable(),
                quote_amount=q_ev(int(ln.amount_vnd), format_vnd(ln.amount_vnd))
                if ln.amount_vnd is not None else EvidenceValue.unavailable(),
                position="extra_in_quote",
                notes=[f"Khái toán không có hạng mục này; có thể thuộc phần khái toán chưa bao gồm: {exc}." if exc
                       else "Khái toán không có hạng mục này."]))
    return out
