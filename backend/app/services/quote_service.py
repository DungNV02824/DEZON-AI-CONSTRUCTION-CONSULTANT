"""Yêu cầu báo giá (đặc tả mục 8.3, 9.2 create_quote_request, 10 POST /api/quote-requests).

Dùng chung cho REST API và tool của orchestrator.
"""
import re
import uuid
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AnalyticsEvent, Contractor, EstimateSession, QuoteRequest

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


def create_quote_requests(db: Session, data: QuoteRequestIn, chat_session_id: UUID | None = None
                          ) -> QuoteRequestResult:
    """Tạo 1 quote_requests cho mỗi nhà thầu, cùng estimate_session_id. Commit.

    Ném QuoteRequestError nếu nhà thầu không tồn tại/không hoạt động hoặc estimate_session_id không tồn tại.
    """
    contractors = db.scalars(select(Contractor).where(Contractor.id.in_(data.contractor_ids),
                                                      Contractor.is_active.is_(True))).all()
    by_id = {c.id: c for c in contractors}
    missing = [i for i in data.contractor_ids if i not in by_id]
    if missing:
        raise QuoteRequestError(f"Không tìm thấy nhà thầu: {missing}")
    if data.estimate_session_id and not db.get(EstimateSession, data.estimate_session_id):
        raise QuoteRequestError("Không tìm thấy phiên khái toán")

    ids: list[UUID] = []
    for cid in data.contractor_ids:
        qr = QuoteRequest(id=uuid.uuid4(), estimate_session_id=data.estimate_session_id, contractor_id=cid,
                          customer_name=data.customer_name.strip(), customer_phone=data.customer_phone,
                          customer_email=data.customer_email, note=data.note, status="new")
        db.add(qr)
        ids.append(qr.id)
    code = "DZ-" + ids[0].hex[:6].upper()
    db.add(AnalyticsEvent(session_id=chat_session_id, event="quote_request_created",
                          props={"code": code, "contractor_ids": data.contractor_ids,
                                 "estimate_session_id": str(data.estimate_session_id)
                                 if data.estimate_session_id else None}))
    db.commit()
    return QuoteRequestResult(ids=ids, code=code, contractor_names=[by_id[i].name for i in data.contractor_ids],
                              estimate_session_id=data.estimate_session_id)
