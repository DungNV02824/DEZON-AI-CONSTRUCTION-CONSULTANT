"""Yêu cầu báo giá (đặc tả mục 8.3, 9.2 create_quote_request, 10 POST /api/quote-requests).

Dùng chung cho REST API và tool của orchestrator.
"""
import re
import uuid
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.state import split_raw
from app.config import get_settings
from app.db.models import AnalyticsEvent, ChatSession, Contractor, EstimateSession, QuoteRequest
from app.formatting import format_m2, format_vnd_range
from app.schemas.slots import Slots, project_preferences

_PHONE_RE = re.compile(r"^0\d{9,10}$")


def normalize_phone(raw: str) -> str:
    """'+84 912.345.678' -> '0912345678'. Không hợp lệ -> ValueError."""
    digits = re.sub(r"[\s.\-()]", "", raw or "")
    if digits.startswith("+84"):
        digits = "0" + digits[3:]
    elif digits.startswith("84") and len(digits) in (11, 12):
        digits = "0" + digits[2:]
    if not _PHONE_RE.match(digits):
        raise ValueError("Số điện thoại không hợp lệ (cần dạng 0xxxxxxxxx)")
    return digits


class QuoteRequestIn(BaseModel):
    contractor_ids: list[int] = Field(min_length=1, max_length=5)
    estimate_session_id: UUID | None = None
    customer_name: str = Field(min_length=1, max_length=200)
    customer_phone: str
    customer_email: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    # Phiên chat của khách (tùy chọn): mong muốn đã ghi nhận (phong cách, mức đầu tư) được gửi kèm ghi chú. Bỏ trống ->
    # lấy theo phiên chat của estimate_session_id (nếu có).
    chat_session_id: UUID | None = None

    @field_validator("customer_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)

    @field_validator("customer_email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Email không hợp lệ")
        return v

    @field_validator("contractor_ids")
    @classmethod
    def _unique(cls, v: list[int]) -> list[int]:
        return list(dict.fromkeys(v))


class QuoteRequestResult(BaseModel):
    ids: list[UUID]
    code: str  # mã hiển thị cho khách, vd "DZ-7F3A2C"
    contractor_names: list[str]
    estimate_session_id: UUID | None = None


class QuoteRequestError(ValueError):
    pass


def _session_slots(db: Session, data: QuoteRequestIn, chat_session_id: UUID | None) -> Slots | None:
    sid = data.chat_session_id or chat_session_id
    if sid is None and data.estimate_session_id is not None:
        est = db.get(EstimateSession, data.estimate_session_id)
        sid = est.chat_session_id if est is not None else None
    sess = db.get(ChatSession, sid) if sid is not None else None
    return split_raw(sess.slots)[0] if sess is not None else None


def note_with_preferences(note: str | None, prefs: list[str]) -> str | None:
    """Ghi chú gửi nhà thầu = ghi chú của khách + mong muốn đã ghi nhận (phong cách, mức đầu tư — chỉ để tham khảo,
    không thuộc khái toán)."""
    extra = [p for p in prefs if p not in (note or "")]
    if not extra:
        return note
    return "\n".join([*([note.strip()] if note and note.strip() else []), *extra])


def create_quote_requests(db: Session, data: QuoteRequestIn, chat_session_id: UUID | None = None,
                          slots: Slots | None = None) -> QuoteRequestResult:
    """Tạo 1 quote_requests cho mỗi nhà thầu, cùng estimate_session_id. Commit.

    Ghi chú gửi nhà thầu kèm phong cách thiết kế / mức đầu tư khách đã chọn trong hội thoại (`slots`, hoặc đọc từ phiên
    chat của yêu cầu). Ném QuoteRequestError nếu nhà thầu không tồn tại/không hoạt động hoặc estimate_session_id không
    tồn tại.
    """
    contractors = db.scalars(select(Contractor).where(Contractor.id.in_(data.contractor_ids),
                                                      Contractor.is_active.is_(True))).all()
    by_id = {c.id: c for c in contractors}
    missing = [i for i in data.contractor_ids if i not in by_id]
    if missing:
        raise QuoteRequestError(f"Không tìm thấy nhà thầu: {missing}")
    if data.estimate_session_id and not db.get(EstimateSession, data.estimate_session_id):
        raise QuoteRequestError("Không tìm thấy phiên khái toán")

    if slots is None:
        slots = _session_slots(db, data, chat_session_id)
    note = note_with_preferences(data.note, project_preferences(slots) if slots is not None else [])
    ids: list[UUID] = []
    for cid in data.contractor_ids:
        qr = QuoteRequest(id=uuid.uuid4(), estimate_session_id=data.estimate_session_id, contractor_id=cid,
                          customer_name=data.customer_name.strip(), customer_phone=data.customer_phone,
                          customer_email=data.customer_email, note=note, status="new")
        db.add(qr)
        ids.append(qr.id)
    code = "DZ-" + ids[0].hex[:6].upper()
    db.add(AnalyticsEvent(session_id=chat_session_id, event="quote_request_created",
                          props={"code": code, "contractor_ids": data.contractor_ids,
                                 "estimate_session_id": str(data.estimate_session_id)
                                 if data.estimate_session_id else None}))
    db.commit()
    names = [by_id[i].name for i in data.contractor_ids]
    _notify(db, data, code=code, names=names, note=note, slots=slots,
            via_chat=chat_session_id is not None)
    return QuoteRequestResult(ids=ids, code=code, contractor_names=names,
                              estimate_session_id=data.estimate_session_id)


def _notify(db: Session, data: QuoteRequestIn, *, code: str, names: list[str], note: str | None,
            slots: Slots | None, via_chat: bool) -> None:
    """Báo nhóm Lark (nếu cấu hình LARK_WEBHOOK_URL): đủ thông tin khách điền + nhà thầu + công trình + khái toán."""
    if not get_settings().lark_webhook_url.strip():
        return
    from app.services import notify_service  # noqa: PLC0415 — tránh vòng import (contact_service dùng quote_service)
    from app.services.contact_service import slots_summary  # noqa: PLC0415

    estimate = None
    est = db.get(EstimateSession, data.estimate_session_id) if data.estimate_session_id else None
    if est is not None and est.cost_min is not None and est.cost_max is not None:
        estimate = (f"{format_vnd_range(float(est.cost_min), float(est.cost_max))} "
                    f"(diện tích sàn quy đổi {format_m2(float(est.converted_area_m2))})")
    elif est is not None:
        estimate = f"Chưa có chi phí (diện tích sàn quy đổi {format_m2(float(est.converted_area_m2))})"
    notify_service.send_card(notify_service.quote_card(
        code=code, name=data.customer_name.strip(), phone=data.customer_phone, email=data.customer_email,
        note=note, contractors=names, project=slots_summary(slots) if slots is not None else None,
        estimate=estimate, channel="Chat với trợ lý AI" if via_chat else "Form yêu cầu báo giá"))
