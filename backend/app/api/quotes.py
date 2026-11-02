"""REST yêu cầu báo giá (đặc tả mục 8.3, 10, 13; docs/API_CONTRACT.md "Yêu cầu báo giá")."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import MAX_BIGINT, VietnameseRoute, unprocessable
from app.db.session import get_db
from app.services.quote_service import QuoteRequestError, QuoteRequestIn, create_quote_requests

router = APIRouter(prefix="/api", tags=["quotes"], route_class=VietnameseRoute)


class QuoteRequestCreated(BaseModel):
    ids: list[UUID]
    code: str


@router.post("/quote-requests", response_model=QuoteRequestCreated, status_code=201)
def create_quote_request(body: QuoteRequestIn, db: Session = Depends(get_db)) -> QuoteRequestCreated:
    if any(i < 1 or i > MAX_BIGINT for i in body.contractor_ids):
        raise unprocessable("Mã nhà thầu không hợp lệ.")
    if not body.customer_name.strip():
        raise unprocessable("Vui lòng nhập họ tên người liên hệ.")
    try:
        res = create_quote_requests(db, body)
    except QuoteRequestError as exc:
        raise unprocessable(str(exc)) from None
    return QuoteRequestCreated(ids=res.ids, code=res.code)
