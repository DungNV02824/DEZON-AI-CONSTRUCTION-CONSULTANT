"""Gợi ý câu hỏi tiếp theo (nút bấm cuối lượt trả lời) theo ngữ cảnh hội thoại.

Chỉ hiện khi luồng chính đã xong (đã xác nhận, có khái toán và nhà thầu đề xuất) và lượt này không chờ khách trả lời
câu hỏi nào (thẻ câu hỏi, bản xác nhận, câu hỏi ngân sách, form báo giá). LLM gợi ý (structured output qua
`provider.extract`, chạy song song với câu trả lời chính để không thêm độ trễ); LLM lỗi / chậm / provider mock ->
câu mẫu theo ngữ cảnh, chọn ngẫu nhiên (cố định theo phiên + lượt). Mọi gợi ý đều lọc qua cùng quy tắc (độ dài, không
"tốt nhất", không trùng nút sẵn có / câu khách vừa hỏi).
"""
from __future__ import annotations

import logging
import random
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from app.ai.normalize import fold
from app.ai.providers.base import LLMProvider
from app.schemas.common import BUILDING_TYPE_VI, WORK_SCOPE_VI
from app.schemas.ui_blocks import QuickReply, SuggestionsBlock

log = logging.getLogger(__name__)

MAX_SUGGESTIONS = 5
# Chờ thêm tối đa N giây sau khi câu trả lời chính xong (LLM gợi ý chạy song song từ trước) rồi dùng câu mẫu.
WAIT_AFTER_REPLY_S = 3.0
MIN_LEN, MAX_LEN = 8, 90
# Nút sẵn có cuối lượt (NEXT_ACTION_REPLIES) + các câu chung -> không gợi ý trùng.
_RESERVED = ("so sanh cac nha thau", "yeu cau bao gia", "toi muon yeu cau bao gia")
_BANNED = ("tot nhat",)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dz-suggest")

SCHEMA = {
    "type": "object",
    "properties": {"suggestions": {"type": "array", "items": {"type": "string"}}},
    "required": ["suggestions"],
    "additionalProperties": False,
}

SYSTEM = """Bạn gợi ý câu hỏi tiếp theo cho khách hàng đang trò chuyện với trợ lý tư vấn xây dựng của Dezon
(nền tảng kết nối chủ nhà với nhà thầu). Dựa vào ngữ cảnh, viết 8 câu hỏi NGẮN (tối đa 70 ký tự) mà khách có thể muốn
hỏi tiếp — giọng khách hàng, xưng "tôi", tiếng Việt có dấu, mỗi câu khác chủ đề nhau, liên quan trực tiếp tới công trình
và các nhà thầu trong ngữ cảnh.
Chỉ gợi ý những câu trợ lý trả lời được bằng dữ liệu trên Dezon:
- hồ sơ, công trình thực tế, đơn giá của MỘT nhà thầu đã đề xuất (nêu đúng tên);
- so sánh HAI nhà thầu cụ thể đã đề xuất (nêu tên);
- đơn giá tham khảo xây thô / trọn gói theo loại công trình và khu vực;
- khái toán thay đổi thế nào nếu đổi số tầng, diện tích hoặc phạm vi (xây thô / trọn gói);
- khái toán đã gồm / chưa gồm những gì;
- nhà thầu khác trong khu vực.
Không gợi ý: "So sánh các nhà thầu", "Yêu cầu báo giá", câu khách vừa hỏi, thời gian thi công, pháp lý, vay vốn,
phong thủy. Không dùng từ "tốt nhất", không hứa hẹn giá. Trả về JSON {"suggestions": [...]}."""


@dataclass
class SuggestContext:
    building_type: str | None = None
    province: str | None = None
    floors: int | None = None
    land_area_m2: float | None = None
    work_scope: str | None = None
    budget_vnd: int | None = None
    contractors: list[str] = field(default_factory=list)
    last_user_message: str = ""
    seed: str = ""

    @property
    def building_label(self) -> str:
        return BUILDING_TYPE_VI.get(str(self.building_type), "công trình").lower() if self.building_type else "công trình"

    def describe(self) -> str:
        parts = [f"Loại công trình: {self.building_label}"]
        if self.province:
            parts.append(f"Khu vực: {self.province}")
        if self.land_area_m2:
            parts.append(f"Diện tích đất: {self.land_area_m2:g} m²")
        if self.floors:
            parts.append(f"Số tầng: {self.floors}")
        if self.work_scope:
            parts.append(f"Phạm vi: {WORK_SCOPE_VI.get(str(self.work_scope), str(self.work_scope))}")
        parts.append("Ngân sách: " + ("đã có" if self.budget_vnd else "chưa cung cấp"))
        if self.contractors:
            parts.append("Nhà thầu đã đề xuất: " + ", ".join(self.contractors))
        if self.last_user_message:
            parts.append(f"Câu khách vừa nhắn: {self.last_user_message[:200]}")
        return "\n".join(parts)


def _clean(raw: object, ctx: SuggestContext, seen: set[str]) -> str | None:
    if not isinstance(raw, str):
        return None
    s = raw.strip().strip("-•*\"'“”").strip()
    if not MIN_LEN <= len(s) <= MAX_LEN or "\n" in s:
        return None
    f = fold(s)
    if any(b in f for b in _BANNED) or f.rstrip(" ?.!") in _RESERVED:
        return None
    if ctx.last_user_message and f.rstrip(" ?.!") == fold(ctx.last_user_message).rstrip(" ?.!"):
        return None
    if f == s.lower():  # không có dấu tiếng Việt nào -> bỏ (khách nhận chữ có dấu)
        return None
    if f in seen:
        return None
    seen.add(f)
    return s


def template_candidates(ctx: SuggestContext) -> list[str]:
    """Câu mẫu theo ngữ cảnh (đều trả lời được bằng tool hiện có)."""
    bt, where = ctx.building_label, f" tại {ctx.province}" if ctx.province else ""
    out: list[str] = []
    names = ctx.contractors
    for n in names[:3]:
        out.append(f"Hồ sơ của {n} có những công trình nào?")
    if len(names) >= 2:
        out.append(f"{names[0]} và {names[1]} khác nhau thế nào?")
    if ctx.work_scope == "xay_tho":
        out.append("Nếu chọn trọn gói thì chi phí khoảng bao nhiêu?")
    elif ctx.work_scope == "tron_goi":
        out.append("Nếu chỉ xây thô thì chi phí khoảng bao nhiêu?")
    out.append(f"Đơn giá xây thô {bt}{where} hiện khoảng bao nhiêu?")
    if ctx.floors:
        out.append(f"Nếu xây {ctx.floors + 1} tầng thì chi phí thay đổi thế nào?")
    out.append("Khái toán này chưa bao gồm những chi phí nào?")
    if ctx.province:
        out.append(f"Còn nhà thầu nào khác{where} không?")
    return out


def pick(candidates: list[str], ctx: SuggestContext, k: int = MAX_SUGGESTIONS) -> list[str]:
    """Lọc rồi chọn ngẫu nhiên k câu (cố định theo `ctx.seed`: cùng phiên + lượt -> cùng gợi ý)."""
    seen: set[str] = set()
    ok = [c for c in (_clean(x, ctx, seen) for x in candidates) if c]
    rng = random.Random(ctx.seed)
    return rng.sample(ok, k) if len(ok) > k else ok


def _llm_candidates(provider: LLMProvider, ctx: SuggestContext) -> list[str]:
    data = provider.extract(SYSTEM, ctx.describe(), SCHEMA)
    items = data.get("suggestions") if isinstance(data, dict) else None
    return [x for x in items if isinstance(x, str)] if isinstance(items, list) else []


def start(provider: LLMProvider, ctx: SuggestContext) -> Future | None:
    """Bắt đầu gọi LLM gợi ý ở luồng nền (song song với câu trả lời chính). Provider mock -> None (dùng câu mẫu)."""
    if getattr(provider, "name", "") == "mock":
        return None
    try:
        return _executor.submit(_llm_candidates, provider, ctx)
    except RuntimeError:  # executor đã đóng (tắt tiến trình)
        return None


def build(ctx: SuggestContext, future: Future | None, wait_s: float = WAIT_AFTER_REPLY_S) -> SuggestionsBlock | None:
    """Gợi ý từ LLM (nếu kịp và đủ câu hợp lệ), bổ sung bằng câu mẫu; không có câu nào -> None."""
    llm: list[str] = []
    if future is not None:
        try:
            llm = future.result(timeout=wait_s)
        except FutureTimeout:
            log.info("Gợi ý LLM quá %ss — dùng câu mẫu", wait_s)
        except Exception:  # noqa: BLE001 — gợi ý là phần phụ, không được làm hỏng lượt chat
            log.warning("Gợi ý LLM lỗi — dùng câu mẫu", exc_info=True)
    chosen = pick(llm, ctx)
    if len(chosen) < MAX_SUGGESTIONS:
        seen = {fold(c) for c in chosen}
        rest = [t for t in template_candidates(ctx) if fold(t) not in seen]
        chosen += pick(rest, ctx, MAX_SUGGESTIONS - len(chosen))
    if not chosen:
        return None
    return SuggestionsBlock(options=[QuickReply(label=c, value=c) for c in chosen])


__all__ = ["MAX_SUGGESTIONS", "SuggestContext", "build", "pick", "start", "template_candidates"]
