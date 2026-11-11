"""Admin tối thiểu (đặc tả mục 10, 11.2 bước 3–6, 12, 14 bước 11).

- `GET /admin`: trang HTML tĩnh tự chứa (app/static/admin.html), không cần token để tải trang; trang hỏi token
  rồi gửi qua header `X-Admin-Token` cho mọi lệnh gọi `/api/admin/*`.
- `/api/admin/*`: bắt buộc `X-Admin-Token` (deps.require_admin).
"""
from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path as FsPath
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import MAX_BIGINT, VietnameseRoute, require_admin
from app.db.session import get_db
from app.evidence import SourceType, VerificationStatus
from app.schemas.common import BuildingType, QualityTier, WorkScope
from app.schemas.quote_analysis import QuoteAnalysisResult
from app.services import admin_service as svc
from app.services import chat_service as chat_svc
from app.services import contact_service as contact_svc
from app.services import quote_analysis_service as quote_svc
from app.services.metrics_service import MetricsReport, compute_metrics

STATIC_DIR = FsPath(__file__).resolve().parent.parent / "static"
ADMIN_PAGE = STATIC_DIR / "admin.html"

api = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)],
                route_class=VietnameseRoute)
page = APIRouter(tags=["admin"])


def _call[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return fn(*args, **kwargs)
    except svc.AdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


class OkOut(BaseModel):
    ok: bool = True


class ListOut(BaseModel):
    items: list[Any]
    total: int


class VerifyIn(BaseModel):
    verified_by: str | None = Field(default=None, max_length=200)


IdPath = Annotated[int, Path(ge=1, le=MAX_BIGINT)]
VersionPath = Annotated[int, Path(ge=1, le=2**31 - 1)]


# ------------------------------------------------------------------ trang quản trị
@page.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(ADMIN_PAGE, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


# ------------------------------------------------------------------ đơn giá
@api.get("/rates")
def list_rates(building_type: BuildingType | None = None, province: str | None = Query(None, max_length=200),
               work_scope: WorkScope | None = None, quality_tier: QualityTier | None = None,
               limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db)) -> dict:
    items, total = svc.list_rates(db, building_type, province, work_scope, quality_tier, limit, offset)
    return {"items": items, "total": total}


@api.get("/rates/{rate_id}", response_model=svc.RateOut)
def get_rate(rate_id: IdPath, db: Session = Depends(get_db)) -> svc.RateOut:
    return _call(svc.get_rate, db, rate_id)


@api.post("/rates", response_model=svc.RateOut, status_code=201)
def create_rate(body: svc.RateIn, db: Session = Depends(get_db)) -> svc.RateOut:
    return _call(svc.create_rate, db, body)


@api.put("/rates/{rate_id}", response_model=svc.RateOut)
def update_rate(rate_id: IdPath, body: svc.RateUpdate, db: Session = Depends(get_db)) -> svc.RateOut:
    return _call(svc.update_rate, db, rate_id, body)


@api.delete("/rates/{rate_id}", response_model=OkOut)
def delete_rate(rate_id: IdPath, db: Session = Depends(get_db)) -> OkOut:
    _call(svc.delete_rate, db, rate_id)
    return OkOut()


@api.post("/rates/{rate_id}/approve")
def approve_rate(rate_id: IdPath, body: VerifyIn | None = None, db: Session = Depends(get_db)) -> dict:
    return _call(svc.approve_rate, db, rate_id, (body.verified_by if body else None) or "admin")


# ------------------------------------------------------------------ hệ số
@api.get("/rules")
def list_rules(code: str | None = Query(None, max_length=100), approved: bool | None = None,
               latest_only: bool = False, db: Session = Depends(get_db)) -> dict:
    items = svc.list_rules(db, code, approved, latest_only)
    return {"items": items, "total": len(items)}


@api.post("/rules", response_model=svc.RuleOut, status_code=201)
def create_rule(body: svc.RuleIn, db: Session = Depends(get_db)) -> svc.RuleOut:
    return _call(svc.create_rule_version, db, body)


@api.put("/rules/{rule_id}", response_model=svc.RuleOut)
def update_rule(rule_id: IdPath, body: svc.RuleUpdate, db: Session = Depends(get_db)) -> svc.RuleOut:
    return _call(svc.update_rule, db, rule_id, body)


@api.post("/rules/{rule_id}/approve", response_model=svc.RuleOut)
def approve_rule(rule_id: IdPath, db: Session = Depends(get_db)) -> svc.RuleOut:
    return _call(svc.update_rule, db, rule_id, svc.RuleUpdate(approved=True))


@api.delete("/rules/{rule_id}", response_model=OkOut)
def delete_rule(rule_id: IdPath, db: Session = Depends(get_db)) -> OkOut:
    _call(svc.delete_rule, db, rule_id)
    return OkOut()


# ------------------------------------------------------------------ trọng số matching
@api.get("/weights")
def list_weights(db: Session = Depends(get_db)) -> dict:
    items = svc.list_weights(db)
    return {"items": items, "total": len(items)}


@api.post("/weights", response_model=svc.WeightsOut, status_code=201)
def create_weights(body: svc.WeightsIn, db: Session = Depends(get_db)) -> svc.WeightsOut:
    return _call(svc.create_weights, db, body)


@api.put("/weights/{version}", response_model=svc.WeightsOut)
def update_weights(version: VersionPath, body: svc.WeightsUpdate,
                   db: Session = Depends(get_db)) -> svc.WeightsOut:
    return _call(svc.update_weights, db, version, body)


@api.post("/weights/{version}/activate", response_model=svc.WeightsOut)
def activate_weights(version: VersionPath, db: Session = Depends(get_db)) -> svc.WeightsOut:
    return _call(svc.update_weights, db, version, svc.WeightsUpdate(is_active=True))


@api.delete("/weights/{version}", response_model=OkOut)
def delete_weights(version: VersionPath, db: Session = Depends(get_db)) -> OkOut:
    _call(svc.delete_weights, db, version)
    return OkOut()


# ------------------------------------------------------------------ nguồn dữ liệu
@api.get("/sources")
def list_sources(status: VerificationStatus | None = None, source_type: SourceType | None = None,
                 limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db)) -> dict:
    items, total = svc.list_sources(db, status, source_type, limit, offset)
    return {"items": items, "total": total}


@api.post("/sources/{source_id}/verify", response_model=svc.SourceOut)
def verify_source(source_id: IdPath, body: VerifyIn | None = None,
                  db: Session = Depends(get_db)) -> svc.SourceOut:
    return _call(svc.verify_source, db, source_id, (body.verified_by if body else None) or "admin")


# ------------------------------------------------------------------ guardrail & chỉ số
@api.get("/guardrail-violations")
def list_violations(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                    rule: str | None = Query(None, max_length=100), db: Session = Depends(get_db)) -> dict:
    items, total = svc.list_violations(db, limit, offset, rule)
    return {"items": items, "total": total}


@api.get("/metrics", response_model=MetricsReport)
def metrics(db: Session = Depends(get_db)) -> MetricsReport:
    return compute_metrics(db)


# ------------------------------------------------------------------ yêu cầu liên hệ nhân viên
@api.get("/contact-requests", response_model=contact_svc.ContactRequestList)
def list_contact_requests(status: contact_svc.ContactStatus | None = None,
                          limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                          db: Session = Depends(get_db)) -> contact_svc.ContactRequestList:
    return contact_svc.list_contact_requests(db, status, limit, offset)


@api.patch("/contact-requests/{request_id}", response_model=contact_svc.ContactRequestOut)
def update_contact_request(request_id: UUID, body: contact_svc.ContactRequestUpdate,
                           db: Session = Depends(get_db)) -> contact_svc.ContactRequestOut:
    out = contact_svc.update_contact_request(db, request_id, body)
    if out is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu liên hệ.")
    return out


# ------------------------------------------------------------------ yêu cầu báo giá & báo giá nhà thầu (V2 — quotes)
@api.get("/quote-requests", response_model=quote_svc.QuoteRequestList)
def list_quote_requests(status: quote_svc.QuoteRequestStatus | None = None,
                        code: str | None = Query(None, max_length=20),
                        limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                        db: Session = Depends(get_db)) -> quote_svc.QuoteRequestList:
    return quote_svc.list_quote_requests(db, status, code, limit, offset)


@api.get("/quote-requests/{quote_request_id}", response_model=quote_svc.QuoteRequestDetail)
def get_quote_request(quote_request_id: UUID, db: Session = Depends(get_db)) -> quote_svc.QuoteRequestDetail:
    return _call(quote_svc.get_quote_request, db, quote_request_id)


@api.post("/quote-requests/{quote_request_id}/quotes", response_model=quote_svc.ContractorQuoteOut, status_code=201)
def create_contractor_quote(quote_request_id: UUID, body: quote_svc.ContractorQuoteIn,
                            db: Session = Depends(get_db)) -> quote_svc.ContractorQuoteOut:
    return _call(quote_svc.create_contractor_quote, db, quote_request_id, body)


@api.post("/quote-requests/{quote_request_id}/analysis-preview", response_model=QuoteAnalysisResult)
def preview_quote_analysis(quote_request_id: UUID, body: quote_svc.ContractorQuoteIn,
                           db: Session = Depends(get_db)) -> QuoteAnalysisResult:
    """Phân tích từ dữ liệu form "Nhập báo giá" mà không lưu."""
    return _call(quote_svc.preview_analysis, db, quote_request_id, body)


@api.put("/contractor-quotes/{quote_id}", response_model=quote_svc.ContractorQuoteOut)
def update_contractor_quote(quote_id: IdPath, body: quote_svc.ContractorQuoteIn,
                            db: Session = Depends(get_db)) -> quote_svc.ContractorQuoteOut:
    return _call(quote_svc.update_contractor_quote, db, quote_id, body)


@api.delete("/contractor-quotes/{quote_id}", response_model=OkOut)
def delete_contractor_quote(quote_id: IdPath, db: Session = Depends(get_db)) -> OkOut:
    _call(quote_svc.delete_contractor_quote, db, quote_id)
    return OkOut()


@api.get("/contractor-quotes/{quote_id}/analysis", response_model=QuoteAnalysisResult)
def contractor_quote_analysis(quote_id: IdPath, db: Session = Depends(get_db)) -> QuoteAnalysisResult:
    return _call(quote_svc.analyze_contractor_quote, db, quote_id)


# ------------------------------------------------------------------ hội thoại của khách (nút "Xem hội thoại")
@api.get("/chat-sessions/{session_id}", response_model=chat_svc.SessionStateOut)
def get_chat_session(session_id: UUID, db: Session = Depends(get_db)) -> chat_svc.SessionStateOut:
    """Toàn bộ tin khách/trợ lý của một phiên (bỏ tin tool nội bộ) — cùng dạng GET /api/chat/sessions/{id}."""
    view = chat_svc.get_session_view(db, session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên chat.")
    return view


# ------------------------------------------------------------------ đồng bộ & nhập file
class SyncSource(StrEnum):
    csv = "csv"
    api = "api"
    crawl = "crawl"
    reindex = "reindex"
    after_sync = "after_sync"


class ImportKind(StrEnum):
    rates = "rates"
    rules = "rules"


_TRUE = {"1", "true", "yes", "on", "co", "có"}


def _flag(params: dict[str, Any], key: str) -> bool:
    v = params.get(key)
    return v is True or str(v).strip().lower() in _TRUE


async def _read_request(request: Request) -> tuple[bytes | None, str | None, dict[str, Any]]:
    """Đọc file tải lên (multipart field `file`, hoặc thân request thô kèm ?filename=) và tham số
    (query + form/JSON). Dùng request.form() tại thời điểm gọi để app vẫn chạy khi thiếu python-multipart."""
    params: dict[str, Any] = dict(request.query_params)
    ctype = (request.headers.get("content-type") or "").lower()
    content: bytes | None = None
    filename: str | None = params.get("filename")
    limit = svc.MAX_UPLOAD_BYTES + 1
    if ctype.startswith("multipart/form-data"):
        try:
            form = await request.form()
        except Exception:  # noqa: BLE001 — form lỗi/thiếu thư viện: báo lỗi rõ thay vì 500
            raise HTTPException(status_code=422, detail="Không đọc được dữ liệu form tải lên.") from None
        for key, value in form.multi_items():
            if key == "file" and hasattr(value, "read"):
                content = await value.read(limit)
                filename = value.filename or filename
            elif isinstance(value, str):
                params.setdefault(key, value)
    elif ctype.startswith("application/json"):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=422, detail="JSON không hợp lệ.") from None
        if isinstance(body, dict):
            for key, value in body.items():
                params.setdefault(key, value)
    else:
        raw = await request.body()
        if raw:
            content = raw[:limit]
    return content, filename, params


def _import(db: Session, content: bytes | None, filename: str | None, params: dict[str, Any],
            kind: str) -> dict:
    dry_run, force = _flag(params, "dry_run"), _flag(params, "force")
    if content is not None:
        return _call(svc.import_upload, db, content, filename or "", kind, dry_run=dry_run, force=force)
    path = params.get("path")
    if path:
        return _call(svc.import_path, db, str(path), kind, dry_run=dry_run, force=force)
    raise HTTPException(status_code=422, detail="Cần tải lên file (trường 'file') hoặc truyền 'path' trên máy chủ.")


@api.post("/import/{kind}")
async def import_file(kind: ImportKind, request: Request, db: Session = Depends(get_db)) -> dict:
    """Nhập đơn giá (`rates`) / hệ số (`rules`) từ CSV/XLSX. Tham số `dry_run`, `force` (query hoặc form)."""
    content, filename, params = await _read_request(request)
    return await run_in_threadpool(_import, db, content, filename, params, kind.value)


@api.post("/sync/{source}")
async def sync(source: SyncSource, request: Request, db: Session = Depends(get_db)) -> dict:
    """csv: nhập trực tiếp (file tải lên hoặc `path`, kèm `kind`=rates|rules) và trả báo cáo.
    api | crawl | reindex | after_sync: xếp job RQ; Redis không sẵn sàng -> 503."""
    content, filename, params = await _read_request(request)
    if source == SyncSource.csv:
        kind = str(params.get("kind") or "").strip()
        if kind not in svc.IMPORT_KINDS:
            raise HTTPException(status_code=422, detail="Cần tham số kind = 'rates' (đơn giá) hoặc 'rules' (hệ số).")
        report = await run_in_threadpool(_import, db, content, filename, params, kind)
        return {"status": "done", "source": "csv", "report": report}
    kwargs: dict[str, Any] = {}
    if source in (SyncSource.api, SyncSource.crawl) and params.get("max_pages") not in (None, ""):
        try:
            max_pages = int(params["max_pages"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="max_pages phải là số nguyên.") from None
        if not 1 <= max_pages <= 1000:
            raise HTTPException(status_code=422, detail="max_pages phải trong khoảng 1..1000.")
        kwargs["max_pages"] = max_pages
    return await run_in_threadpool(_call, svc.enqueue_sync, source.value, **kwargs)



@api.get("/sync/jobs/{job_id}")
def sync_job_status(job_id: str) -> dict:
    """Trạng thái + kết quả job đồng bộ đã xếp (queued|started|finished|failed..., result/error). Kết quả giữ 7 ngày."""
    return _call(svc.get_sync_job, job_id)


router = APIRouter()
router.include_router(page)
router.include_router(api)
