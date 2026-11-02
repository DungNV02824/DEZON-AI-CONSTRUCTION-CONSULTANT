"""Hợp đồng dữ liệu của Calculation Engine (đặc tả mục 6)."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.evidence import EvidenceValue, SourceRef, VerificationStatus
from app.schemas.common import BuildingType, QualityTier, WorkScope

FoundationType = Literal["foundation_single", "foundation_strip", "foundation_pile"]
RoofType = Literal["roof_metal", "roof_concrete", "roof_tile_on_truss", "roof_tile_on_concrete", "none"]
RooftopType = Literal["none", "covered", "open"]


class EstimateInput(BaseModel):
    building_type: BuildingType
    province: str
    district: str | None = None
    land_width_m: float | None = Field(default=None, gt=0)
    land_length_m: float | None = Field(default=None, gt=0)
    land_area_m2: float | None = Field(default=None, gt=0)  # bắt buộc, hoặc suy ra từ rộng × dài
    building_area_m2: float | None = Field(default=None, gt=0)  # diện tích xây dựng/tầng; None -> = đất
    floors: int = Field(ge=1, le=50)  # số tầng nổi, tính cả trệt
    has_mezzanine: bool = False
    mezzanine_area_m2: float | None = Field(default=None, gt=0)
    basement: bool = False
    basement_depth_m: float | None = Field(default=None, gt=0)
    basement_area_m2: float | None = Field(default=None, gt=0)
    roof_type: RoofType | None = None  # None -> mặc định roof_concrete
    roof_area_m2: float | None = Field(default=None, gt=0)
    rooftop_terrace: RooftopType | None = None  # None -> không có sân thượng
    rooftop_area_m2: float | None = Field(default=None, gt=0)
    foundation_type: FoundationType | None = None  # None -> strip (≤3 tầng) / pile (≥4 tầng)
    yard_area_m2: float | None = Field(default=None, gt=0)
    work_scope: WorkScope
    quality_tier: QualityTier = QualityTier.trung_binh

    @model_validator(mode="after")
    def _derive_land_area(self) -> "EstimateInput":
        if self.land_area_m2 is None:
            if self.land_width_m and self.land_length_m:
                self.land_area_m2 = round(self.land_width_m * self.land_length_m, 2)
            else:
                raise ValueError("Cần land_area_m2 hoặc land_width_m + land_length_m")
        return self


class RuleData(BaseModel):
    """Một dòng estimate_rules (đã nạp từ DB)."""
    code: str
    group_name: str
    label_vi: str
    coefficient: float
    base: str
    approved: bool
    version: int
    source: SourceRef | None = None


class RuleSet(BaseModel):
    version: int
    rules: dict[str, RuleData]  # code -> rule

    @property
    def all_approved(self) -> bool:
        return all(r.approved for r in self.rules.values())

    def __getitem__(self, code: str) -> RuleData:
        return self.rules[code]


class RateData(BaseModel):
    """Đơn giá tham chiếu đã chọn từ construction_rates (VND/m²)."""
    id: int | None = None
    building_type: BuildingType
    province: str  # '*' = toàn quốc
    work_scope: WorkScope
    quality_tier: QualityTier
    price_min: int
    price_max: int
    effective_from: date
    is_fallback: bool = False  # True khi phải dùng province='*'
    status: VerificationStatus = VerificationStatus.reference
    source: SourceRef | None = None


class EstimateItem(BaseModel):
    rule_code: str
    label_vi: str
    group_name: str
    area_m2: float  # A_i
    coefficient: float  # k_i
    converted_m2: float  # A_i × k_i


class EstimateResult(BaseModel):
    estimate_session_id: UUID | None = None
    input: EstimateInput  # đã áp các giá trị mặc định
    items: list[EstimateItem]
    converted_area_m2: float
    cost_min: int | None  # VND, làm tròn triệu; None khi không có đơn giá
    cost_max: int | None
    cost: EvidenceValue  # value=[cost_min,cost_max], display "2,058–2,352 tỷ"
    rate: EvidenceValue  # value=[price_min,price_max], display "4,2–4,8 triệu/m²"
    rate_id: int | None = None
    rules_version: int
    rules_status: VerificationStatus  # reference nếu có rule chưa approved
    assumptions: list[str]
    exclusions: list[str]
    cost_drivers: list[str]
    disclaimer: str
    warnings: list[str] = []


class BudgetOption(BaseModel):
    code: str  # reduce_floor | lower_quality | remove_basement | change_roof | phase_rough_first | ...
    label_vi: str
    description: str
    changes: dict  # các trường EstimateInput bị thay đổi, vd {"floors": 3}
    converted_area_m2: float
    cost_min: int | None
    cost_max: int | None
    cost: EvidenceValue
    saving_min: int | None = None  # so với khái toán hiện tại (VND, dương = tiết kiệm)
    fits_budget: bool  # cost_min <= target_budget


class BudgetAdjustmentResult(BaseModel):
    estimate_session_id: UUID | None = None
    target_budget_vnd: int
    target_budget: EvidenceValue  # display "1,5 tỷ" (status=reference, nguồn: khách cung cấp)
    current_cost: EvidenceValue
    options: list[BudgetOption]
