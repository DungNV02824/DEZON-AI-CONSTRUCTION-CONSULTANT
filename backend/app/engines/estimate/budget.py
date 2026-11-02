"""Gợi ý điều chỉnh khi ngân sách thấp hơn khái toán (đặc tả mục 6.5).

Mỗi phương án = một thay đổi trên input của khách, sau đó CHẠY LẠI `calculate_estimate`
(đơn giá mới lấy qua `rate_lookup`) — không tự nhân hệ số tắt, không tự tạo số.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.schemas.common import QUALITY_TIER_VI, WorkScope
from app.schemas.estimate import BudgetOption, EstimateInput, EstimateResult, RateData, RuleSet

from . import rules as R
from .calculator import calculate_estimate

RateLookup = Callable[[EstimateInput], RateData | None]

_ROOF_CODES = (R.ROOF_METAL, R.ROOF_CONCRETE, R.ROOF_TILE_ON_TRUSS, R.ROOF_TILE_ON_CONCRETE)


@dataclass
class _Candidate:
    code: str
    label_vi: str
    description: str
    changes: dict[str, Any]


def apply_changes(inp: EstimateInput, changes: dict[str, Any]) -> EstimateInput:
    """Tạo input mới từ input gốc của khách (giữ nguyên trường khách chưa cung cấp -> mặc định tính lại)."""
    data = inp.model_dump(exclude_unset=True)
    data.update(changes)
    return EstimateInput.model_validate(data)


def _cheaper_roof(current: str, rules: RuleSet) -> str | None:
    """Loại mái có hệ số thấp hơn liền kề (theo bộ hệ số đang dùng)."""
    if current not in rules.rules:
        return None
    k_now = rules[current].coefficient
    cheaper = [(rules[c].coefficient, c) for c in _ROOF_CODES
               if c in rules.rules and rules[c].coefficient < k_now]
    return max(cheaper)[1] if cheaper else None


def _candidates(inp: EstimateInput, rules: RuleSet, current: EstimateResult) -> list[_Candidate]:
    eff = current.input
    groups = {it.group_name for it in current.items}
    out: list[_Candidate] = []

    if inp.floors >= 2:
        n = inp.floors - 1
        out.append(_Candidate("reduce_floor", f"Giảm 1 tầng (còn {n} tầng)",
                              "Bớt một tầng, giữ nguyên các thông số khác.", {"floors": n}))

    lower = R.lower_quality(inp.quality_tier)
    if lower is not None:
        out.append(_Candidate(
            "lower_quality", f"Hạ mức chất lượng xuống {QUALITY_TIER_VI[lower]}",
            f"Dùng vật tư mức {QUALITY_TIER_VI[lower]} thay cho mức {QUALITY_TIER_VI[inp.quality_tier]}.",
            {"quality_tier": lower.value}))

    if eff.basement and "ham" in groups:
        out.append(_Candidate("remove_basement", "Bỏ tầng hầm",
                              "Không làm tầng hầm, giữ nguyên các tầng nổi.",
                              {"basement": False, "basement_depth_m": None, "basement_area_m2": None}))

    if eff.roof_type in _ROOF_CODES and "mai" in groups:
        cheaper = _cheaper_roof(eff.roof_type, rules)
        if cheaper:
            out.append(_Candidate(
                "change_roof", f"Đổi sang {R.ROOF_LABEL_VI[cheaper]}",
                f"Thay {R.ROOF_LABEL_VI[eff.roof_type]} bằng {R.ROOF_LABEL_VI[cheaper]}.",
                {"roof_type": cheaper}))

    if eff.rooftop_terrace == "covered" and "san_thuong" in groups:
        out.append(_Candidate("open_rooftop", "Để sân thượng không mái che",
                              "Không làm mái che sân thượng.", {"rooftop_terrace": "open"}))

    if eff.has_mezzanine and "tang" in groups and any(it.rule_code == R.MEZZANINE for it in current.items):
        out.append(_Candidate("remove_mezzanine", "Bỏ tầng lửng", "Không làm tầng lửng.",
                              {"has_mezzanine": False, "mezzanine_area_m2": None}))

    if WorkScope(inp.work_scope) == WorkScope.tron_goi:
        out.append(_Candidate(
            "phase_rough_first", "Chia giai đoạn: làm phần xây thô trước",
            "Làm phần xây thô trước, phần hoàn thiện thực hiện sau khi có thêm ngân sách. "
            "Chi phí bên dưới chỉ là giai đoạn xây thô.",
            {"work_scope": WorkScope.xay_tho.value}))
    return out


def _derived_note(base: EstimateResult, new: EstimateResult) -> str:
    """Ghi chú các mặc định engine tính lại (vd đổi móng khi giảm tầng)."""
    if base.input.foundation_type and new.input.foundation_type != base.input.foundation_type \
            and new.input.foundation_type:
        return f" Móng tính lại theo mặc định: {R.FOUNDATION_LABEL_VI[new.input.foundation_type]}."
    return ""


def _to_option(cand: _Candidate, result: EstimateResult, current: EstimateResult,
               target_budget_vnd: int) -> BudgetOption:
    saving = (current.cost_min - result.cost_min
              if current.cost_min is not None and result.cost_min is not None else None)
    return BudgetOption(
        code=cand.code, label_vi=cand.label_vi,
        description=cand.description + _derived_note(current, result),
        changes=cand.changes, converted_area_m2=result.converted_area_m2,
        cost_min=result.cost_min, cost_max=result.cost_max, cost=result.cost, saving_min=saving,
        fits_budget=result.cost_min is not None and result.cost_min <= target_budget_vnd,
    )


def suggest_adjustments(inp: EstimateInput, rules: RuleSet, rate_lookup: RateLookup,
                        target_budget_vnd: int, current: EstimateResult,
                        today: date | None = None) -> list[BudgetOption]:
    """Các phương án điều chỉnh, sắp theo chi phí tăng dần; mỗi phương án do engine tính lại.

    `inp` là input GỐC của khách (chưa áp mặc định) để engine tính lại mặc định phù hợp
    (vd giảm từ 4 xuống 3 tầng -> móng mặc định đổi từ cọc sang băng).
    """
    def run(changes: dict[str, Any]) -> EstimateResult | None:
        new_inp = apply_changes(inp, changes)
        rate = rate_lookup(new_inp)
        if rate is None:
            return None
        res = calculate_estimate(new_inp, rules, rate, today=today)
        return res if res.cost_min is not None else None

    options: list[BudgetOption] = []
    for cand in _candidates(inp, rules, current):
        res = run(cand.changes)
        if res is None:
            continue
        opt = _to_option(cand, res, current, target_budget_vnd)
        if opt.saving_min is not None and opt.saving_min <= 0:
            continue
        options.append(opt)

    # Không phương án đơn lẻ nào vừa ngân sách -> thử kết hợp 2 phương án tiết kiệm nhiều nhất.
    if len(options) >= 2 and not any(o.fits_budget for o in options):
        top = sorted(options, key=lambda o: o.cost_min)
        first = top[0]
        second = next((o for o in top[1:] if not set(o.changes) & set(first.changes)), None)
        if second is not None:
            merged = {**first.changes, **second.changes}
            res = run(merged)
            if res is not None and res.cost_min < first.cost_min:
                cand = _Candidate("combined", f"Kết hợp: {first.label_vi} + {second.label_vi.lower()}",
                                  "Áp dụng đồng thời hai phương án trên.", merged)
                options.append(_to_option(cand, res, current, target_budget_vnd))

    options.sort(key=lambda o: (o.cost_min is None, o.cost_min or 0))
    return options
