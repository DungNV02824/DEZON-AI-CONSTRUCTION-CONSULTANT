"""Interface LLMProvider — bọc nhà cung cấp LLM để có thể thay đổi (đặc tả mục 3.1).

Provider cụ thể: `mock.py` (kịch bản, không cần mạng) và `anthropic_provider.py` (Claude).
Factory: `app.ai.providers.get_provider()`.
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema (sinh từ Pydantic)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)  # role=assistant
    tool_call_id: str | None = None  # role=tool
    name: str | None = None  # role=tool: tên tool
    # role=assistant: content block gốc của provider (vd Anthropic response.content, gồm cả thinking block)
    # để gửi lại NGUYÊN VẸN trong các vòng tool-use của cùng lượt. None với lịch sử dựng lại từ DB.
    raw: Any = None


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"  # end_turn | tool_use | max_tokens


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def chat(self, system: str, messages: list[Message], tools: list[ToolSpec],
             context: dict[str, Any] | None = None) -> LLMResponse:
        """Một lượt gọi LLM có function calling.

        `context` là thông tin phụ cho provider (vd slots hiện tại, stage) — provider thật có thể bỏ qua,
        provider mock dùng nó để quyết định kịch bản.
        """
        ...

    def extract(self, system: str, text: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Structured output: trả về dict khớp JSON schema. Provider không hỗ trợ -> trả {}."""
        ...
