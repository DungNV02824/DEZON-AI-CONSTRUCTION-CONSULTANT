"""AnthropicProvider (tùy chọn) — Messages API có tool use.

- `client.messages.create(model, max_tokens=16000, system, tools=[{name, description, input_schema}], messages,
  output_config={"effort": "low"})`. KHÔNG gửi temperature/top_p, KHÔNG gửi `thinking`, không dùng tool_choice
  any/tool (mặc định auto).
- `response.content` (gồm cả thinking block) được giữ trong `raw` của phản hồi; orchestrator đặt vào `Message.raw`
  và provider gửi lại NGUYÊN VẸN trong các vòng tool của cùng lượt.
- Kết quả của nhiều tool trong một vòng được gửi trong MỘT user message (nhiều khối tool_result).
- `stop_reason == "refusal"` -> LLMResponse(text="", stop_reason="refusal").
- `extract()`: output_config={"format": {"type": "json_schema", "schema": ...}}, json.loads khối text đầu tiên.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import anthropic

from app.ai.providers import LLMError
from app.ai.providers.base import LLMResponse, Message, ToolCall, ToolSpec
from app.config import Settings, get_settings

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
DEFAULT_TIMEOUT_S = 120.0
MAX_RETRIES = 2


@dataclass
class AnthropicResponse(LLMResponse):
    raw: Any = None  # response.content gốc (gồm thinking block) — gửi lại nguyên vẹn trong vòng tool


def _block_get(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _tool_result_block(m: Message) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.content or ""}
    try:
        data = json.loads(m.content or "")
        if isinstance(data, dict) and data.get("is_error") is True:
            block["is_error"] = True
    except (json.JSONDecodeError, ValueError):
        pass
    return block


def to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Chuyển Message nội bộ sang định dạng Messages API.

    - tool liên tiếp -> một user message nhiều khối tool_result;
    - user liền sau user (vd thông báo sinh lại sau tool_result) được gộp vào cùng user message.
    """
    out: list[dict[str, Any]] = []

    def push_user_blocks(blocks: list[dict[str, Any]]) -> None:
        if out and out[-1]["role"] == "user":
            prev = out[-1]["content"]
            if isinstance(prev, str):
                prev = [{"type": "text", "text": prev}] if prev else []
            out[-1]["content"] = prev + blocks
        else:
            out.append({"role": "user", "content": blocks})

    start = 0
    # Messages API: hội thoại bắt đầu bằng lượt user. Lịch sử dựng từ DB có thể mở đầu bằng lời chào của trợ lý
    # (chỉ văn bản) -> bỏ các tin assistant văn bản ở đầu (thông tin cần thiết đã có trong khối trạng thái).
    while (start < len(messages) and messages[start].role == "assistant" and not messages[start].tool_calls
           and messages[start].raw is None):
        start += 1
    for m in messages[start:]:
        if m.role == "user":
            if out and out[-1]["role"] == "user":
                push_user_blocks([{"type": "text", "text": m.content or ""}])
            else:
                out.append({"role": "user", "content": m.content or ""})
        elif m.role == "assistant":
            if m.raw is not None:
                out.append({"role": "assistant", "content": m.raw})
                continue
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments or {}})
            if not blocks:
                blocks.append({"type": "text", "text": "…"})
            out.append({"role": "assistant", "content": blocks})
        elif m.role == "tool":
            push_user_blocks([_tool_result_block(m)])
    return out


def to_anthropic_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]


def _wrap_error(exc: Exception) -> LLMError:
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("Anthropic: vượt hạn mức gọi API", "rate_limit", 429)
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError("Anthropic: hết thời gian chờ phản hồi", "timeout")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("Anthropic: không kết nối được", "connection")
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return LLMError("Anthropic: API key không hợp lệ hoặc không có quyền", "auth",
                        getattr(exc, "status_code", None))
    if isinstance(exc, (anthropic.BadRequestError, anthropic.NotFoundError, anthropic.UnprocessableEntityError)):
        return LLMError(f"Anthropic: yêu cầu không hợp lệ ({exc.__class__.__name__})", "bad_request",
                        getattr(exc, "status_code", None))
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError(f"Anthropic: lỗi máy chủ ({exc.status_code})", "server", exc.status_code)
    return LLMError(f"Anthropic: lỗi không xác định ({exc.__class__.__name__})", "unknown")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings | None = None, client: Any = None):
        self.settings = settings or get_settings()
        model = self.settings.llm_model or DEFAULT_MODEL
        # Cấu hình mặc định của dự án là model OpenAI; khi chọn anthropic mà chưa đổi LLM_MODEL thì dùng Claude.
        self.model = DEFAULT_MODEL if model.startswith(("gpt-", "o1", "o3", "o4")) else model
        if client is None:
            kwargs: dict[str, Any] = {"max_retries": MAX_RETRIES, "timeout": DEFAULT_TIMEOUT_S}
            if self.settings.llm_api_key:
                kwargs["api_key"] = self.settings.llm_api_key
            if self.settings.llm_base_url:
                kwargs["base_url"] = self.settings.llm_base_url
            client = anthropic.Anthropic(**kwargs)
        self.client = client

    def chat(self, system: str, messages: list[Message], tools: list[ToolSpec],
             context: dict[str, Any] | None = None) -> LLMResponse:
        params: dict[str, Any] = {"model": self.model, "max_tokens": MAX_TOKENS,
                                  "messages": to_anthropic_messages(messages),
                                  "output_config": {"effort": "low"}}
        if system:
            params["system"] = system
        if tools:
            params["tools"] = to_anthropic_tools(tools)
        try:
            resp = self.client.messages.create(**params)
        except anthropic.AnthropicError as exc:
            log.warning("Anthropic chat lỗi: %s", exc.__class__.__name__)
            raise _wrap_error(exc) from exc
        content = list(getattr(resp, "content", None) or [])
        stop = getattr(resp, "stop_reason", None) or "end_turn"
        if stop == "refusal":
            return AnthropicResponse(text="", stop_reason="refusal", raw=content)
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in content:
            btype = _block_get(block, "type")
            if btype == "text":
                texts.append(_block_get(block, "text", "") or "")
            elif btype == "tool_use":
                inp = _block_get(block, "input", {})
                calls.append(ToolCall(id=_block_get(block, "id", ""), name=_block_get(block, "name", ""),
                                      arguments=inp if isinstance(inp, dict) else {}))
        return AnthropicResponse(text="".join(texts), tool_calls=calls,
                                 stop_reason="tool_use" if calls else stop, raw=content)

    def extract(self, system: str, text: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=MAX_TOKENS, system=system,
                messages=[{"role": "user", "content": text}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.AnthropicError as exc:
            raise _wrap_error(exc) from exc
        if getattr(resp, "stop_reason", None) == "refusal":
            return {}
        for block in getattr(resp, "content", None) or []:
            if _block_get(block, "type") == "text":
                try:
                    data = json.loads(_block_get(block, "text", "") or "")
                except (json.JSONDecodeError, ValueError):
                    return {}
                return data if isinstance(data, dict) else {}
        return {}
