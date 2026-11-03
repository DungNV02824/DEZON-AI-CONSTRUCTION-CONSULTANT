"""Yêu cầu liên hệ nhân viên (nút "Liên hệ nhân viên"; docs/API_CONTRACT.md "Yêu cầu liên hệ").

Khách để lại họ tên + số điện thoại để nhân viên Dezon gọi lại. Khác yêu cầu báo giá: không gửi cho nhà thầu, không
bắt buộc nhà thầu/khái toán. Có thể gắn phiên chat (nhân viên xem tóm tắt công trình) và nhà thầu khách đang xem.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.dialog import describe_budget, describe_location
from app.ai.state import split_raw
from app.db.models import AnalyticsEvent, ChatSession, ContactRequest, Contractor
from app.formatting import format_m2
from app.schemas.common import BUILDING_TYPE_VI, WORK_SCOPE_VI
from app.services.quote_service import normalize_phone

CONTACT_SOURCES = ("composer", "contractor_card", "contractor_detail")
DEDUP_WINDOW = timedelta(minutes=10)
CODE_PREFIX = "LH-"
MAX_NAME = 200


class ContactStatus(StrEnum):
    new = "new"
    contacted = "contacted"
    done = "done"


class ContactRequestError(ValueError):
    pass


def _blank_to_none(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


class ContactRequestIn(BaseModel):
    customer_name: str = Field(max_length=MAX_NAME)  # đã bỏ khoảng trắng hai đầu; rỗng -> lỗi ở create
    customer_phone: str = Field(max_length=50)
    note: str | None = Field(default=None, max_length=1000)
    chat_session_id: UUID | None = None
    contractor_id: int | None = None
    # composer | contractor_card | contractor_detail; kiểm tra ở create để báo lỗi tiếng Việt đúng ngữ cảnh.
    source: str | None = None
    page_url: str | None = Field(default=None, max_length=500)

    @field_validator("customer_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)

    @field_validator("customer_name", mode="before")
    @classmethod
    def _strip_name(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v

    @field_validator("note", "page_url", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return _blank_to_none(v)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, v: Any) -> Any:
        # Giá trị lạ (không phải chuỗi, quá dài...) vẫn tới bước kiểm tra CONTACT_SOURCES -> lỗi đúng hợp đồng.
        v = _blank_to_none(v)
        return v if v is None or (isinstance(v, str) and len(v) <= 50) else "__invalid__"


class ContactRequestCreated(BaseModel):
    id: UUID
    code: str  # mã hiển thị cho khách, vd "LH-7F3A2C"


class ContactRequestUpdate(BaseModel):
    status: ContactStatus | None = None
    handled_note: str | None = Field(default=None, max_length=1000)


class ContractorRef(BaseModel):
    id: int
    name: str


class ContactRequestOut(BaseModel):
    id: UUID
    code: str
    customer_name: str
    customer_phone: str
    note: str | None = None
    source: str | None = None
    page_url: str | None = None
    status: str
    handled_note: str | None = None
    created_at: datetime
    contractor: ContractorRef | None = None
    project: str | None = None  # tóm tắt công trình từ phiên chat, vd "Nhà phố · TP. Hồ Chí Minh · 4 tầng · Xây thô"


class ContactRequestList(BaseModel):
    items: list[ContactRequestOut]
    total: int
    new_count: int


# ------------------------------------------------------------------ tạo (công khai)
def _new_code(db: Session) -> str:
    for _ in range(10):
        code = CODE_PREFIX + secrets.token_hex(3).upper()
        if db.scalar(select(ContactRequest.id).where(ContactRequest.code == code)) is None:
            return code
    raise ContactRequestError("Chưa tạo được mã yêu cầu liên hệ, anh/chị vui lòng thử lại.")


def create_contact_request(db: Session, data: ContactRequestIn) -> ContactRequestCreated:
    """Lưu yêu cầu liên hệ. Commit.

    - Họ tên rỗng/quá dài, nguồn không hợp lệ -> ContactRequestError (route trả 422).
    - Phiên chat / nhà thầu không tồn tại -> bỏ qua (không lỗi).
    - Cùng SĐT + cùng phiên chat + cùng nhà thầu trong 10 phút, yêu cầu cũ còn "Mới" -> trả lại yêu cầu cũ (không tạo
      trùng) nhưng ghi nhận thông tin mới hơn (họ tên, ghi chú nối thêm, trang) để không mất nội dung khách vừa gửi.
      Yêu cầu cũ đã được nhân viên xử lý (Đã liên hệ / Xong) -> tạo yêu cầu mới.
    """
    name = data.customer_name.strip()
    if not name:
        raise ContactRequestError("Vui lòng nhập họ tên người liên hệ.")
    if data.source is not None and data.source not in CONTACT_SOURCES:
        raise ContactRequestError("Nguồn yêu cầu liên hệ không hợp lệ.")

    sid = data.chat_session_id if data.chat_session_id and db.get(ChatSession, data.chat_session_id) else None
    cid = data.contractor_id if data.contractor_id and db.get(Contractor, data.contractor_id) else None

    existing = db.scalars(
        select(ContactRequest)
        .where(ContactRequest.customer_phone == data.customer_phone,
               ContactRequest.chat_session_id.is_not_distinct_from(sid),
               ContactRequest.contractor_id.is_not_distinct_from(cid),
               ContactRequest.status == ContactStatus.new.value,
               ContactRequest.created_at >= func.now() - DEDUP_WINDOW)
        .order_by(ContactRequest.created_at.desc())
        .limit(1)).first()
    if existing is not None:
        changed = False
        if name != existing.customer_name:
            existing.customer_name, changed = name, True
        if data.note and data.note not in (existing.note or ""):
            existing.note = (f"{existing.note}\n{data.note}" if existing.note else data.note)[:1000]
            changed = True
        if data.page_url and data.page_url != existing.page_url:
            existing.page_url, changed = data.page_url, True
        if changed:
            existing.updated_at = func.now()
            db.commit()
        return ContactRequestCreated(id=existing.id, code=existing.code)

    code = _new_code(db)
    req = ContactRequest(id=uuid.uuid4(), code=code, customer_name=name, customer_phone=data.customer_phone,
                         note=data.note, chat_session_id=sid, contractor_id=cid, source=data.source,
                         page_url=data.page_url, status=ContactStatus.new.value)
    db.add(req)
    db.add(AnalyticsEvent(session_id=sid, event="contact_requested",
                          props={"code": code, "source": data.source, "has_contractor": cid is not None}))
    db.commit()
    return ContactRequestCreated(id=req.id, code=code)


# ------------------------------------------------------------------ admin
def project_summary(raw_slots: dict | None) -> str | None:
    """Tóm tắt công trình khách đã nêu trong phiên chat cho nhân viên: "Nhà phố · TP. Hồ Chí Minh · 100 m² · 4 tầng ·
    Xây thô · Ngân sách 2–4 tỷ". Chưa có thông tin nào -> None."""
    try:
        slots = split_raw(raw_slots)[0]
    except ValueError:
        return None
    parts: list[str] = []
    if slots.building_type:
        parts.append(BUILDING_TYPE_VI.get(str(slots.building_type), str(slots.building_type)))
    if slots.province:
        parts.append(describe_location(slots))
    if slots.land_area_m2:
        parts.append(format_m2(slots.land_area_m2))
    if slots.floors:
        parts.append(f"{slots.floors} tầng" + (" + lửng" if slots.has_mezzanine else ""))
    if slots.work_scope:
        parts.append(WORK_SCOPE_VI.get(str(slots.work_scope), str(slots.work_scope)))
    budget = describe_budget(slots)
    if budget:
        parts.append(f"Ngân sách {budget}")
    return " · ".join(parts) or None


def _out(req: ContactRequest) -> ContactRequestOut:
    contractor = req.contractor
    return ContactRequestOut(
        id=req.id, code=req.code, customer_name=req.customer_name, customer_phone=req.customer_phone,
        note=req.note, source=req.source, page_url=req.page_url, status=req.status, handled_note=req.handled_note,
        created_at=req.created_at,
        contractor=ContractorRef(id=contractor.id, name=contractor.name) if contractor is not None else None,
        project=project_summary(req.chat_session.slots) if req.chat_session is not None else None)


def list_contact_requests(db: Session, status: ContactStatus | None = None, limit: int = 50,
                          offset: int = 0) -> ContactRequestList:
    """Mới nhất trước. `total` theo bộ lọc; `new_count` = số yêu cầu chưa xử lý (không phụ thuộc bộ lọc)."""
    stmt = select(ContactRequest)
    count = select(func.count()).select_from(ContactRequest)
    if status is not None:
        stmt = stmt.where(ContactRequest.status == status.value)
        count = count.where(ContactRequest.status == status.value)
    rows = db.scalars(stmt.order_by(ContactRequest.created_at.desc(), ContactRequest.code)
                      .limit(limit).offset(offset)).all()
    new_count = db.scalar(select(func.count()).select_from(ContactRequest)
                          .where(ContactRequest.status == ContactStatus.new.value)) or 0
    return ContactRequestList(items=[_out(r) for r in rows], total=db.scalar(count) or 0, new_count=new_count)


def update_contact_request(db: Session, request_id: UUID, data: ContactRequestUpdate) -> ContactRequestOut | None:
    """Đổi trạng thái / ghi chú xử lý. Trường không gửi thì giữ nguyên; `handled_note: null` hoặc rỗng -> xóa ghi chú.
    Không tìm thấy -> None."""
    req = db.get(ContactRequest, request_id)
    if req is None:
        return None
    fields = data.model_fields_set
    if "status" in fields and data.status is not None:
        req.status = data.status.value
    if "handled_note" in fields:
        req.handled_note = _blank_to_none(data.handled_note)
    req.updated_at = func.now()
    db.commit()
    db.refresh(req)
    return _out(req)
