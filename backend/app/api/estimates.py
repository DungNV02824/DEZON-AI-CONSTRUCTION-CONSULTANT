"""REST khái toán (đặc tả mục 10, docs/API_CONTRACT.md "Khái toán"). Gọi engine trực tiếp, không qua LLM."""
from __future__ import annotations

import html
import json
import logging
from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from app.api.contractors import NO_WEIGHTS
from app.api.deps import (
    MAX_BIGINT,
    VietnameseRoute,
    client_ip,
    existing_chat_session_id,
    log_event,
    not_found,
    unprocessable,
)
from app.db.session import get_db
from app.evidence import EvidenceValue
from app.export.estimate_pdf import BRAND_NAME, SITE_URL
from app.schemas.common import BuildingType, QualityTier, WorkScope
from app.schemas.estimate import BudgetAdjustmentResult, EstimateInput, EstimateResult, RateData
from app.services import chat_service, estimate_service, export_service
from app.services.chat_service import RateLimiter

router = APIRouter(prefix="/api", tags=["estimates"], route_class=VietnameseRoute)
log = logging.getLogger(__name__)

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


PDF_RATE_LIMIT_MESSAGE = "Anh/chị đã tải PDF quá nhiều lần, vui lòng thử lại sau ít phút."
PDF_BAD_IDS = "Cần 0 hoặc từ 2 đến 5 mã nhà thầu hợp lệ để xuất bảng so sánh."
PDF_RENDER_FAILED = ("Em chưa tạo được file PDF cho bảng khái toán này. Anh/chị vui lòng thử lại với ít nhà thầu hơn "
                     "hoặc liên hệ nhân viên để được hỗ trợ.")
PDF_UNEXPECTED = "Em chưa tạo được file PDF lúc này, anh/chị vui lòng thử lại sau ít phút."


def _wants_html(request: Request) -> bool:
    """Trình duyệt mở link (thẻ <a> "Tải PDF") gửi `Accept: text/html,…`; client API (fetch/httpx) thì không."""
    return "text/html" in request.headers.get("accept", "").lower()


def pdf_error_page(status_code: int, message: str, retry_url: str | None = None,
                   headers: dict[str, str] | None = None) -> HTMLResponse:
    """Trang lỗi HTML tiếng Việt cho link tải PDF mở trong tab mới (thay cho JSON `{detail}` khách không đọc được)."""
    retry = (f'<a class="btn" href="{html.escape(retry_url, quote=True)}">Thử lại</a>'
             if retry_url and status_code in (429, 500, 503) else "")
    body = f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Chưa tải được PDF — {html.escape(BRAND_NAME)}</title>
<style>body{{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#f4f6f9;color:#1f2933}}
main{{max-width:520px;margin:48px auto;padding:24px 20px;background:#fff;border-radius:12px;
box-shadow:0 1px 4px rgba(0,28,51,.12)}}h1{{font-size:20px;color:#001c33;margin:0 0 12px}}
p{{line-height:1.55;margin:0 0 12px}}.muted{{color:#5b6573;font-size:14px}}.actions{{margin-top:16px}}
.btn{{display:inline-block;padding:9px 16px;border-radius:8px;background:#001c33;color:#fff;text-decoration:none;
margin:0 8px 8px 0}}.btn.alt{{background:#fff;color:#001c33;border:1px solid #d9dee5}}</style></head>
<body><main><h1>Chưa tải được file PDF</h1><p>{html.escape(message)}</p>
<p class="muted">Anh/chị có thể đóng tab này để quay lại cuộc trò chuyện.</p>
<div class="actions">{retry}<a class="btn alt" href="{html.escape(SITE_URL, quote=True)}">Về {html.escape(BRAND_NAME)}</a>
</div></main></body></html>"""
    return HTMLResponse(body, status_code=status_code, headers={
        **(headers or {}),
        "Content-Disposition": "inline",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
    })


class PdfDownloadRoute(VietnameseRoute):
    """Route của link tải PDF: khi trình duyệt điều hướng tới (Accept có text/html) thì mọi lỗi — kể cả lỗi validation
    và lỗi không lường trước — thành trang HTML tiếng Việt; client API vẫn nhận JSON `{detail}` như cũ."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            if not _wants_html(request):
                return await original(request)
            retry_url = str(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
            try:
                response = await original(request)
            except HTTPException as exc:
                return pdf_error_page(exc.status_code, str(exc.detail), retry_url, exc.headers)
            except Exception:  # noqa: BLE001 — khách thấy trang tiếng Việt thay vì "Internal Server Error"
                log.exception("Lỗi xuất PDF khái toán %s", request.url.path)
                return pdf_error_page(500, PDF_UNEXPECTED, retry_url)
            if response.status_code >= 400:  # lỗi validation 422 do VietnameseRoute trả JSON
                try:
                    detail = json.loads(bytes(response.body)).get("detail")
                except (ValueError, AttributeError):
                    detail = None
                return pdf_error_page(response.status_code, detail if isinstance(detail, str) else PDF_UNEXPECTED,
                                      retry_url)
            return response

        return handler


def estimate_pdf(estimate_id: str, request: Request,
                 contractor_ids: list[int] | None = Query(default=None),
                 db: Session = Depends(get_db),
                 limiter: RateLimiter = Depends(chat_service.get_pdf_rate_limiter)) -> Response:
    """PDF khái toán (+ bảng so sánh 2–5 nhà thầu tính lại lúc xuất) — V2. Ẩn danh, không có thông tin khách."""
    ids = contractor_ids or []
    if len(ids) == 1 or len(ids) > 5 or any(i < 1 or i > MAX_BIGINT for i in ids):
        raise unprocessable(PDF_BAD_IDS)
    allowed, retry_after = limiter.hit(client_ip(request))
    if not allowed:
        raise HTTPException(status_code=429, detail=PDF_RATE_LIMIT_MESSAGE, headers={"Retry-After": str(retry_after)})
    try:
        export = export_service.estimate_pdf(db, estimate_id, ids)
    except LookupError:
        raise HTTPException(status_code=503, detail=NO_WEIGHTS) from None
    except ValueError as exc:
        raise unprocessable(str(exc)) from None
    except export_service.PdfRenderError:
        raise HTTPException(status_code=500, detail=PDF_RENDER_FAILED) from None
    if export is None:
        raise not_found(ESTIMATE_NOT_FOUND)
    log_event(db, "estimate_pdf_exported", existing_chat_session_id(db, export.chat_session_id),
              estimate_session_id=str(export.estimate_session_id), contractor_count=export.contractor_count)
    return Response(content=export.content, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{export.filename}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


router.add_api_route(
    "/estimates/{estimate_id}/pdf", estimate_pdf, methods=["GET"], response_class=Response,
    route_class_override=PdfDownloadRoute,
    responses={200: {"content": {"application/pdf": {}}, "description": "File PDF khái toán"},
               429: {"description": PDF_RATE_LIMIT_MESSAGE}, 500: {"description": PDF_RENDER_FAILED}},
)


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
