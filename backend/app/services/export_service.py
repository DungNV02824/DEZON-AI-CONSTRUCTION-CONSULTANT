"""Xuất PDF khái toán (+ so sánh nhà thầu) — V2. Truy cập DB ở đây; dựng/dàn trang ở app/export."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import EstimateSession
from app.export.estimate_pdf import VN_TZ, build_report, render_pdf, to_vn_time
from app.services import contractor_service, estimate_service

log = logging.getLogger(__name__)


class PdfRenderError(RuntimeError):
    """ReportLab không dàn trang được (vd một dòng bảng cao hơn khung trang) — route trả thông báo tiếng Việt."""


@dataclass
class PdfExport:
    content: bytes
    filename: str
    chat_session_id: uuid.UUID | None
    estimate_session_id: uuid.UUID
    contractor_count: int


def pdf_filename(estimate_session_id: uuid.UUID, generated_at: datetime, with_comparison: bool) -> str:
    """Tên file ASCII, vd `khai-toan-vuathau-20261111-ab12cd34.pdf` (+ `-so-sanh` khi kèm bảng so sánh)."""
    stamp = to_vn_time(generated_at).strftime("%Y%m%d")
    suffix = "-so-sanh" if with_comparison else ""
    return f"khai-toan-vuathau-{stamp}-{estimate_session_id.hex[:8]}{suffix}.pdf"


def estimate_pdf(db: Session, estimate_id: str | uuid.UUID, contractor_ids: list[int] | None = None, *,
                 now: datetime | None = None) -> PdfExport | None:
    """PDF của một phiên khái toán (snapshot đã hiển thị cho khách). `contractor_ids` (2–5) -> thêm bảng so sánh
    tính lại lúc xuất theo tiêu chí của khái toán. None = không có phiên khái toán.

    Ném ValueError (danh sách nhà thầu sai/không tìm thấy) và LookupError (chưa có bộ trọng số) như compare;
    PdfRenderError khi ReportLab không dàn trang được."""
    from reportlab.platypus.doctemplate import LayoutError

    try:
        sid = estimate_id if isinstance(estimate_id, uuid.UUID) else uuid.UUID(str(estimate_id))
    except ValueError:
        return None
    row = db.get(EstimateSession, sid)
    if row is None:
        return None
    estimate = estimate_service.get_estimate(db, sid)
    if estimate is None:
        return None
    comparison = None
    if contractor_ids:
        comparison = contractor_service.compare_contractors(db, contractor_ids, estimate_session_id=sid)
    generated_at = to_vn_time(now or datetime.now(VN_TZ))
    try:
        content = render_pdf(build_report(estimate, comparison, generated_at))
    except LayoutError as exc:
        log.exception("Không dàn trang được PDF khái toán %s (contractor_ids=%s)", sid, contractor_ids)
        raise PdfRenderError(str(exc)) from exc
    return PdfExport(content=content, filename=pdf_filename(sid, generated_at, comparison is not None),
                     chat_session_id=row.chat_session_id, estimate_session_id=sid,
                     contractor_count=len(comparison.contractors) if comparison else 0)
