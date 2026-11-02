"""Calculation Engine — khái toán chi phí (đặc tả mục 6.1–6.3).

Hàm thuần: không DB, không mạng, không LLM. Mọi đơn giá đến từ `RateData` do service chọn,
mọi hệ số đến từ `RuleSet`; engine chỉ áp công thức

    A_quy_doi = Σ A_i × k_i ;  C_min/max = A_quy_doi × P_min/max  (làm tròn triệu)

và ghi mọi giá trị mặc định vào `assumptions`.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.evidence import EvidenceValue, VerificationStatus, make_evidence
from app.formatting import format_m2, format_price_per_m2_range, format_vnd_range, round_to_million
from app.schemas.common import (
    BUILDING_TYPE_VI,
    QUALITY_TIER_VI,
    WORK_SCOPE_VI,
    BuildingType,
    WorkScope,
)
from app.schemas.estimate import EstimateInput, EstimateItem, EstimateResult, RateData, RuleSet

from . import explain
from . import rules as R

RULES_NOT_APPROVED_WARNING = "Hệ số khái toán chưa được Dezon phê duyệt chính thức"
_TWO = Decimal("0.01")


def _d(x: float | int | Decimal) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _r2(x: Decimal) -> float:
    return float(x.quantize(_TWO, rounding=ROUND_HALF_UP))


@dataclass
class _Builder:
    rules: RuleSet
    items: list[EstimateItem] = field(default_factory=list)
    used_approved: list[bool] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total: Decimal = Decimal(0)

    def add(self, code: str, area: float, label: str | None = None) -> None:
        rule = self.rules.rules.get(code)
        if rule is None:
            self.warnings.append(
                f"Chưa có hệ số khái toán cho hạng mục “{code}”, em chưa tính hạng mục này vào diện tích quy đổi")
            return
        area_d = _d(area)
        converted = area_d * _d(rule.coefficient)
        self.total += converted
        self.used_approved.append(rule.approved)
        self.items.append(EstimateItem(
            rule_code=rule.code, label_vi=label or rule.label_vi, group_name=rule.group_name,
            area_m2=_r2(area_d), coefficient=rule.coefficient, converted_m2=_r2(converted)))

    def label(self, code: str) -> str:
        rule = self.rules.rules.get(code)
        return rule.label_vi if rule else code


def _province_label(province: str | None) -> str:
    p = (province or "").strip()
    return p if p and p != "*" else "khu vực của anh/chị"


def _rate_mismatch(inp: EstimateInput, rate: RateData) -> list[str]:
    """Các trường của đơn giá không khớp input (đơn giá phải được chọn đúng theo input)."""
    out: list[str] = []
    if str(rate.building_type) != str(inp.building_type):
        out.append(f"loại công trình {BUILDING_TYPE_VI.get(rate.building_type, rate.building_type)}")
    if str(rate.work_scope) != str(inp.work_scope):
        out.append(f"phạm vi {WORK_SCOPE_VI.get(rate.work_scope, rate.work_scope)}")
    if str(rate.quality_tier) != str(inp.quality_tier):
        out.append(f"mức {QUALITY_TIER_VI.get(rate.quality_tier, rate.quality_tier)}")
    return out


def _floor_label(i: int, floors: int, apartment: bool) -> str:
    if apartment:
        return "Sàn căn hộ" if floors == 1 else f"Sàn căn hộ — tầng {i + 1}"
    return "Tầng trệt" if i == 0 else f"Lầu {i}"


def calculate_estimate(inp: EstimateInput, rules: RuleSet, rate: RateData | None,
                       today: date | None = None) -> EstimateResult:
    """Khái toán chi phí từ input khách + bộ hệ số + đơn giá đã chọn (None = không có đơn giá)."""
    b = _Builder(rules)
    assumptions: list[str] = []
    applied: dict[str, Any] = {}

    scope = WorkScope(inp.work_scope)
    apartment = inp.building_type == BuildingType.can_ho
    interior_only = apartment or scope in R.INTERIOR_ONLY_SCOPES
    land = float(inp.land_area_m2)  # validator đảm bảo có giá trị

    assumptions.append(explain.SCOPE_INCLUDES_VI[scope])
    if "quality_tier" not in inp.model_fields_set:
        assumptions.append(
            f"Mức chất lượng: {QUALITY_TIER_VI[inp.quality_tier]} (mặc định, anh/chị chưa chọn)")

    # --- Diện tích xây dựng mỗi tầng ---------------------------------------------------------
    if inp.building_area_m2 is None:
        ground = land
        applied["building_area_m2"] = ground
        if apartment:
            assumptions.append(f"Diện tích sàn căn hộ = {format_m2(land)} theo diện tích anh/chị cung cấp")
        else:
            assumptions.append(
                f"Diện tích xây dựng mỗi tầng = diện tích đất {format_m2(land)} (anh/chị chưa cung cấp)")
    else:
        ground = float(inp.building_area_m2)
        if ground > land and not apartment:
            b.warnings.append(
                f"Diện tích xây dựng mỗi tầng ({format_m2(ground)}) lớn hơn diện tích đất ({format_m2(land)}), "
                "anh/chị kiểm tra lại giúp em")

    if apartment:
        assumptions.append(
            "Căn hộ: không tính móng, mái, hầm, sân thượng và sân (thuộc phần chung của tòa nhà)")
        if inp.floors > 3:
            b.warnings.append(
                "Với căn hộ, số tầng được hiểu là số tầng của chính căn hộ (vd căn duplex = 2), "
                "không phải vị trí tầng trong tòa nhà — anh/chị kiểm tra lại giúp em")
    elif interior_only:
        assumptions.append(
            "Phạm vi Nội thất: chỉ tính diện tích sàn sử dụng (các tầng và tầng lửng); "
            "không tính móng, mái, hầm, sân thượng và sân")

    # --- Móng ---------------------------------------------------------------------------------
    if not interior_only:
        foundation = inp.foundation_type
        if foundation is None:
            foundation = R.default_foundation(inp.floors)
            applied["foundation_type"] = foundation
            cond = (f"≤ {R.STRIP_FOUNDATION_MAX_FLOORS}" if foundation == R.FOUNDATION_STRIP
                    else f"≥ {R.STRIP_FOUNDATION_MAX_FLOORS + 1}")
            assumptions.append(
                f"Móng: {R.FOUNDATION_LABEL_VI[foundation]} (mặc định cho nhà {cond} tầng nổi, "
                "anh/chị chưa cung cấp loại móng)")
        b.add(foundation, ground)

    # --- Các tầng nổi --------------------------------------------------------------------------
    assumptions.append(
        f"Số tầng nổi: {inp.floors} (tính cả tầng trệt), mỗi tầng {format_m2(ground)}" if not apartment
        else f"Số tầng của căn hộ: {inp.floors}, mỗi tầng {format_m2(ground)}")
    for i in range(inp.floors):
        b.add(R.FLOOR, ground, _floor_label(i, inp.floors, apartment))

    # --- Tầng lửng ----------------------------------------------------------------------------
    has_mezzanine = inp.has_mezzanine or inp.mezzanine_area_m2 is not None
    if has_mezzanine:
        applied["has_mezzanine"] = True
        mezz = inp.mezzanine_area_m2
        if mezz is None:
            mezz = ground
            applied["mezzanine_area_m2"] = mezz
            assumptions.append(
                f"Diện tích tầng lửng = diện tích xây dựng {format_m2(ground)} (anh/chị chưa cung cấp)")
        b.add(R.MEZZANINE, float(mezz))

    # --- Tầng hầm -----------------------------------------------------------------------------
    wants_basement = inp.basement or inp.basement_depth_m is not None or inp.basement_area_m2 is not None
    has_basement = wants_basement and not interior_only
    if has_basement:
        applied["basement"] = True
        if not inp.basement:
            assumptions.append("Có tính tầng hầm vì anh/chị đã cung cấp độ sâu hoặc diện tích hầm")
        code = R.basement_rule_for_depth(inp.basement_depth_m)
        if inp.basement_depth_m is None:
            assumptions.append(
                f"Tầng hầm: anh/chị chưa cho độ sâu, em tạm tính theo hạng mục “{b.label(code)}”")
        area = inp.basement_area_m2
        if area is None:
            area = ground
            applied["basement_area_m2"] = area
            assumptions.append(
                f"Diện tích hầm = diện tích xây dựng {format_m2(ground)} (anh/chị chưa cung cấp)")
        b.add(code, float(area))
    elif wants_basement:
        assumptions.append("Không tính tầng hầm vì nằm ngoài phạm vi khái toán này")
    elif not interior_only:
        assumptions.append("Không có tầng hầm (anh/chị chưa đề cập)")

    # --- Sân thượng & mái ----------------------------------------------------------------------
    if not interior_only:
        rooftop_area: float | None = None
        if inp.rooftop_terrace in ("covered", "open"):
            rooftop_area = inp.rooftop_area_m2
            if rooftop_area is None:
                rooftop_area = ground
                applied["rooftop_area_m2"] = rooftop_area
                assumptions.append(
                    f"Diện tích {R.ROOFTOP_LABEL_VI[inp.rooftop_terrace]} = diện tích xây dựng "
                    f"{format_m2(ground)} (anh/chị chưa cung cấp)")
            code = R.ROOFTOP_COVERED if inp.rooftop_terrace == "covered" else R.ROOFTOP_OPEN
            b.add(code, float(rooftop_area))

        roof_type = inp.roof_type
        if roof_type == R.ROOF_NONE:
            assumptions.append("Không tính hạng mục mái theo thông tin anh/chị cung cấp")
        else:
            roof_area = inp.roof_area_m2
            if roof_area is None and rooftop_area is not None:
                remaining = _r2(_d(ground) - _d(rooftop_area))
                if remaining <= 0:
                    assumptions.append(
                        "Sân thượng phủ toàn bộ mặt bằng mái nên em không tính thêm hạng mục mái")
                else:
                    roof_area = remaining
                    applied["roof_area_m2"] = roof_area
                    assumptions.append(
                        f"Diện tích mái = diện tích xây dựng {format_m2(ground)} trừ sân thượng "
                        f"{format_m2(rooftop_area)} = {format_m2(roof_area)}")
            elif roof_area is None:
                roof_area = ground
                applied["roof_area_m2"] = roof_area
                assumptions.append(
                    f"Diện tích mái = diện tích xây dựng {format_m2(ground)} (anh/chị chưa cung cấp)")
            if roof_area is not None:
                if roof_type is None:
                    roof_type = R.ROOF_CONCRETE
                    applied["roof_type"] = roof_type
                    assumptions.append(
                        f"Mái: {R.ROOF_LABEL_VI[roof_type]} (mặc định, anh/chị chưa chọn loại mái)")
                b.add(roof_type, float(roof_area))

        # --- Sân ---------------------------------------------------------------------------
        if inp.yard_area_m2 is not None:
            b.add(R.YARD, float(inp.yard_area_m2))
        elif ground < land:
            assumptions.append("Chưa tính sân trước/sau (anh/chị chưa cung cấp diện tích sân)")

    effective = inp.model_copy(update=applied)
    converted = _r2(b.total)
    warnings = list(b.warnings)

    # --- Trạng thái hệ số --------------------------------------------------------------------
    if b.used_approved and all(b.used_approved):
        rules_status = VerificationStatus.verified
    else:
        rules_status = VerificationStatus.reference
        if b.used_approved:
            warnings.append(RULES_NOT_APPROVED_WARNING)

    # --- Đơn giá & chi phí ---------------------------------------------------------------------
    cost_min: int | None = None
    cost_max: int | None = None
    if rate is None:
        rate_ev = EvidenceValue.unavailable()
        cost_ev = EvidenceValue.unavailable()
        warnings.append(
            f"Chưa có đơn giá tham chiếu cho {BUILDING_TYPE_VI.get(inp.building_type, inp.building_type)} "
            f"tại {_province_label(inp.province)} (phạm vi {WORK_SCOPE_VI[scope]}, "
            f"mức {QUALITY_TIER_VI[inp.quality_tier]}) nên em chưa thể khái toán chi phí")
    else:
        mismatch = _rate_mismatch(inp, rate)
        rate_status = (VerificationStatus.reference if rate.is_fallback or mismatch
                       else VerificationStatus(rate.status))
        if rate.is_fallback:
            assumptions.append(
                f"Dùng đơn giá mặc định toàn quốc do chưa có đơn giá riêng cho {_province_label(inp.province)}")
        if mismatch:
            warnings.append(
                "Đơn giá tham chiếu đang dùng là của " + ", ".join(mismatch)
                + ", không khớp thông tin công trình — con số chỉ để tham khảo")
        rate_ev = make_evidence([rate.price_min, rate.price_max],
                                format_price_per_m2_range(rate.price_min, rate.price_max),
                                source=rate.source, status=rate_status, today=today)
        if rate_ev.is_stale and rate.source and rate.source.updated_at:
            warnings.append(
                f"Đơn giá cập nhật ngày {rate.source.updated_at.strftime('%d/%m/%Y')}, có thể đã cũ")
        if converted > 0:
            conv_d = _d(converted)
            cost_min = round_to_million(conv_d * rate.price_min)
            cost_max = round_to_million(conv_d * rate.price_max)
            cost_ev = make_evidence([cost_min, cost_max], format_vnd_range(cost_min, cost_max),
                                    source=rate.source, status=R.weakest_status(rate_status, rules_status),
                                    today=today)
        else:
            cost_ev = EvidenceValue.unavailable()
            warnings.append("Chưa đủ hệ số khái toán để tính diện tích quy đổi nên em chưa thể khái toán chi phí")

    return EstimateResult(
        input=effective,
        items=b.items,
        converted_area_m2=converted,
        cost_min=cost_min,
        cost_max=cost_max,
        cost=cost_ev,
        rate=rate_ev,
        rate_id=rate.id if rate else None,
        rules_version=rules.version,
        rules_status=rules_status,
        assumptions=assumptions,
        exclusions=explain.build_exclusions(effective),
        cost_drivers=explain.build_cost_drivers(effective, has_basement, has_mezzanine),
        disclaimer=explain.build_disclaimer(rate.source if rate else None),
        warnings=warnings,
    )
