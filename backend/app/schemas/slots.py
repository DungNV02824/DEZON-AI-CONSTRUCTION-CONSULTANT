"""Slot thu thập trong hội thoại (đặc tả mục 5.2)."""
from pydantic import BaseModel

from app.schemas.common import BuildingType, QualityTier, WorkScope
from app.schemas.estimate import EstimateInput, FoundationType, RooftopType, RoofType


class Slots(BaseModel):
    building_type: BuildingType | None = None
    province: str | None = None
    district: str | None = None
    land_width_m: float | None = None
    land_length_m: float | None = None
    land_area_m2: float | None = None
    building_area_m2: float | None = None
    floors: int | None = None
    has_mezzanine: bool | None = None
    basement: bool | None = None
    basement_depth_m: float | None = None
    roof_type: RoofType | None = None
    rooftop_terrace: RooftopType | None = None
    foundation_type: FoundationType | None = None
    work_scope: WorkScope | None = None
    quality_tier: QualityTier | None = None
    budget_vnd: int | None = None
    start_time: str | None = None
    confirmed: bool = False  # khách đã xác nhận bản tóm tắt trước khi khái toán
    # Các slot đã hỏi khách (kể cả khi khách chưa trả lời) — tránh hỏi lại slot tùy chọn như budget_vnd.
    asked: list[str] = []


# Slot bắt buộc để khái toán (land_area_m2 được suy ra từ rộng × dài khi chuẩn hóa).
REQUIRED_SLOTS: list[str] = ["building_type", "province", "land_area_m2", "floors", "work_scope"]

# Thứ tự hỏi mặc định (mục 5.2). Bỏ qua slot đã có.
ASK_ORDER: list[str] = [
    "building_type", "province", "land_area_m2", "floors", "work_scope", "quality_tier", "budget_vnd",
    "start_time",
]


def missing_required(slots: Slots) -> list[str]:
    return [k for k in REQUIRED_SLOTS if getattr(slots, k) is None]


def to_estimate_input(slots: Slots) -> EstimateInput:
    """Chuyển slot đã đủ sang EstimateInput. Gọi khi missing_required(slots) == []."""
    data = slots.model_dump(exclude={"budget_vnd", "start_time", "confirmed"}, exclude_none=True)
    data["has_mezzanine"] = bool(slots.has_mezzanine)
    data["basement"] = bool(slots.basement)
    return EstimateInput(**data)
