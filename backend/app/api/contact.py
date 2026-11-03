"""REST yêu cầu liên hệ nhân viên (nút "Liên hệ nhân viên"; docs/API_CONTRACT.md "Yêu cầu liên hệ")."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import MAX_BIGINT, VietnameseRoute, client_ip, unprocessable
from app.db.session import get_db
from app.services import chat_service
from app.services.chat_service import RateLimiter
from app.services.contact_service import (
    ContactRequestCreated,
    ContactRequestError,
    ContactRequestIn,
    create_contact_request,
)

router = APIRouter(prefix="/api", tags=["contact"], route_class=VietnameseRoute)


RATE_LIMIT_MESSAGE = "Anh/chị đã gửi quá nhiều yêu cầu liên hệ, vui lòng thử lại sau ít phút."


@router.post("/contact-requests", response_model=ContactRequestCreated, status_code=201,
             responses={429: {"description": RATE_LIMIT_MESSAGE}})
def create_contact(body: ContactRequestIn, request: Request, db: Session = Depends(get_db),
                   limiter: RateLimiter = Depends(chat_service.get_contact_rate_limiter)):
    # Endpoint công khai lưu số điện thoại: giới hạn theo IP để chống spam hàng đợi của nhân viên.
    allowed, retry_after = limiter.hit(client_ip(request))
    if not allowed:
        return JSONResponse(status_code=429, content={"detail": RATE_LIMIT_MESSAGE},
                            headers={"Retry-After": str(retry_after)})
    if body.contractor_id is not None and not 1 <= body.contractor_id <= MAX_BIGINT:
        raise unprocessable("Mã nhà thầu không hợp lệ.")
    try:
        return create_contact_request(db, body)
    except ContactRequestError as exc:
        raise unprocessable(str(exc)) from None
