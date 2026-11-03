"""Chat API (đặc tả mục 10; docs/API_CONTRACT.md mục Chat).

- POST /api/chat/sessions {user_id?} -> {session_id, stage, slots, messages} (tin chào + quick_replies)
- POST /api/chat/sessions/{id}/messages {content} -> SSE: text… ui_block… done | error
- GET  /api/chat/sessions/{id} -> {session_id, stage, slots, missing_required, messages}
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.ai.orchestrator import SessionNotFoundError
from app.ai.providers.base import LLMProvider
from app.api.deps import VietnameseRoute, client_ip
from app.db.session import get_db
from app.services import chat_service
from app.services.chat_service import RateLimiter, SessionCreatedOut, SessionStateOut

log = logging.getLogger(__name__)

# VietnameseRoute: lỗi validation (vd tin nhắn rỗng) -> 422 {detail, errors:[{field, message}]} tiếng Việt như các API
# khác, không trả lỗi pydantic thô.
router = APIRouter(prefix="/api/chat", tags=["chat"], route_class=VietnameseRoute)

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


class CreateSessionIn(BaseModel):
    user_id: str | None = Field(default=None, max_length=200)


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Nội dung tin nhắn không được để trống")
        return v


def get_chat_provider() -> LLMProvider:
    return chat_service.default_provider()


def _client_ip(request: Request) -> str:
    return client_ip(request)


@router.post("/sessions", response_model=SessionCreatedOut)
def create_session(body: CreateSessionIn | None = Body(default=None), db: Session = Depends(get_db)):
    return chat_service.create_session(db, body.user_id if body else None)


@router.get("/sessions/{session_id}", response_model=SessionStateOut)
def get_session(session_id: str, db: Session = Depends(get_db)):
    view = chat_service.get_session_view(db, session_id)
    if view is None:
        raise HTTPException(status_code=404, detail=chat_service.SESSION_NOT_FOUND_MESSAGE)
    return view


@router.post("/sessions/{session_id}/messages")
def send_message(session_id: str, body: SendMessageIn, request: Request, db: Session = Depends(get_db),
                 limiter: RateLimiter = Depends(chat_service.get_rate_limiter),
                 provider: LLMProvider = Depends(get_chat_provider)):
    allowed, retry_after = limiter.hit(_client_ip(request))
    if not allowed:
        return JSONResponse(status_code=429, content={"detail": chat_service.RATE_LIMIT_MESSAGE},
                            headers={"Retry-After": str(retry_after)})
    if chat_service.get_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail=chat_service.SESSION_NOT_FOUND_MESSAGE)
    # Văn bản chỉ được stream SAU KHI validator duyệt -> tính trọn lượt trước, rồi phát sự kiện.
    try:
        result = chat_service.send_message(db, session_id, body.content, provider=provider)
        events = chat_service.turn_events(result)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail=chat_service.SESSION_NOT_FOUND_MESSAGE) from None
    except Exception:  # noqa: BLE001 — trả sự kiện error tiếng Việt thay cho 500 giữa luồng
        log.exception("Lỗi xử lý tin nhắn chat session=%s", session_id)
        db.rollback()
        events = chat_service.error_events()
    return StreamingResponse(iter(events), media_type="text/event-stream", headers=SSE_HEADERS)
