"""Provider LLM: OpenAI / Anthropic với client giả (không gọi mạng), factory, MockProvider; test live tùy chọn."""
import json
import os
from types import SimpleNamespace

import anthropic
import httpx2
import openai
import pytest

from app.ai.providers import LLMError, get_provider
from app.ai.providers.anthropic_provider import AnthropicProvider, to_anthropic_messages
from app.ai.providers.base import Message, ToolCall, ToolSpec
from app.ai.providers.mock import MockProvider, contractor_name_in
from app.ai.providers.openai_provider import OpenAIProvider, parse_arguments, to_openai_messages
from app.ai.slot_filling import EXTRACTION_JSON_SCHEMA
from app.config import Settings

TOOLS = [ToolSpec(name="calculate_estimate", description="Tính khái toán",
                  input_schema={"type": "object", "properties": {"floors": {"type": "integer"}}})]
CONVO = [
    Message(role="user", content="Tính giúp tôi"),
    Message(role="assistant", content="", tool_calls=[ToolCall(id="c1", name="calculate_estimate",
                                                               arguments={"floors": 4})]),
    Message(role="tool", content='{"ok": true, "result": {"cost": "x"}}', tool_call_id="c1",
            name="calculate_estimate"),
]


def _settings(**kw) -> Settings:
    base = {"llm_provider": "mock", "llm_api_key": "sk-test", "llm_model": "gpt-4.1-mini", "llm_base_url": ""}
    base.update(kw)
    return Settings(**base)


def _req():
    return httpx2.Request("POST", "https://api.example.com/v1/x")


# =============================================================================================
# Factory
# =============================================================================================
def test_get_provider_selects_by_settings():
    assert get_provider(_settings(llm_provider="mock")).name == "mock"
    assert isinstance(get_provider(_settings(llm_provider="openai")), OpenAIProvider)
    assert isinstance(get_provider(_settings(llm_provider="anthropic")), AnthropicProvider)
    assert get_provider(_settings(llm_provider="auto", llm_api_key="")).name == "mock"
    assert get_provider(_settings(llm_provider="auto", llm_api_key="sk-x")).name == "openai"
    with pytest.raises(ValueError):
        get_provider(_settings(llm_provider="khac"))


def test_openai_client_uses_model_and_base_url():
    p = OpenAIProvider(_settings(llm_base_url="https://proxy.example.com/v1"))
    assert p.model == "gpt-4.1-mini"
    assert str(p.client.base_url).startswith("https://proxy.example.com/v1")
    assert p.client.max_retries == 2


# =============================================================================================
# OpenAI (client giả)
# =============================================================================================
class FakeOpenAI:
    def __init__(self, responses=None, error=None):
        self.calls: list[dict] = []
        self.responses = list(responses or [])
        self.error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


def _oa_resp(content=None, tool_calls=None, finish="stop", refusal=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])


def _oa_call(id_, name, args):
    return SimpleNamespace(id=id_, type="function", function=SimpleNamespace(name=name, arguments=args))


def test_openai_message_conversion():
    out = to_openai_messages("SYS", CONVO)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == {"role": "user", "content": "Tính giúp tôi"}
    assert out[2]["role"] == "assistant" and out[2]["content"] is None
    tc = out[2]["tool_calls"][0]
    assert tc["id"] == "c1" and tc["type"] == "function"
    assert tc["function"]["name"] == "calculate_estimate" and json.loads(tc["function"]["arguments"]) == {"floors": 4}
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true, "result": {"cost": "x"}}'}


def test_openai_chat_sends_tools_and_parses_tool_calls():
    fake = FakeOpenAI([_oa_resp(tool_calls=[_oa_call("call_1", "calculate_estimate", '{"floors": 3}'),
                                            _oa_call("call_2", "calculate_estimate", "{hỏng")],
                                finish="tool_calls")])
    p = OpenAIProvider(_settings(), client=fake)
    r = p.chat("SYS", CONVO, TOOLS)
    kw = fake.calls[0]
    assert kw["model"] == "gpt-4.1-mini" and kw["tool_choice"] == "auto"
    assert kw["tools"] == [{"type": "function", "function": {"name": "calculate_estimate",
                                                             "description": "Tính khái toán",
                                                             "parameters": TOOLS[0].input_schema}}]
    assert r.stop_reason == "tool_use"
    assert [(c.id, c.name, c.arguments) for c in r.tool_calls] == [
        ("call_1", "calculate_estimate", {"floors": 3}), ("call_2", "calculate_estimate", {})]


def test_openai_chat_text_and_no_tools_param_when_empty():
    fake = FakeOpenAI([_oa_resp(content="Dạ, chào anh/chị")])
    r = OpenAIProvider(_settings(), client=fake).chat("SYS", [Message(role="user", content="hi")], [])
    assert r.text == "Dạ, chào anh/chị" and r.tool_calls == [] and r.stop_reason == "end_turn"
    assert "tools" not in fake.calls[0] and "tool_choice" not in fake.calls[0]


def test_openai_refusal_and_length():
    fake = FakeOpenAI([_oa_resp(refusal="no"), _oa_resp(content="cắt", finish="length")])
    p = OpenAIProvider(_settings(), client=fake)
    assert p.chat("S", [Message(role="user", content="x")], []).stop_reason == "refusal"
    assert p.chat("S", [Message(role="user", content="x")], []).stop_reason == "max_tokens"


def test_parse_arguments_safe():
    assert parse_arguments('{"a": 1}') == {"a": 1}
    assert parse_arguments("[1,2]") == {}
    assert parse_arguments("") == {}
    assert parse_arguments(None) == {}
    assert parse_arguments({"b": 2}) == {"b": 2}


def test_openai_extract_uses_strict_json_schema():
    fake = FakeOpenAI([_oa_resp(content='{"building_type": "nha_pho", "intent": "provide_info"}')])
    out = OpenAIProvider(_settings(), client=fake).extract("SYS", "nhà phố", EXTRACTION_JSON_SCHEMA)
    assert out == {"building_type": "nha_pho", "intent": "provide_info"}
    rf = fake.calls[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] is EXTRACTION_JSON_SCHEMA
    assert EXTRACTION_JSON_SCHEMA["additionalProperties"] is False


def test_openai_extract_bad_json_returns_empty():
    fake = FakeOpenAI([_oa_resp(content="không phải json")])
    assert OpenAIProvider(_settings(), client=fake).extract("S", "x", {}) == {}


@pytest.mark.parametrize("exc,kind", [
    (lambda: openai.RateLimitError("rl", response=httpx2.Response(429, request=_req()), body=None), "rate_limit"),
    (lambda: openai.APITimeoutError(request=_req()), "timeout"),
    (lambda: openai.APIConnectionError(request=_req()), "connection"),
    (lambda: openai.AuthenticationError("auth", response=httpx2.Response(401, request=_req()), body=None), "auth"),
    (lambda: openai.BadRequestError("bad", response=httpx2.Response(400, request=_req()), body=None),
     "bad_request"),
    (lambda: openai.InternalServerError("boom", response=httpx2.Response(500, request=_req()), body=None),
     "server"),
])
def test_openai_errors_wrapped_as_llm_error(exc, kind):
    p = OpenAIProvider(_settings(), client=FakeOpenAI(error=exc()))
    with pytest.raises(LLMError) as ei:
        p.chat("S", [Message(role="user", content="x")], TOOLS)
    assert ei.value.kind == kind
    with pytest.raises(LLMError):
        p.extract("S", "x", {})


# =============================================================================================
# Anthropic (client giả)
# =============================================================================================
class FakeAnthropic:
    def __init__(self, responses=None, error=None):
        self.calls: list[dict] = []
        self.responses = list(responses or [])
        self.error = error
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


def _an_resp(content, stop="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop)


def test_anthropic_default_model_when_openai_model_configured():
    assert AnthropicProvider(_settings(), client=FakeAnthropic()).model == "claude-opus-5-5"
    assert AnthropicProvider(_settings(llm_model="claude-sonnet-5-5"), client=FakeAnthropic()).model == \
        "claude-sonnet-5-5"


def test_anthropic_message_conversion_groups_tool_results():
    msgs = [
        Message(role="user", content="Tính giúp tôi"),
        Message(role="assistant", content="Dạ", tool_calls=[
            ToolCall(id="t1", name="calculate_estimate", arguments={"floors": 4}),
            ToolCall(id="t2", name="search_contractors", arguments={})]),
        Message(role="tool", content='{"ok": true}', tool_call_id="t1", name="calculate_estimate"),
        Message(role="tool", content='{"is_error": true, "error": "x"}', tool_call_id="t2", name="search_contractors"),
        Message(role="user", content="Viết lại"),
    ]
    out = to_anthropic_messages(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[1]["content"] == [
        {"type": "text", "text": "Dạ"},
        {"type": "tool_use", "id": "t1", "name": "calculate_estimate", "input": {"floors": 4}},
        {"type": "tool_use", "id": "t2", "name": "search_contractors", "input": {}}]
    blocks = out[2]["content"]
    assert [b["type"] for b in blocks] == ["tool_result", "tool_result", "text"]
    assert blocks[0]["tool_use_id"] == "t1" and "is_error" not in blocks[0]
    assert blocks[1]["is_error"] is True
    assert blocks[2]["text"] == "Viết lại"


def test_anthropic_raw_content_sent_back_unchanged():
    raw = [SimpleNamespace(type="thinking", thinking="", signature="sig"),
           SimpleNamespace(type="tool_use", id="t1", name="calculate_estimate", input={})]
    out = to_anthropic_messages([Message(role="user", content="x"),
                                 Message(role="assistant", tool_calls=[ToolCall("t1", "calculate_estimate", {})],
                                         raw=raw)])
    assert out[1]["content"] is raw


def test_anthropic_chat_params_and_parsing():
    content = [SimpleNamespace(type="thinking", thinking="", signature="s"),
               SimpleNamespace(type="text", text="Dạ, em tính nhé"),
               SimpleNamespace(type="tool_use", id="tu_1", name="calculate_estimate", input={"floors": 4})]
    fake = FakeAnthropic([_an_resp(content, stop="tool_use")])
    p = AnthropicProvider(_settings(), client=fake)
    r = p.chat("SYS", [Message(role="user", content="x")], TOOLS)
    kw = fake.calls[0]
    assert kw["max_tokens"] == 16000 and kw["system"] == "SYS" and kw["output_config"] == {"effort": "low"}
    assert kw["tools"] == [{"name": "calculate_estimate", "description": "Tính khái toán",
                            "input_schema": TOOLS[0].input_schema}]
    for forbidden in ("temperature", "top_p", "thinking", "tool_choice"):
        assert forbidden not in kw
    assert r.text == "Dạ, em tính nhé" and r.stop_reason == "tool_use"
    assert [(c.id, c.name, c.arguments) for c in r.tool_calls] == [("tu_1", "calculate_estimate", {"floors": 4})]
    assert r.raw == content  # giữ nguyên thinking block để gửi lại


def test_anthropic_refusal():
    fake = FakeAnthropic([_an_resp([SimpleNamespace(type="text", text="...")], stop="refusal")])
    r = AnthropicProvider(_settings(), client=fake).chat("S", [Message(role="user", content="x")], [])
    assert r.text == "" and r.stop_reason == "refusal"
    assert "tools" not in fake.calls[0]


def test_anthropic_extract_uses_output_config_format():
    fake = FakeAnthropic([_an_resp([SimpleNamespace(type="text", text='{"intent": "confirm"}')])])
    out = AnthropicProvider(_settings(), client=fake).extract("SYS", "đúng rồi", EXTRACTION_JSON_SCHEMA)
    assert out == {"intent": "confirm"}
    assert fake.calls[0]["output_config"] == {"format": {"type": "json_schema", "schema": EXTRACTION_JSON_SCHEMA}}


def test_anthropic_errors_wrapped():
    err = anthropic.RateLimitError("rl", response=httpx2.Response(429, request=_req()), body=None)
    with pytest.raises(LLMError) as ei:
        AnthropicProvider(_settings(), client=FakeAnthropic(error=err)).chat("S", [Message(role="user",
                                                                                           content="x")], [])
    assert ei.value.kind == "rate_limit"
    with pytest.raises(LLMError) as ei:
        AnthropicProvider(_settings(), client=FakeAnthropic(error=anthropic.APIConnectionError(request=_req()))
                          ).chat("S", [Message(role="user", content="x")], [])
    assert ei.value.kind == "connection"


# =============================================================================================
# MockProvider
# =============================================================================================
def test_mock_extract_empty_and_name():
    m = MockProvider()
    assert m.name == "mock" and m.extract("s", "nhà phố", {}) == {}


def test_contractor_name_detection():
    assert contractor_name_in("đơn giá của Huy Hoàng là bao nhiêu?") == "Huy Hoàng"
    assert contractor_name_in("cho em xem hồ sơ Kiến Phong") == "Kiến Phong"
    assert contractor_name_in("nhà thầu nào tốt nhất?") is None
    assert contractor_name_in("smartcons tech có bảo hành không", [{"id": 2, "name": "Smartcons Tech"}]) == \
        "Smartcons Tech"


@pytest.mark.parametrize("msg,ctx,tool", [
    ("So sánh các nhà thầu", {"nha_thau_da_de_xuat": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]},
     "compare_contractors"),
    ("so sánh 3 nhà thầu", {}, "search_contractors"),
    ("đơn giá của Huy Hoàng là bao nhiêu?", {}, "get_contractor_profile"),
    ("Nhà thầu nào thi công biệt thự tốt tại TP.HCM?", {}, "get_contractor_overview"),
    ("nhà thầu nào tốt nhất?", {}, "get_contractor_overview"),
    ("Tìm nhà thầu giúp tôi", {}, "search_contractors"),
    ("Báo giá giúp tôi, tên tôi là Lan, sđt 0912345678", {}, "create_quote_request"),
])
def test_mock_tool_choice(msg, ctx, tool):
    r = MockProvider().chat("S", [Message(role="user", content=msg)], [ToolSpec(tool, "", {})] + [
        ToolSpec(n, "", {}) for n in ("search_contractors", "compare_contractors")],
        {**ctx, "last_user_message": msg})
    assert r.tool_calls and r.tool_calls[0].name == tool


def test_mock_overview_args_parsed():
    msg = "Nhà thầu nào thi công biệt thự tốt tại TP.HCM?"
    r = MockProvider().chat("S", [Message(role="user", content=msg)],
                            [ToolSpec("get_contractor_overview", "", {})], {"last_user_message": msg})
    assert r.tool_calls[0].arguments == {"province": "TP. Hồ Chí Minh", "building_type": "biet_thu"}


def test_mock_quote_without_name_does_not_call_tool():
    msg = "báo giá giúp tôi, sđt 0912345678"
    ctx = {"last_user_message": msg, "nha_thau_da_de_xuat": [{"id": 1, "name": "Kiến Phong"}]}
    r = MockProvider().chat("S", [Message(role="user", content=msg)], [ToolSpec("create_quote_request", "", {})], ctx)
    assert not r.tool_calls and "form" in r.text


def test_mock_quote_without_contractors_does_not_mention_form():
    """Chưa có nhà thầu đề xuất thì hệ thống không hiện form -> văn bản không được nhắc 'form bên dưới'."""
    msg = "Tôi muốn yêu cầu báo giá"
    r = MockProvider().chat("S", [Message(role="user", content=msg)], [ToolSpec("create_quote_request", "", {})],
                            {"last_user_message": msg})
    assert not r.tool_calls and "form" not in r.text and "tìm nhà thầu" in r.text


def test_mock_reference_rates_uses_scope_from_question():
    msg = "đơn giá hoàn thiện ở HCM là bao nhiêu?"
    ctx = {"last_user_message": msg, "slots": {"building_type": "nha_pho", "province": "TP. Hồ Chí Minh",
                                               "work_scope": "xay_tho"}}
    r = MockProvider().chat("S", [Message(role="user", content=msg)], [ToolSpec("get_reference_rates", "", {})], ctx)
    assert r.tool_calls[0].name == "get_reference_rates"
    assert r.tool_calls[0].arguments["work_scope"] == "hoan_thien"


def test_anthropic_messages_drop_leading_greeting():
    out = to_anthropic_messages([Message(role="assistant", content="Xin chào anh/chị!"),
                                 Message(role="user", content="Nhà phố")])
    assert out == [{"role": "user", "content": "Nhà phố"}]


def test_mock_writes_from_tool_results_without_best_claim():
    result = {"ok": True, "result": {"results": [
        {"fit_label": "Phù hợp", "contractor": {"name": "Kiến Phong"},
         "reasons": [{"text": "7 công trình ghi nhận trên Dezon"}, {"text": "Hoạt động tại TP. Hồ Chí Minh"}]}]}}
    msgs = [Message(role="user", content="tìm nhà thầu"),
            Message(role="assistant", tool_calls=[ToolCall("auto_1", "search_contractors", {})]),
            Message(role="tool", content=json.dumps(result, ensure_ascii=False), tool_call_id="auto_1",
                    name="search_contractors")]
    r = MockProvider().chat("S", msgs, [ToolSpec("search_contractors", "", {})], {"last_user_message": "tìm nhà thầu"})
    assert not r.tool_calls
    assert "Kiến Phong" in r.text and "7 công trình ghi nhận trên Dezon" in r.text
    assert "tốt nhất" not in r.text and "phù hợp nhất với tiêu chí của anh/chị" in r.text


def test_mock_lie_mode():
    r = MockProvider(lie_text="5,2 triệu/m²").chat("S", [Message(role="user", content="xin chào")], [],
                                                   {"last_user_message": "xin chào"})
    assert r.text == "5,2 triệu/m²"


# =============================================================================================
# Live (tùy chọn): RUN_LIVE_LLM=1 + OPENAI_API_KEY
# =============================================================================================
@pytest.mark.live_llm
@pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM") != "1" or not os.environ.get("OPENAI_API_KEY"),
                    reason="Chỉ chạy khi RUN_LIVE_LLM=1 và có OPENAI_API_KEY")
def test_live_openai_short_conversation():
    settings = Settings(llm_provider="openai", llm_api_key=os.environ["OPENAI_API_KEY"],
                        llm_model=os.environ.get("MODEL_NAME", "gpt-4.1-mini"))
    p = OpenAIProvider(settings)
    r = p.chat("Bạn là trợ lý xây dựng. Trả lời tiếng Việt, ngắn gọn.",
               [Message(role="user", content="Tính khái toán nhà 4 tầng giúp tôi")], TOOLS)
    assert r.tool_calls or r.text
    out = p.extract("Trích thông tin công trình.", "Tôi muốn xây nhà phố 5x20 ở Thủ Đức", EXTRACTION_JSON_SCHEMA)
    assert out.get("building_type") == "nha_pho"
