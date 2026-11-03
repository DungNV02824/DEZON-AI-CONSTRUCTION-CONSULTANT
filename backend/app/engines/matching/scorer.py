"""Chấm điểm và xếp hạng nhà thầu (đặc tả mục 7.1–7.3). Hàm thuần: không DB, không LLM, không mạng.

Trọng số luôn được TRUYỀN VÀO (đọc từ bảng matching_weights), không hard-code trong logic.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.engines.matching.criteria import (
    ContractorFacts,
    missing_important_fields,
    norm_place,
    profile_completeness,
    verified_ratio,
)
from app.formatting import format_vnd, format_vnd_range, round_to_million
from app.schemas.common import BUILDING_TYPE_VI, WORK_SCOPE_VI
from app.schemas.matching import FIT_HIGH, FIT_OK, FIT_REVIEW, CriterionScore, MatchCriteria

CRITERIA_KEYS: tuple[str, ...] = (
    "location", "building_type", "budget_fit", "experience", "similar_projects", "profile_quality",
)
CRITERIA_LABEL_VI: dict[str, str] = {
    "location": "Khu vực hoạt động",
    "building_type": "Loại công trình đã thực hiện",
    "budget_fit": "Mức độ phù hợp ngân sách",
    "experience": "Kinh nghiệm và năng lực",
    "similar_projects": "Dự án thực tế tương tự",
    "profile_quality": "Mức độ đầy đủ và xác minh hồ sơ",
}

FLAG_VERIFY_PRICE = "Cần xác minh mức giá"
FLAG_OVER_BUDGET = "over_budget"
FLAG_UNDER_BUDGET = "under_budget"
FLAG_SCOPE_NO_PRICE = "scope_no_contractor_price"

# Tham số công thức (mục 7.3) — là định nghĩa công thức, không phải trọng số.
NEUTRAL = 0.5
BUDGET_BAND_LOW = 0.9
OVERRUN_ZERO_AT = 0.40  # vượt > 40% -> 0
UNDER_BUDGET_SCORE = 0.8  # quyết định riêng: chi phí ước thấp hơn nhiều so với ngân sách (xem DECISIONS)
UNKNOWN_FLOORS_WEIGHT = 0.5  # dự án không có số tầng tính nửa dự án (xem DECISIONS)
HIGH_THRESHOLD = 85
OK_THRESHOLD = 70
MIN_CANDIDATES = 3

# Phạm vi thi công -> loại đơn giá nhà thầu dùng để ước chi phí. Hồ sơ Dezon chỉ có "Đơn giá thi công thô" (rough)
# và "Đơn giá hoàn thiện cơ bản" (finish) — mục sau là giá TRỌN GÓI (thô + hoàn thiện cơ bản), không phải giá riêng
# phần hoàn thiện. Hoàn thiện / nội thất: nhà thầu chưa công bố đơn giá riêng -> trung tính (xem docs/DECISIONS.md).
SCOPE_PRICE_FIELD: dict[str, str | None] = {
    "xay_tho": "rough",
    "hoan_thien": None,
    "tron_goi": "finish",
    "noi_that": None,
}
PRICE_KIND_VI = {"rough": "xây thô", "finish": "trọn gói (hoàn thiện cơ bản)"}


@dataclass
class ScoredContractor:
    facts: ContractorFacts
    score: int
    fit_label: str
    breakdown: list[CriterionScore]
    flags: list[str] = field(default_factory=list)
    estimated_cost: tuple[int, int] | None = None  # VND, làm tròn triệu
    price_kind: str | None = None  # rough | finish | None
    # Hồ sơ chỉ có cận trên của đơn giá -> estimated_cost là MỨC TỐI ĐA (lo = hi), hiển thị "tối đa …".
    cost_upper_only: bool = False
    location_kind: str = "none"  # district | province | project | neighbor | none
    relaxed_location: bool = False
    missing_important: list[str] = field(default_factory=list)
    has_type_capability: bool = False
    has_type_project: bool = False

    def criterion(self, key: str) -> CriterionScore | None:
        return next((c for c in self.breakdown if c.key == key), None)


@dataclass
class RankingResult:
    ranked: list[ScoredContractor]
    total_candidates: int
    relaxed_to_neighbors: bool
    notes: list[str]


# Khớp tỉnh chỉ qua công trình (không phải tỉnh trên hồ sơ/khu vực hoạt động) — xem docs/DECISIONS.md.
PROJECT_ONLY_LOCATION_SCORE = 0.6


# --- Tiện ích ----------------------------------------------------------------------------------
def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Lấy trọng số 6 tiêu chí từ dict (thiếu khóa = 0); chuẩn hóa để tổng = 1 nếu cần."""
    w = {k: float(weights.get(k, 0) or 0) for k in CRITERIA_KEYS}
    total = sum(w.values())
    if total <= 0:
        raise ValueError("Trọng số matching không hợp lệ (tổng = 0)")
    if abs(total - 1.0) > 1e-6:
        w = {k: v / total for k, v in w.items()}
    return w


def _places(facts: ContractorFacts) -> list[tuple[str | None, str | None]]:
    out: list[tuple[str | None, str | None]] = [(c.province, c.district) for c in facts.capabilities]
    out += [(p.province, p.district) for p in facts.projects]
    if facts.province:
        out.append((facts.province, None))
    return out


def operates_in(facts: ContractorFacts, province: str) -> bool:
    """Bộ lọc cứng 7.2: có capability hoặc dự án tại tỉnh (kèm tỉnh chính trên hồ sơ — xem DECISIONS)."""
    p = norm_place(province)
    return bool(p) and any(norm_place(pp) == p for pp, _ in _places(facts))


def operates_in_any(facts: ContractorFacts, provinces: Iterable[str]) -> str | None:
    wanted = {norm_place(x): x for x in provinces if x}
    for pp, _ in _places(facts):
        if norm_place(pp) in wanted:
            return wanted[norm_place(pp)]
    return None


# --- Từng tiêu chí (0..1) ----------------------------------------------------------------------
def score_location(facts: ContractorFacts, criteria: MatchCriteria,
                   neighbors: Iterable[str] = ()) -> tuple[float, str, str]:
    p, d = norm_place(criteria.province), norm_place(criteria.district)
    places = _places(facts)
    registered = [c.province for c in facts.capabilities] + [facts.province]
    registered_prov = any(norm_place(pp) == p for pp in registered if pp)

    def _in_district(items) -> bool:
        return any(norm_place(x.province) == p and norm_place(x.district) == d for x in items)

    # Khớp quận/huyện chỉ tính 1.0 khi tỉnh đó là khu vực đăng ký (capability tại quận, hoặc dự án tại quận của
    # nhà thầu đã đăng ký tỉnh). Dự án tại quận của nhà thầu ngoại tỉnh vẫn chỉ là "project" (không vượt sở tại).
    if d and (_in_district(facts.capabilities) or (registered_prov and _in_district(facts.projects))):
        return 1.0, "district", f"Khớp quận/huyện {criteria.district} ({criteria.province})"
    extra = "" if not criteria.district else f"; chưa ghi nhận hoạt động tại {criteria.district}"
    if registered_prov:
        return 0.8, "province", f"Khớp tỉnh/thành {criteria.province}{extra}"
    if any(norm_place(pp) == p for pp, _ in places):
        # Chỉ có công trình tại tỉnh của khách, hồ sơ đăng ký khu vực khác -> thấp hơn nhà thầu sở tại.
        if d and _in_district(facts.projects):
            where, extra = f"{criteria.district} ({criteria.province})", ""
        else:
            where = criteria.province
        return (PROJECT_ONLY_LOCATION_SCORE, "project",
                f"Có công trình tại {where}, hồ sơ chưa ghi khu vực hoạt động tại {criteria.province}{extra}")
    nb = operates_in_any(facts, neighbors)
    if nb:
        return 0.4, "neighbor", f"Chỉ hoạt động tại tỉnh lân cận ({nb}) của {criteria.province}"
    return 0.0, "none", f"Chưa ghi nhận hoạt động tại {criteria.province} hoặc tỉnh lân cận"


def score_building_type(facts: ContractorFacts, criteria: MatchCriteria) -> tuple[float, str, bool, bool]:
    bt = str(criteria.building_type)
    label = BUILDING_TYPE_VI.get(bt, bt).lower()
    has_cap = any(c.building_type == bt for c in facts.capabilities)
    has_proj = any(p.building_type == bt for p in facts.projects)
    if has_cap and has_proj:
        return 1.0, f"Có năng lực và có dự án {label} ghi nhận trên Dezon", has_cap, has_proj
    if has_cap:
        return 0.7, f"Có năng lực thi công {label}, chưa có dự án {label} trong danh sách trên Dezon", \
            has_cap, has_proj
    if has_proj:
        return 0.7, f"Có dự án {label} trên Dezon, chưa đăng ký năng lực {label}", has_cap, has_proj
    return 0.0, f"Chưa ghi nhận năng lực hoặc dự án {label}", has_cap, has_proj


def contractor_price(facts: ContractorFacts, kind: str | None) -> tuple[float, float] | None:
    if kind == "rough":
        lo, hi = facts.rough_price_min, facts.rough_price_max
    elif kind == "finish":
        lo, hi = facts.finish_price_min, facts.finish_price_max
    else:
        return None
    if lo is None and hi is None:
        return None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    return float(lo), float(hi)


def price_upper_only(facts: ContractorFacts, kind: str | None) -> bool:
    """Hồ sơ chỉ công bố cận trên (cận dưới mặc định của form đã bị bỏ) -> giá/chi phí là mức tối đa."""
    if kind not in ("rough", "finish"):
        return False
    return getattr(facts, f"{kind}_price_min") is None and getattr(facts, f"{kind}_price_max") is not None


def format_cost(cost: tuple[float, float], upper_only: bool = False) -> str:
    """Chuỗi chi phí ước: khoảng "a–b", hoặc "tối đa b" khi chỉ biết cận trên."""
    return f"tối đa {format_vnd(cost[1])}" if upper_only else format_vnd_range(*cost)


def estimate_cost(facts: ContractorFacts, work_scope: str | None,
                  converted_area_m2: float | None) -> tuple[tuple[int, int] | None, str | None]:
    """Chi phí ước = A_quy_doi × đơn giá nhà thầu (min–max), làm tròn triệu."""
    kind = SCOPE_PRICE_FIELD.get(str(work_scope)) if work_scope else None
    price = contractor_price(facts, kind)
    if price is None or not converted_area_m2 or converted_area_m2 <= 0:
        return None, kind
    return (round_to_million(converted_area_m2 * price[0]),
            round_to_million(converted_area_m2 * price[1])), kind


def budget_score(cost: tuple[float, float], budget: float, upper_only: bool = False) -> tuple[float, str]:
    """Quy tắc 7.3: 1.0 nếu khoảng chi phí giao [0,9B; 1,1B] và có phần ≤ B; vượt thì giảm tuyến tính
    theo % vượt (tính trên đầu thấp của khoảng), 0 khi vượt > 40%.

    `upper_only`: chỉ biết mức tối đa (cận dưới chưa rõ). Mức tối đa ≤ B -> xét như thường; mức tối đa > B ->
    không đủ căn cứ kết luận vượt ngân sách -> trung tính, không gắn cờ vượt ngân sách."""
    lo, hi = cost
    if budget <= 0:
        raise ValueError("Ngân sách phải > 0")
    if upper_only and hi > budget:
        return NEUTRAL, ""
    if lo > budget:
        overrun = (lo - budget) / budget
        return max(0.0, 1.0 - overrun / OVERRUN_ZERO_AT), FLAG_OVER_BUDGET
    if hi >= BUDGET_BAND_LOW * budget:
        return 1.0, ""
    return UNDER_BUDGET_SCORE, FLAG_UNDER_BUDGET


def score_budget_fit(facts: ContractorFacts, criteria: MatchCriteria, converted_area_m2: float | None
                     ) -> tuple[float, str, list[str], tuple[int, int] | None, str | None]:
    scope = str(criteria.work_scope) if criteria.work_scope else None
    est, kind = estimate_cost(facts, scope, converted_area_m2)
    flags: list[str] = []
    if scope is None:
        return NEUTRAL, "Chưa rõ phạm vi thi công — tính trung tính", flags, est, kind
    if kind is None:
        flags.append(FLAG_SCOPE_NO_PRICE)
        return (NEUTRAL, f"Nhà thầu chưa công bố đơn giá riêng cho phạm vi {WORK_SCOPE_VI[scope].lower()} "
                "trên Dezon — tính trung tính", flags, est, kind)
    if contractor_price(facts, kind) is None:
        flags.append(FLAG_VERIFY_PRICE)
        return (NEUTRAL, f"Chưa có đơn giá {PRICE_KIND_VI[kind]} — tính trung tính, {FLAG_VERIFY_PRICE.lower()}",
                flags, est, kind)
    if not criteria.budget_vnd or criteria.budget_vnd <= 0:
        return NEUTRAL, "Anh/chị chưa cung cấp ngân sách — tính trung tính", flags, est, kind
    if est is None:
        return NEUTRAL, "Chưa có diện tích quy đổi từ khái toán — tính trung tính", flags, est, kind
    upper_only = price_upper_only(facts, kind)
    score, flag = budget_score(est, float(criteria.budget_vnd), upper_only)
    cost_txt = format_cost(est, upper_only)
    budget_txt = format_vnd(criteria.budget_vnd)
    if upper_only and est[1] > criteria.budget_vnd:
        note = (f"Chi phí ước {cost_txt}, cao hơn ngân sách {budget_txt}; hồ sơ chỉ công bố mức tối đa "
                "— tính trung tính, cần đối chiếu báo giá")
    elif flag == FLAG_OVER_BUDGET:
        flags.append(flag)
        note = f"Chi phí ước {cost_txt} vượt ngân sách {budget_txt}"
    elif flag == FLAG_UNDER_BUDGET:
        flags.append(flag)
        note = f"Chi phí ước {cost_txt} thấp hơn nhiều so với ngân sách {budget_txt} — cần đối chiếu phạm vi"
    else:
        note = f"Chi phí ước {cost_txt} phù hợp ngân sách {budget_txt}"
    return score, note, flags, est, kind


def score_experience(facts: ContractorFacts) -> tuple[float, str]:
    years = facts.years_experience
    staff_parts = [v for v in (facts.staff_workers, facts.staff_office) if v is not None]
    staff_total = sum(staff_parts) if staff_parts else None
    s_years = min(years / 10, 1.0) if years is not None else 0.0
    s_staff = min(staff_total / 30, 1.0) if staff_total is not None else 0.0
    score = s_years * 0.6 + s_staff * 0.4
    y_txt = f"{years} năm kinh nghiệm" if years is not None else "chưa có số năm kinh nghiệm"
    st_txt = f"{staff_total} nhân sự" if staff_total is not None else "chưa có thông tin nhân sự"
    return score, f"{y_txt[0].upper()}{y_txt[1:]}; {st_txt}"


def count_similar_projects(facts: ContractorFacts, criteria: MatchCriteria) -> float:
    bt, p, floors = str(criteria.building_type), norm_place(criteria.province), criteria.floors
    n = 0.0
    for proj in facts.projects:
        if proj.building_type != bt or norm_place(proj.province) != p:
            continue
        if floors is None:
            n += 1
        elif proj.floors is None:
            n += UNKNOWN_FLOORS_WEIGHT
        elif abs(proj.floors - floors) <= 1:
            n += 1
    return n


def score_similar_projects(facts: ContractorFacts, criteria: MatchCriteria) -> tuple[float, str]:
    n = count_similar_projects(facts, criteria)
    label = BUILDING_TYPE_VI.get(str(criteria.building_type), "").lower()
    floors_txt = f", {criteria.floors}±1 tầng" if criteria.floors else ""
    n_txt = f"{n:g}".replace(".", ",")
    return min(n / 5, 1.0), f"{n_txt} dự án {label} tại {criteria.province}{floors_txt} trên Dezon"


def score_profile_quality(facts: ContractorFacts) -> tuple[float, str]:
    comp, ver = profile_completeness(facts), verified_ratio(facts)
    return comp * 0.7 + ver * 0.3, (f"Hồ sơ đầy đủ {_round_half_up(comp * 100)}%, "
                                    f"trường đã xác minh {_round_half_up(ver * 100)}%")


def fit_label(score: int, missing_important: list[str]) -> str:
    if len(missing_important) >= 2:
        return FIT_REVIEW
    if score >= HIGH_THRESHOLD:
        return FIT_HIGH
    if score >= OK_THRESHOLD:
        return FIT_OK
    return FIT_REVIEW


def score_contractor(facts: ContractorFacts, criteria: MatchCriteria, weights: Mapping[str, float],
                     neighbors: Iterable[str] = (), converted_area_m2: float | None = None) -> ScoredContractor:
    w = normalize_weights(weights)
    area = converted_area_m2 if converted_area_m2 is not None else criteria.converted_area_m2
    neighbors = list(neighbors)
    loc, loc_kind, loc_note = score_location(facts, criteria, neighbors)
    bt, bt_note, has_cap, has_proj = score_building_type(facts, criteria)
    bud, bud_note, flags, est, kind = score_budget_fit(facts, criteria, area)
    exp, exp_note = score_experience(facts)
    sim, sim_note = score_similar_projects(facts, criteria)
    pq, pq_note = score_profile_quality(facts)
    raw = {"location": (loc, loc_note), "building_type": (bt, bt_note), "budget_fit": (bud, bud_note),
           "experience": (exp, exp_note), "similar_projects": (sim, sim_note), "profile_quality": (pq, pq_note)}
    breakdown = [
        CriterionScore(key=k, label_vi=CRITERIA_LABEL_VI[k], score=round(raw[k][0], 3), weight=round(w[k], 4),
                       weighted=round(raw[k][0] * w[k] * 100, 2), note=raw[k][1])
        for k in CRITERIA_KEYS
    ]
    total = _round_half_up(round(sum(raw[k][0] * w[k] for k in CRITERIA_KEYS) * 100, 6))
    missing = missing_important_fields(facts)
    return ScoredContractor(
        facts=facts, score=total, fit_label=fit_label(total, missing), breakdown=breakdown, flags=flags,
        estimated_cost=est, price_kind=kind, cost_upper_only=est is not None and price_upper_only(facts, kind),
        location_kind=loc_kind,
        relaxed_location=loc_kind == "neighbor", missing_important=missing,
        has_type_capability=has_cap, has_type_project=has_proj,
    )


def _sort_key(s: ScoredContractor) -> tuple:
    return (-s.score, -(s.facts.dezon_project_count or 0), s.facts.name.casefold(), s.facts.id)


def rank_contractors(contractors: Iterable[ContractorFacts], criteria: MatchCriteria,
                     weights: Mapping[str, float], neighbors: Iterable[str] = (),
                     converted_area_m2: float | None = None, limit: int | None = None,
                     min_candidates: int = MIN_CANDIDATES) -> RankingResult:
    """Bộ lọc cứng (7.2) -> chấm điểm (7.3) -> xếp hạng, trả top `limit` (mặc định criteria.limit)."""
    limit = limit or criteria.limit
    neighbors = [n for n in neighbors if norm_place(n) != norm_place(criteria.province)]
    active = [c for c in contractors if c.is_active]
    local = [c for c in active if operates_in(c, criteria.province)]
    notes: list[str] = []
    candidates = list(local)
    relaxed = False
    if len(local) < min_candidates and neighbors:
        local_ids = {c.id for c in local}
        extra = [c for c in active if c.id not in local_ids and operates_in_any(c, neighbors)]
        if extra:
            relaxed = True
            candidates += extra
            notes.append(f"Chỉ có {len(local)} nhà thầu ghi nhận hoạt động tại {criteria.province}; em đã mở rộng "
                         f"sang tỉnh lân cận ({', '.join(neighbors)}).")
    if not candidates:
        notes.append(f"Chưa ghi nhận nhà thầu hoạt động tại {criteria.province} trên Dezon.")
    scored = [score_contractor(c, criteria, weights, neighbors, converted_area_m2) for c in candidates]
    scored.sort(key=_sort_key)
    return RankingResult(ranked=scored[:limit], total_candidates=len(candidates),
                         relaxed_to_neighbors=relaxed, notes=notes)
