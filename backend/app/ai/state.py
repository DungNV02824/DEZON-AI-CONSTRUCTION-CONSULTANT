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

# Slot không ảnh hưởng tới con số khái toán: đổi chúng không bắt khách xác nhận lại / không tính lại. Phong cách và
# mức đầu tư chỉ ghi nhận (không vào công thức); riêng mức đầu tư nằm trong hash khái toán để bảng khái toán được dựng
# lại kèm ghi chú "Mức đầu tư ... chưa được tính vào khái toán" khi khách đổi mức đầu tư.
NON_ESTIMATE_SLOTS = frozenset({"budget_vnd", "budget_label", "start_time", "design_style", "investment_level"})


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
    # Khóa câu hỏi (dialog.QUESTION_KEYS) của thẻ question_step vừa hiển thị — dùng cho "Quay lại câu trước".
    current_question: str | None = None
    # Giữ trường cho phiên cũ (không còn dùng: câu tùy chọn không hỏi trong phần hỏi nữa).
    optional_done: bool = False
    # Khách bấm "Cung cấp thêm thông tin" trên bản xác nhận: hỏi lần lượt các mục chi tiết (diện tích xây dựng, hầm/
    # lửng, mái, độ sâu hầm, diện tích lửng) rồi quay lại bản tóm tắt. Tắt khi hiện lại bản tóm tắt / đã xác nhận.
    more_mode: bool = False
    # Khóa câu chi tiết (dialog.DETAIL_KEYS) còn thiếu lúc khách bấm "Cung cấp thêm thông tin" — bước i/N của lượt
    # hỏi thêm tính trên danh sách này (dialog.more_plan; có hầm / có lửng thì thêm độ sâu hầm / diện tích lửng).
    more_keys: list[str] = Field(default_factory=list)


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
    """Hash các slot ảnh hưởng tới khái toán (bỏ ngân sách, thời gian, phong cách, cờ xác nhận). Mức đầu tư chỉ góp vào
    hash khi có giá trị (khái toán kèm ghi chú mức đầu tư; phiên cũ chưa có trường này giữ nguyên hash)."""
    exclude = {"budget_vnd", "budget_label", "start_time", "confirmed", "asked", "design_style"}
    if slots.investment_level is None:
        exclude.add("investment_level")
    data = slots.model_dump(mode="json", exclude=exclude)
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
