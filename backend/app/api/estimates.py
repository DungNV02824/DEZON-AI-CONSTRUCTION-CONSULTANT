"""REST khái toán (đặc tả mục 10, docs/API_CONTRACT.md "Khái toán"). Gọi engine trực tiếp, không qua LLM."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from app.api.deps import VietnameseRoute, existing_chat_session_id, log_event, not_found, unprocessable
from app.db.session import get_db
from app.evidence import EvidenceValue
from app.schemas.common import BuildingType, QualityTier, WorkScope
from app.schemas.estimate import BudgetAdjustmentResult, EstimateInput, EstimateResult, RateData
from app.services import estimate_service

router = APIRouter(prefix="/api", tags=["estimates"], route_class=VietnameseRoute)

ESTIMATE_NOT_FOUND = "Không tìm thấy phiên khái toán."
# Giới hạn đầu vào REST để kết quả luôn lưu được (converted_area_m2 NUMERIC(10,2), cost NUMERIC(16,0)) thay vì
# lỗi 500 do tràn số; vẫn rộng hơn nhiều so với quy mô nhà ở/khách sạn thực tế.
MAX_DIMENSION_M = 5_000
MAX_AREA_M2 = 200_000
MAX_BUDGET_VND = 10**15  # 1 triệu tỷ
TOO_LARGE = "Quy mô công trình vượt giới hạn tính toán của khái toán tham khảo — vui lòng kiểm tra lại diện tích."


class EstimateCreateIn(EstimateInput):
    """`EstimateInput & {chat_session_id?}` + giới hạn đầu vào của REST (tỉnh không rỗng, diện tích hợp lý)."""
    province: str = Field(min_length=1, max_length=200)
    district: str | None = Field(default=None, max_length=200)
    land_width_m: float | None = Field(default=None, gt=0, le=MAX_DIMENSION_M)
    land_length_m: float | None = Field(default=None, gt=0, le=MAX_DIMENSION_M)
    land_area_m2: float | None = Field(default=None, gt=0, le=MAX_AREA_M2)
    building_area_m2: float | None = Field(default=None, gt=0, le=MAX_AREA_M2)
    mezzanine_area_m2: float | None = Field(default=None, gt=0, le=MAX_AREA_M2)
    basement_depth_m: float | None = Field(default=None, gt=0, le=100)
    basement_area_m2: float | None = Field(default=None, gt=0, le=MAX_AREA_M2)
    roof_area_m2: float | None = Field(default=None, gt=0, le=MAX_AREA_M2)
    rooftop_area_m2: float | None = Field(default=None, gt=0, le=MAX_AREA_M2)
    yard_area_m2: float | None = Field(default=None, gt=0, le=MAX_AREA_M2)
    chat_session_id: UUID | None = None

    @field_validator("province")
    @classmethod
    def _province_not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("cần nhập tỉnh/thành")
        return v


class BudgetAdjustmentIn(BaseModel):
    target_budget_vnd: int = Field(le=MAX_BUDGET_VND)


class RatesOut(BaseModel):
    rate: EvidenceValue
    rate_data: RateData | None = None


@router.post("/estimates", response_model=EstimateResult)
def create_estimate(body: EstimateCreateIn, db: Session = Depends(get_db)) -> EstimateResult:
    inp = EstimateInput.model_validate(body.model_dump(exclude={"chat_session_id"}, exclude_unset=True))
    if (inp.land_area_m2 or 0) > MAX_AREA_M2:  # suy ra từ rộng × dài
        raise unprocessable(TOO_LARGE)
    chat_id = existing_chat_session_id(db, body.chat_session_id)
    try:
        result = estimate_service.create_estimate(db, inp, chat_session_id=chat_id)
    except DataError:  # tràn cột số khi lưu (đơn giá × diện tích quá lớn) -> 422 thay vì 500
        db.rollback()
        raise unprocessable(TOO_LARGE) from None
    log_event(db, "estimate_created", chat_id, source="api",
              estimate_session_id=str(result.estimate_session_id),
              building_type=str(inp.building_type), province=inp.province, work_scope=str(inp.work_scope),
              converted_area_m2=result.converted_area_m2, cost_min=result.cost_min, cost_max=result.cost_max)
    return result


@router.get("/estimates/{estimate_id}", response_model=EstimateResult)
def get_estimate(estimate_id: str, db: Session = Depends(get_db)) -> EstimateResult:
    result = estimate_service.get_estimate(db, estimate_id)
    if result is None:
        raise not_found(ESTIMATE_NOT_FOUND)
    return result


@router.post("/estimates/{estimate_id}/budget-adjustments", response_model=BudgetAdjustmentResult)
def budget_adjustments(estimate_id: str, body: BudgetAdjustmentIn,
                       db: Session = Depends(get_db)) -> BudgetAdjustmentResult:
    if body.target_budget_vnd <= 0:
        raise unprocessable("Ngân sách mục tiêu phải là số dương (VND).")
    try:
        return estimate_service.suggest_budget_adjustments(db, estimate_id, body.target_budget_vnd)
    except estimate_service.EstimateNotFoundError:
        raise not_found(ESTIMATE_NOT_FOUND) from None
    except ValueError as exc:
        raise unprocessable(f"Không thể gợi ý phương án điều chỉnh: {exc}") from None


@router.get("/rates", response_model=RatesOut)
def get_rates(building_type: BuildingType, province: str = Query(min_length=1, max_length=200),
              work_scope: WorkScope = Query(), quality_tier: QualityTier = QualityTier.trung_binh,
              db: Session = Depends(get_db)) -> RatesOut:
    province = " ".join(province.split())
    if not province:
        raise unprocessable("Cần nhập tỉnh/thành.")
    try:
        rate, rate_data = estimate_service.get_reference_rates(db, building_type, province, work_scope,
                                                               quality_tier)
    except ValueError as exc:
        raise unprocessable(f"Tham số không hợp lệ: {exc}") from None
    return RatesOut(rate=rate, rate_data=rate_data)
