"""Trạng thái phụ của phiên chat (lưu trong `chat_sessions.slots` dưới khóa `_state`).

`chat_sessions.slots` = {<các slot mục 5.2>..., "_state": SessionState}. Khóa `_state` là nội bộ, luôn bị loại khỏi
slots trả cho frontend (`public_slots`). Lý do lưu chung cột: không cần migration, đọc/ghi cùng lúc với slots.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.slots import Slots

STATE_KEY = "_state"

# Slot không ảnh hưởng tới con số khái toán: đổi chúng không bắt khách xác nhận lại / không tính lại.
NON_ESTIMATE_SLOTS = frozenset({"budget_vnd", "start_time"})


class EstimateRef(BaseModel):
    id: str  # estimate_session_id
    cost_min: int | None = None
    cost_max: int | None = None
    cost_display: str = ""
    slots_hash: str | None = None  # hash slot khái toán lúc tính (None: tính theo tham số khác slot)


class ContractorRef(BaseModel):
    id: int
    name: str
    slug: str | None = None


class SessionState(BaseModel):
    expected_slots: list[str] | None = None  # slot câu hỏi trước đang hỏi (None: chưa hỏi gì — lượt đầu)
    user_turns: int = 0
    slots_completed_logged: bool = False
    estimate: EstimateRef | None = None  # khái toán hiện tại
    budget_checked_for: str | None = None  # "<estimate_id>:<budget>" đã gọi suggest_budget_adjustments
    matched_for: str | None = None  # "<estimate_id>:<budget>" đã chạy search_contractors tự động
    recommendation_log_id: int | None = None
    contractors: list[ContractorRef] = Field(default_factory=list)  # nhà thầu đề xuất gần nhất (theo thứ hạng)
    presented: bool = False  # đã so sánh / xem hồ sơ / gửi báo giá


def split_raw(raw: dict[str, Any] | None) -> tuple[Slots, SessionState]:
    data = dict(raw or {})
    st = data.pop(STATE_KEY, None) or {}
    try:
        state = SessionState.model_validate(st)
    except ValueError:
        state = SessionState()
    return Slots.model_validate(data), state


def to_raw(slots: Slots, state: SessionState) -> dict[str, Any]:
    data = slots.model_dump(mode="json")
    data[STATE_KEY] = state.model_dump(mode="json")
    return data


# Trường nội bộ của dialog (không phải slot nghiệp vụ) — không trả ra client.
INTERNAL_SLOT_FIELDS = frozenset({"asked"})


def public_slots(raw: dict[str, Any] | Slots | None) -> dict[str, Any]:
    """Slots trả cho frontend: chỉ slot nghiệp vụ (bỏ `_state` và trường nội bộ như `asked`)."""
    slots = raw if isinstance(raw, Slots) else split_raw(raw)[0]
    return slots.model_dump(mode="json", exclude=set(INTERNAL_SLOT_FIELDS))


def estimate_slots_hash(slots: Slots) -> str:
    """Hash các slot ảnh hưởng tới khái toán (bỏ ngân sách, thời gian, cờ xác nhận)."""
    data = slots.model_dump(mode="json", exclude={"budget_vnd", "start_time", "confirmed", "asked"})
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
