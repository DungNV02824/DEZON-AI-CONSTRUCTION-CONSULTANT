"""Dịch vụ chat: tạo phiên, đọc lịch sử, gửi tin nhắn (qua orchestrator), dựng sự kiện SSE, rate limit.

Hợp đồng: docs/API_CONTRACT.md mục Chat.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime
from functools import lru_cache
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import orchestrator
from app.ai.dialog import GREETING_EXPECTED_SLOTS, greeting
from app.ai.providers.base import LLMProvider
from app.ai.state import SessionState, public_slots, split_raw, to_raw
from app.config import get_settings
from app.db.models import AnalyticsEvent, ChatMessage, ChatSession
from app.schemas.slots import Slots, missing_required

log = logging.getLogger(__name__)

ERROR_MESSAGE = "Dạ, hệ thống đang gặp sự cố nên em chưa trả lời được. Anh/chị vui lòng thử lại sau ít phút ạ."
RATE_LIMIT_MESSAGE = ("Anh/chị đang gửi tin nhắn quá nhanh. Vui lòng chờ khoảng một phút rồi gửi tiếp giúp em "
                      "nhé.")
SESSION_NOT_FOUND_MESSAGE = "Không tìm thấy phiên chat. Anh/chị vui lòng tải lại để bắt đầu phiên mới."


class ChatMessageOut(BaseModel):
    id: int
    role: str  # user | assistant
    content: str
    ui_blocks: list[dict[str, Any]]
    created_at: datetime | None = None


class SessionCreatedOut(BaseModel):
    session_id: uuid.UUID
    stage: str
    slots: dict[str, Any]
    messages: list[ChatMessageOut]


class SessionStateOut(BaseModel):
    session_id: uuid.UUID
    stage: str
    slots: dict[str, Any]
    missing_required: list[str]
    messages: list[ChatMessageOut]


def message_out(m: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(id=m.id, role=m.role, content=m.content or "", ui_blocks=list(m.ui_blocks or []),
                          created_at=m.created_at)


def _parse_id(session_id: uuid.UUID | str) -> uuid.UUID | None:
    if isinstance(session_id, uuid.UUID):
        return session_id
    try:
        return uuid.UUID(str(session_id))
    except ValueError:
        return None


def get_session(db: Session, session_id: uuid.UUID | str) -> ChatSession | None:
    sid = _parse_id(session_id)
    return db.get(ChatSession, sid) if sid else None


def create_session(db: Session, user_id: str | None = None) -> SessionCreatedOut:
    """Tạo phiên + tin chào (dialog.greeting) kèm quick replies; ghi analytics "session_started"."""
    sid = uuid.uuid4()
    slots, state = Slots(), SessionState(expected_slots=list(GREETING_EXPECTED_SLOTS))
    sess = ChatSession(id=sid, user_id=user_id, slots=to_raw(slots, state), stage="collect")
    db.add(sess)
    db.flush()
    text, quick = greeting()
    msg = ChatMessage(session_id=sid, role="assistant", content=text, ui_blocks=[quick.model_dump(mode="json")])
    db.add(msg)
    db.add(AnalyticsEvent(session_id=sid, event="session_started", props={"user_id": user_id} if user_id else {}))
    db.flush()
    db.refresh(msg)
    out = SessionCreatedOut(session_id=sid, stage="collect", slots=public_slots(slots), messages=[message_out(msg)])
    db.commit()
    return out


def get_session_view(db: Session, session_id: uuid.UUID | str) -> SessionStateOut | None:
    sess = get_session(db, session_id)
    if sess is None:
        return None
    slots, _ = split_raw(sess.slots)
    rows = db.scalars(select(ChatMessage).where(ChatMessage.session_id == sess.id,
                                                ChatMessage.role.in_(("user", "assistant")))
                      .order_by(ChatMessage.id)).all()
    return SessionStateOut(session_id=sess.id, stage=sess.stage, slots=public_slots(slots),
                           missing_required=missing_required(slots), messages=[message_out(m) for m in rows])


def send_message(db: Session, session_id: uuid.UUID | str, content: str,
                 provider: LLMProvider | None = None) -> orchestrator.TurnResult:
    return orchestrator.handle_message(db, session_id, content, provider=provider)


# ============================================================================================
# SSE
# ============================================================================================
def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


_PIECE_RE = re.compile(r"\S+\s*|\s+")


def split_text(text: str, max_chars: int = 48) -> list[str]:
    """Chia văn bản (đã validate) thành nhiều mảnh theo từ; nối lại đúng bằng văn bản gốc."""
    pieces = _PIECE_RE.findall(text or "")
    out: list[str] = []
    buf = ""
    for p in pieces:
        if buf and len(buf) + len(p) > max_chars:
            out.append(buf)
            buf = ""
        buf += p
    if buf:
        out.append(buf)
    return out


def turn_events(result: orchestrator.TurnResult) -> list[str]:
    events = [sse_event("text", {"delta": d}) for d in split_text(result.text)]
    events += [sse_event("ui_block", {"block": b.model_dump(mode="json")}) for b in result.ui_blocks]
    events.append(sse_event("done", {"message_id": result.message_id, "stage": result.stage, "slots": result.slots,
                                     "missing_required": result.missing_required}))
    return events


def error_events(message: str = ERROR_MESSAGE) -> list[str]:
    return [sse_event("error", {"message": message})]


# ============================================================================================
# Rate limit (20 tin/phút/IP): Redis nếu có, fallback bộ nhớ trong tiến trình
# ============================================================================================
class RateLimiter(Protocol):
    def hit(self, key: str) -> tuple[bool, int]:
        """Ghi nhận một lượt. Trả (được phép?, số giây nên chờ nếu bị chặn)."""
        ...


class MemoryRateLimiter:
    """Cửa sổ trượt 60 giây trong bộ nhớ tiến trình (dùng khi không có Redis)."""

    def __init__(self, limit: int, window_s: int = 60, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, int]:
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(1, int(self.window - (now - q[0])) + 1)
            q.append(now)
            return True, 0


class RedisRateLimiter:
    """Cửa sổ cố định theo phút trên Redis (INCR + EXPIRE). Redis lỗi -> chuyển sang bộ nhớ."""

    def __init__(self, client: Any, limit: int, window_s: int = 60, prefix: str = "dezon:rl:chat",
                 fallback: MemoryRateLimiter | None = None, clock=time.time):
        self.client, self.limit, self.window, self.prefix, self.clock = client, limit, window_s, prefix, clock
        self.fallback = fallback or MemoryRateLimiter(limit, window_s)

    def hit(self, key: str) -> tuple[bool, int]:
        now = self.clock()
        bucket = int(now // self.window)
        rkey = f"{self.prefix}:{key}:{bucket}"
        try:
            pipe = self.client.pipeline()
            pipe.incr(rkey)
            pipe.expire(rkey, self.window + 5)
            count = int(pipe.execute()[0])
        except Exception:  # noqa: BLE001 — Redis mất kết nối: không chặn khách vì lỗi hạ tầng
            log.warning("Redis rate limit lỗi — dùng bộ nhớ trong tiến trình", exc_info=True)
            return self.fallback.hit(key)
        if count > self.limit:
            return False, max(1, int(self.window - (now % self.window)) + 1)
        return True, 0


@lru_cache
def get_rate_limiter() -> RateLimiter:
    """FastAPI dependency (singleton). Test có thể override bằng app.dependency_overrides."""
    settings = get_settings()
    limit = settings.chat_rate_limit_per_min
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.3, socket_timeout=0.3)
        client.ping()
        return RedisRateLimiter(client, limit)
    except Exception:  # noqa: BLE001
        log.warning("Không kết nối được Redis — rate limit chat dùng bộ nhớ trong tiến trình")
        return MemoryRateLimiter(limit)


@lru_cache
def default_provider() -> LLMProvider:
    from app.ai.providers import get_provider

    return get_provider()
