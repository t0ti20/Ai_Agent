"""Wire-level adapter tests: real SDK clients over a fake HTTP transport, no network or keys."""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import openai
import pytest

from agent_base import Message, ModelError, ModelProfile
from agent_base.adapters.llm.anthropic import AnthropicAdapter
from agent_base.adapters.llm.base import ModelRequest, ModelResponse
from agent_base.adapters.llm.openai_compat import OpenAICompatAdapter, to_openai_messages
from agent_base.adapters.registry import create_adapter
from agent_base.capabilities.tools.decorator import ToolSpec
from agent_base.core.errors import ConfigError
from agent_base.core.events import TextDelta, ThinkingDelta
from agent_base.core.messages import ToolResultPart

TOOL = ToolSpec(
    "word_count",
    "Count words.",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
)


def sse(events: list[dict[str, Any]], named: bool = True) -> bytes:
    lines = []
    for event in events:
        if named:
            lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event)}\n")
    return ("\n".join(lines) + "\n").encode()


class Recorder:
    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def event_stream(body: bytes) -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)


CLAUDE_TOOL_TURN = sse(
    [
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 50,
                    "cache_creation_input_tokens": 10,
                },
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Count them."},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig-abc"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "Counting."},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "word_count",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"text": "a b c"}'},
        },
        {"type": "content_block_stop", "index": 2},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 30},
        },
        {"type": "message_stop"},
    ]
)


def claude(recorder: Recorder, **options: Any) -> AnthropicAdapter:
    profile = ModelProfile(
        adapter="anthropic",
        model="claude-opus-5-5",
        options=options,
        price={"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0},
    )
    adapter = AnthropicAdapter(profile)
    adapter._client = anthropic.AsyncAnthropic(  # pyright: ignore[reportPrivateUsage]
        api_key="test",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(recorder)),
    )
    return adapter


async def test_anthropic_request_shape_and_response_parsing() -> None:
    recorder = Recorder(event_stream(CLAUDE_TOOL_TURN))
    adapter = claude(recorder)
    request = ModelRequest(
        system="You count words.",
        messages=[Message.user("count a b c")],
        tools=[TOOL],
        effort="low",
    )
    chunks = [c async for c in adapter.stream(request)]

    body = recorder.body()
    assert body["model"] == "claude-opus-5-5"
    assert body["system"] == "You count words."
    assert body["max_tokens"] == 64_000
    assert body["output_config"] == {"effort": "low"}
    assert body["fallbacks"] == "default"
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0]["eager_input_streaming"] is True
    assert "temperature" not in body and "thinking" not in body
    assert "server-side-fallback-2026-07-01" in recorder.requests[-1].headers["anthropic-beta"]

    assert [type(c) for c in chunks] == [ThinkingDelta, TextDelta, ModelResponse]
    response = chunks[-1]
    assert isinstance(response, ModelResponse)
    assert response.stop_reason == "tool_use"
    assert response.message.text == "Counting."
    assert response.message.tool_calls[0].arguments == {"text": "a b c"}
    assert (response.usage.input_tokens, response.usage.cache_read_tokens) == (100, 50)
    assert response.usage.output_tokens == 30
    expected = (100 * 4.0 + 30 * 20.0 + 50 * 0.2 + 10 * 5.0) / 1_000_000
    assert adapter.cost(response.usage) == pytest.approx(expected)


async def test_anthropic_replays_its_own_blocks_unchanged() -> None:
    recorder = Recorder(
        event_stream(CLAUDE_TOOL_TURN),
        event_stream(CLAUDE_TOOL_TURN),
    )
    adapter = claude(recorder)
    first = await adapter.complete(
        ModelRequest(system="s", messages=[Message.user("go")], tools=[TOOL])
    )
    history = [
        Message.user("go"),
        first.message,
        Message(role="user", parts=[ToolResultPart(call_id="toolu_1", content="3")]),
    ]
    await adapter.complete(ModelRequest(system="s", messages=history, tools=[TOOL]))

    replayed = recorder.body()["messages"][1]["content"]
    assert replayed[0] == {"type": "thinking", "thinking": "Count them.", "signature": "sig-abc"}
    assert replayed[2]["type"] == "tool_use" and replayed[2]["id"] == "toolu_1"
    assert recorder.body()["messages"][2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "3",
        "is_error": False,
    }


async def test_anthropic_without_fallbacks_uses_the_standard_endpoint() -> None:
    recorder = Recorder(event_stream(CLAUDE_TOOL_TURN))
    adapter = claude(recorder, fallbacks=None, thinking_display="summarized")
    await adapter.complete(ModelRequest(system="", messages=[Message.user("hi")]))
    body = recorder.body()
    assert "fallbacks" not in body and "system" not in body
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "anthropic-beta" not in recorder.requests[-1].headers


async def test_anthropic_errors_become_model_errors() -> None:
    error = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    adapter = claude(Recorder(httpx2.Response(429, json=error)))
    with pytest.raises(ModelError) as info:
        await adapter.complete(ModelRequest(system="", messages=[Message.user("hi")]))
    assert info.value.retryable

    error = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    adapter = claude(Recorder(httpx2.Response(400, json=error)))
    with pytest.raises(ModelError) as info:
        await adapter.complete(ModelRequest(system="", messages=[Message.user("hi")]))
    assert not info.value.retryable


def test_anthropic_rejects_unknown_options() -> None:
    with pytest.raises(ConfigError, match="unknown anthropic options: fallback"):
        AnthropicAdapter(ModelProfile(adapter="anthropic", model="m", options={"fallback": "x"}))


def chunk(
    delta: dict[str, Any] | None = None, finish: str | None = None, **extra: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "qwen3:14b",
        "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return base | extra


def local(recorder: Recorder, **capabilities: Any) -> OpenAICompatAdapter:
    profile = ModelProfile(
        adapter="openai_compat",
        model="qwen3:14b",
        base_url="http://localhost:11434/v1",
        capabilities=capabilities,
    )
    adapter = OpenAICompatAdapter(profile)
    adapter._client = openai.AsyncOpenAI(  # pyright: ignore[reportPrivateUsage]
        api_key="unused",
        base_url="http://localhost:11434/v1",
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(recorder)),
    )
    return adapter


async def test_openai_compat_streams_tool_calls_and_reasoning() -> None:
    events = [
        chunk({"role": "assistant", "content": "", "reasoning": "Need a tool."}),
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_9",
                        "type": "function",
                        "function": {"name": "word_count", "arguments": '{"text":'},
                    }
                ]
            }
        ),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "a b"}'}}]}),
        chunk({}, finish="tool_calls"),
        chunk(None, usage={"prompt_tokens": 50, "completion_tokens": 9, "total_tokens": 59}),
    ]
    body = b"".join(sse([e], named=False) for e in events) + b"data: [DONE]\n\n"
    recorder = Recorder(event_stream(body))
    adapter = local(recorder, thinking=True)
    request = ModelRequest(
        system="sys", messages=[Message.user("count")], tools=[TOOL], effort="max"
    )
    chunks = [c async for c in adapter.stream(request)]

    sent = recorder.body()
    assert sent["messages"][0] == {"role": "system", "content": "sys"}
    assert sent["tools"][0]["function"]["name"] == "word_count"
    assert sent["reasoning_effort"] == "high"
    assert sent["stream_options"] == {"include_usage": True}
    assert isinstance(chunks[0], ThinkingDelta)
    response = chunks[-1]
    assert isinstance(response, ModelResponse)
    assert response.stop_reason == "tool_use"
    call = response.message.tool_calls[0]
    assert (call.id, call.name, call.arguments) == ("call_9", "word_count", {"text": "a b"})
    assert (response.usage.input_tokens, response.usage.output_tokens) == (50, 9)


async def test_openai_compat_flags_invalid_tool_json() -> None:
    events = [
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "c",
                        "function": {"name": "word_count", "arguments": "{oops"},
                    }
                ]
            }
        ),
        chunk({}, finish="stop"),
    ]
    body = b"".join(sse([e], named=False) for e in events) + b"data: [DONE]\n\n"
    response = await local(Recorder(event_stream(body))).complete(
        ModelRequest(system="", messages=[Message.user("x")], tools=[TOOL])
    )
    assert response.stop_reason == "tool_use"
    assert response.message.tool_calls[0].parse_error is not None


async def test_openai_compat_connection_errors_are_retryable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    adapter = local(Recorder())
    adapter._client = openai.AsyncOpenAI(  # pyright: ignore[reportPrivateUsage]
        api_key="unused",
        base_url="http://localhost:11434/v1",
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(refuse)),
    )
    with pytest.raises(ModelError, match="cannot reach http://localhost:11434/v1") as info:
        await adapter.complete(ModelRequest(system="", messages=[Message.user("x")]))
    assert info.value.retryable


def test_openai_message_conversion() -> None:
    from agent_base.core.messages import ToolCallPart

    history = [
        Message.user("hi"),
        Message(
            role="assistant",
            parts=[ToolCallPart(id="c1", name="word_count", arguments={"text": "x"})],
        ),
        Message(role="user", parts=[ToolResultPart(call_id="c1", content="boom", is_error=True)]),
    ]
    assert to_openai_messages(history) == [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "word_count", "arguments": '{"text": "x"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ERROR: boom"},
    ]


def test_registry_reports_missing_environment_variables() -> None:
    profile = ModelProfile(adapter="openai", model="gpt", api_key="${NOT_SET_ANYWHERE}")
    with pytest.raises(ConfigError, match="needs environment variable\\(s\\) NOT_SET_ANYWHERE"):
        create_adapter("openai", profile)
    with pytest.raises(ConfigError, match="unknown adapter 'nope'"):
        create_adapter("x", ModelProfile(adapter="nope", model="m"))
