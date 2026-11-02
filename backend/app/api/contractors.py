"""REST nhà thầu (đặc tả mục 7, 8, 10; docs/API_CONTRACT.md "Nhà thầu")."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import (
    MAX_BIGINT,
    VietnameseRoute,
    existing_chat_session_id,
    log_event,
    not_found,
    unprocessable,
)
from app.db.models import RecommendationLog
from app.db.session import get_db
from app.schemas.common import BuildingType
from app.schemas.matching import (
    ComparisonMatrix,
    ContractorCard,
    ContractorProfile,
    MatchCriteria,
    MatchResult,
)
from app.services import contractor_service

router = APIRouter(prefix="/api", tags=["contractors"], route_class=VietnameseRoute)

NO_WEIGHTS = "Chưa có bộ trọng số matching đang hoạt động — quản trị viên cần kích hoạt một phiên bản trọng số."


class ContractorListOut(BaseModel):
    items: list[ContractorCard]
    total: int


class MatchIn(MatchCriteria):
    """`MatchCriteria & {chat_session_id?}` + giới hạn đầu vào của REST (không nhận tỉnh rỗng, số âm/quá lớn)."""
    province: str = Field(min_length=1, max_length=200)
    district: str | None = Field(default=None, max_length=200)
    budget_vnd: int | None = Field(default=None, gt=0, le=10**15)
    floors: int | None = Field(default=None, ge=1, le=50)
    converted_area_m2: float | None = Field(default=None, gt=0, le=10**8)
    chat_session_id: UUID | None = None

    @field_validator("province")
    @classmethod
    def _province_not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("cần nhập tỉnh/thành")
        return v


class CompareIn(BaseModel):
    contractor_ids: list[int] = Field(min_length=2, max_length=5)
    estimate_session_id: UUID | None = None
    # Tùy chọn: ngân sách khách nêu (VND). Không gửi -> lấy từ slot phiên chat gắn với khái toán.
    budget_vnd: int | None = Field(default=None, ge=1, le=MAX_BIGINT)


class ClickIn(BaseModel):
    contractor_id: int = Field(ge=1, le=MAX_BIGINT)


class OkOut(BaseModel):
    ok: bool = True


@router.get("/contractors", response_model=ContractorListOut)
def list_contractors(province: str | None = Query(default=None, max_length=200),
                     building_type: BuildingType | None = None,
                     q: str | None = Query(default=None, max_length=200),
                     limit: int = Query(default=20, ge=1, le=100),
                     db: Session = Depends(get_db)) -> ContractorListOut:
    try:
        items, total = contractor_service.search_contractors_simple(
            db, province=province or None, building_type=building_type.value if building_type else None,
            q=q, limit=limit)
    except ValueError as exc:
        raise unprocessable(str(exc)) from None
    return ContractorListOut(items=items, total=total)


@router.get("/contractors/{slug}", response_model=ContractorProfile)
def get_contractor(slug: str, db: Session = Depends(get_db)) -> ContractorProfile:
    if slug.isdigit() and int(slug) > MAX_BIGINT:  # tránh lỗi tràn BIGINT khi tra theo id
        raise not_found("Không tìm thấy nhà thầu.")
    profile = contractor_service.get_contractor_profile(db, slug)
    if profile is None:
        raise not_found("Không tìm thấy nhà thầu.")
    return profile


@router.post("/contractors/match", response_model=MatchResult)
def match(body: MatchIn, db: Session = Depends(get_db)) -> MatchResult:
    criteria = MatchCriteria.model_validate(body.model_dump(exclude={"chat_session_id"}))
    try:
        return contractor_service.match_contractors(db, criteria, chat_session_id=body.chat_session_id)
    except LookupError:
        raise HTTPException(status_code=503, detail=NO_WEIGHTS) from None


@router.post("/contractors/compare", response_model=ComparisonMatrix)
def compare(body: CompareIn, db: Session = Depends(get_db)) -> ComparisonMatrix:
    if any(i < 1 or i > MAX_BIGINT for i in body.contractor_ids):
        raise unprocessable("Mã nhà thầu không hợp lệ.")
    try:
        return contractor_service.compare_contractors(db, body.contractor_ids,
                                                      estimate_session_id=body.estimate_session_id,
                                                      budget_vnd=body.budget_vnd)
    except LookupError:
        raise HTTPException(status_code=503, detail=NO_WEIGHTS) from None
    except ValueError as exc:
        raise unprocessable(str(exc)) from None


@router.post("/recommendations/{log_id}/click", response_model=OkOut)
def record_click(body: ClickIn, log_id: int = Path(ge=1, le=MAX_BIGINT),
                 db: Session = Depends(get_db)) -> OkOut:
    if not contractor_service.record_click(db, log_id, body.contractor_id):
        raise not_found("Không tìm thấy lượt đề xuất hoặc nhà thầu không nằm trong danh sách đề xuất.")
    rec = db.get(RecommendationLog, log_id)
    rank = next((r.get("rank") for r in (rec.results or []) if r.get("contractor_id") == body.contractor_id),
                None) if rec else None
    log_event(db, "contractor_clicked", existing_chat_session_id(db, rec.chat_session_id if rec else None),
              recommendation_log_id=log_id, contractor_id=body.contractor_id, rank=rank,
              weights_version=rec.weights_version if rec else None)
    return OkOut(ok=True)
