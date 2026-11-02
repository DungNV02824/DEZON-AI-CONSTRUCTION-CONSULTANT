"""Factory nhà cung cấp LLM (đặc tả mục 3.1, 9).

`get_provider(settings=None)` chọn provider theo `settings.resolved_llm_provider`:
- `mock`      -> MockProvider (kịch bản xác định, không mạng — demo/test khi chưa có key)
- `openai`    -> OpenAIProvider (mặc định khi có OPENAI_API_KEY; model gpt-4.1-mini)
- `anthropic` -> AnthropicProvider (tùy chọn)

Module này KHÔNG import provider cụ thể ở cấp module (tránh vòng import và không bắt buộc SDK khi dùng mock).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.ai.providers.base import LLMProvider
    from app.config import Settings


class LLMError(RuntimeError):
    """Lỗi gọi nhà cung cấp LLM (hết hạn mức, mất kết nối, lỗi HTTP...).

    Orchestrator bắt lỗi này để trả lời dự phòng thân thiện thay vì 500.
    `kind`: rate_limit | timeout | connection | auth | bad_request | server | refusal | unknown.
    """

    def __init__(self, message: str, kind: str = "unknown", status_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


def get_provider(settings: Settings | None = None) -> LLMProvider:
    from app.config import get_settings

    settings = settings or get_settings()
    name = settings.resolved_llm_provider
    if name == "openai":
        from app.ai.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(settings)
    if name == "anthropic":
        from app.ai.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings)
    if name == "mock":
        from app.ai.providers.mock import MockProvider

        return MockProvider()
    raise ValueError(f"LLM_PROVIDER không hợp lệ: {name!r} (mock | openai | anthropic | auto)")


__all__ = ["LLMError", "get_provider"]
