"""OpenAIProvider — Chat Completions có function calling (nhà cung cấp chính: gpt-4.1-mini).

- `chat()`: tools=[{"type": "function", "function": {name, description, parameters}}], tool_choice="auto".
  Message nội bộ (user / assistant có tool_calls / tool có tool_call_id) được chuyển sang định dạng OpenAI.
- `extract()`: structured output `response_format={"type": "json_schema", "json_schema": {..., "strict": True}}`.
- Lỗi SDK (RateLimitError, APITimeoutError, APIConnectionError, AuthenticationError, APIStatusError...) được
  chuyển thành `LLMError` để orchestrator trả lời dự phòng thay vì 500.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import openai

from app.ai.providers import LLMError
from app.ai.providers.base import LLMResponse, Message, ToolCall, ToolSpec
from app.config import Settings, get_settings

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0  # x (1 + MAX_RETRIES) lần thử; khách đang chờ SSE
MAX_RETRIES = 2
CHAT_TEMPERATURE = 0.2
EXTRACT_SCHEMA_NAME = "slot_extraction"

_FINISH_TO_STOP = {"tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens",
                   "content_filter": "refusal", "stop": "end_turn"}


@dataclass
class OpenAIResponse(LLMResponse):
    raw: Any = None  # message gốc của SDK (để debug/log)


def parse_arguments(raw: Any) -> dict[str, Any]:
    """JSON.parse an toàn cho `function.arguments`. Không phải object JSON hợp lệ -> {}."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        val = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        log.warning("OpenAI trả arguments không phải JSON hợp lệ: %.200s", raw)
        return {}
    return val if isinstance(val, dict) else {}


def to_openai_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content or ""})
        elif m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                msg["tool_calls"] = [{
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments or {}, ensure_ascii=False)},
                } for tc in m.tool_calls]
            elif not m.content:
                msg["content"] = ""
            out.append(msg)
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id or "", "content": m.content or ""})
    return out


def to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.input_schema}} for t in tools]


def _wrap_error(exc: Exception) -> LLMError:
    # Thứ tự cụ thể -> tổng quát (APITimeoutError là lớp con của APIConnectionError).
    if isinstance(exc, openai.RateLimitError):
        return LLMError("OpenAI: vượt hạn mức gọi API", "rate_limit", getattr(exc, "status_code", 429))
    if isinstance(exc, openai.APITimeoutError):
        return LLMError("OpenAI: hết thời gian chờ phản hồi", "timeout")
    if isinstance(exc, openai.APIConnectionError):
        return LLMError("OpenAI: không kết nối được", "connection")
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return LLMError("OpenAI: API key không hợp lệ hoặc không có quyền", "auth", getattr(exc, "status_code", None))
    if isinstance(exc, (openai.BadRequestError, openai.NotFoundError, openai.UnprocessableEntityError)):
        return LLMError(f"OpenAI: yêu cầu không hợp lệ ({exc.__class__.__name__})", "bad_request",
                        getattr(exc, "status_code", None))
    if isinstance(exc, openai.APIStatusError):
        return LLMError(f"OpenAI: lỗi máy chủ ({exc.status_code})", "server", exc.status_code)
    return LLMError(f"OpenAI: lỗi không xác định ({exc.__class__.__name__})", "unknown")


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings | None = None, client: Any = None,
                 timeout: float = DEFAULT_TIMEOUT_S):
        self.settings = settings or get_settings()
        self.model = self.settings.llm_model or "gpt-4.1-mini"
        if client is None:
            kwargs: dict[str, Any] = {"api_key": self.settings.llm_api_key or None, "timeout": timeout,
                                      "max_retries": MAX_RETRIES}
            if self.settings.llm_base_url:
                kwargs["base_url"] = self.settings.llm_base_url
            client = openai.OpenAI(**kwargs)
        self.client = client

    # ------------------------------------------------------------------ chat
    def chat(self, system: str, messages: list[Message], tools: list[ToolSpec],
             context: dict[str, Any] | None = None) -> LLMResponse:
        params: dict[str, Any] = {"model": self.model, "messages": to_openai_messages(system, messages),
                                  "temperature": CHAT_TEMPERATURE}
        if tools:
            params["tools"] = to_openai_tools(tools)
            params["tool_choice"] = "auto"
        try:
            resp = self.client.chat.completions.create(**params)
        except openai.OpenAIError as exc:
            log.warning("OpenAI chat lỗi: %s", exc.__class__.__name__)
            raise _wrap_error(exc) from exc
        if not getattr(resp, "choices", None):
            raise LLMError("OpenAI: phản hồi không có lựa chọn nào", "server")
        choice = resp.choices[0]
        msg = choice.message
        if getattr(msg, "refusal", None):
            return OpenAIResponse(text="", stop_reason="refusal", raw=msg)
        calls: list[ToolCall] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            if getattr(tc, "type", "function") != "function" or fn is None:
                continue  # custom tool call — không dùng
            calls.append(ToolCall(id=tc.id, name=fn.name, arguments=parse_arguments(fn.arguments)))
        stop = _FINISH_TO_STOP.get(getattr(choice, "finish_reason", None) or "stop", "end_turn")
        if calls:
            stop = "tool_use"
        return OpenAIResponse(text=msg.content or "", tool_calls=calls, stop_reason=stop, raw=msg)

    # ------------------------------------------------------------------ extract
    def extract(self, system: str, text: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
                temperature=0,
                response_format={"type": "json_schema",
                                 "json_schema": {"name": EXTRACT_SCHEMA_NAME, "schema": schema, "strict": True}},
            )
        except openai.OpenAIError as exc:
            raise _wrap_error(exc) from exc
        if not getattr(resp, "choices", None):
            return {}
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None) or not msg.content:
            return {}
        try:
            data = json.loads(msg.content)
        except (json.JSONDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
