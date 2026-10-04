"""Any OpenAI-format Chat Completions endpoint: Ollama, vLLM, LM Studio, llama.cpp, LocalAI.

Servers differ in how much of the OpenAI spec they implement, so features that
not every server has are gated by capability flags that a profile can switch
on: `thinking: true` sends `reasoning_effort`, `tool_calling: false` stops
sending tools. Profile options: `stream_usage` (bool, default true) and
`extra_body` (a mapping merged into the request as-is).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Generator
from contextlib import contextmanager
from typing import Any, ClassVar

from agent_base.adapters.llm.base import (
    Capabilities,
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    ModelStopReason,
    StreamChunk,
)
from agent_base.config.models import Effort, ModelProfile
from agent_base.core.errors import ConfigError, ModelError
from agent_base.core.events import TextDelta, ThinkingDelta, Usage
from agent_base.core.messages import Message, TextPart, ThinkingPart, ToolCallPart

OPTIONS = {"stream_usage", "extra_body"}

_FINISH: dict[str, ModelStopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}
_EFFORT: dict[Effort, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}


class OpenAICompatAdapter(ModelAdapter):
    name = "openai_compat"
    default_capabilities: ClassVar[Capabilities] = Capabilities(tool_calling=True, streaming=True)
    max_tokens_param: ClassVar[str] = "max_tokens"

    def __init__(self, profile: ModelProfile) -> None:
        super().__init__(profile)
        try:
            import openai
        except ImportError:
            raise ConfigError(
                f"the {self.name} adapter needs the SDK: pip install 'agent-base[openai]'"
            ) from None
        unknown = set(profile.options) - OPTIONS
        if unknown:
            raise ConfigError(
                f"unknown {self.name} options: {', '.join(sorted(unknown))} "
                f"(known: {', '.join(sorted(OPTIONS))})"
            )
        self._sdk = openai
        try:
            self._client = openai.AsyncOpenAI(
                api_key=self._api_key(),
                base_url=self._base_url(),
                timeout=profile.timeout_s,
                max_retries=profile.max_retries,
            )
        except openai.OpenAIError as exc:
            raise ConfigError(f"{self.name}: {exc}") from None

    def _api_key(self) -> str | None:
        # Local servers ignore the key, but the SDK insists on having one.
        return self.profile.api_key or "not-needed"

    def _base_url(self) -> str | None:
        if not self.profile.base_url:
            raise ConfigError(f"a profile using the {self.name} adapter needs base_url")
        return self.profile.base_url

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        text: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish: str | None = None
        model = self.profile.model
        usage = Usage()

        with self._translate_errors():
            stream = await self._client.chat.completions.create(**self._params(request))
            async for chunk in stream:
                model = chunk.model or model
                if chunk.usage:
                    usage = _usage(chunk.usage)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text.append(delta.content)
                    yield TextDelta(text=delta.content)
                # Not in the OpenAI spec; Ollama, vLLM and DeepSeek-style servers send one of these.
                thought = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "reasoning", None
                )
                if isinstance(thought, str) and thought:
                    reasoning.append(thought)
                    yield ThinkingDelta(text=thought)
                for position, call in enumerate(delta.tool_calls or []):
                    index = call.index if call.index is not None else position
                    slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if call.id:
                        slot["id"] = call.id
                    if call.function and call.function.name and not slot["name"]:
                        slot["name"] = call.function.name
                    if call.function and call.function.arguments:
                        slot["arguments"] += call.function.arguments
                if choice.finish_reason:
                    finish = choice.finish_reason

        parts: list[Any] = []
        if reasoning:
            parts.append(ThinkingPart(text="".join(reasoning)))
        if text:
            parts.append(TextPart(text="".join(text)))
        parts.extend(_tool_call(calls[i]) for i in sorted(calls))
        stop: ModelStopReason = (
            "tool_use" if calls and finish != "length" else _FINISH.get(finish or "", "other")
        )
        yield ModelResponse(
            message=Message(role="assistant", parts=parts),
            stop_reason=stop,
            usage=usage,
            model=model,
        )

    def _params(self, request: ModelRequest) -> dict[str, Any]:
        caps = self.capabilities()
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(to_openai_messages(request.messages))
        params: dict[str, Any] = {"model": self.profile.model, "messages": messages, "stream": True}
        if self.profile.options.get("stream_usage", True):
            params["stream_options"] = {"include_usage": True}
        if request.tools and caps.tool_calling:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        max_tokens = request.max_output_tokens or self.profile.max_output_tokens
        if max_tokens:
            params[self.max_tokens_param] = max_tokens
        if self.profile.temperature is not None:
            params["temperature"] = self.profile.temperature
        if request.effort and caps.thinking:
            params["reasoning_effort"] = _EFFORT[request.effort]
        if self.profile.options.get("extra_body"):
            params["extra_body"] = self.profile.options["extra_body"]
        return params

    @contextmanager
    def _translate_errors(self) -> Generator[None, None, None]:
        sdk = self._sdk
        try:
            yield
        except sdk.RateLimitError as exc:
            raise ModelError(
                f"rate limited: {exc.message}", retryable=True, adapter=self.name
            ) from exc
        except sdk.APIStatusError as exc:
            retryable = exc.status_code >= 500 or exc.status_code in (408, 409)
            raise ModelError(
                f"HTTP {exc.status_code}: {exc.message}", retryable=retryable, adapter=self.name
            ) from exc
        except sdk.APIConnectionError as exc:
            where = self.profile.base_url or "the API"
            raise ModelError(
                f"cannot reach {where}: {exc}", retryable=True, adapter=self.name
            ) from exc


def to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "user":
            for result in message.tool_results:
                content = f"ERROR: {result.content}" if result.is_error else result.content
                out.append({"role": "tool", "tool_call_id": result.call_id, "content": content})
            if message.text:
                out.append({"role": "user", "content": message.text})
            continue
        entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
        if message.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ]
        if entry["content"] is not None or message.tool_calls:
            out.append(entry)
    return out


def _tool_call(slot: dict[str, str]) -> ToolCallPart:
    call_id = slot["id"] or f"call_{uuid.uuid4().hex[:12]}"
    try:
        arguments = json.loads(slot["arguments"]) if slot["arguments"].strip() else {}
    except json.JSONDecodeError as exc:
        return ToolCallPart(id=call_id, name=slot["name"], parse_error=f"invalid JSON: {exc}")
    if not isinstance(arguments, dict):
        return ToolCallPart(
            id=call_id, name=slot["name"], parse_error="arguments must be an object"
        )
    return ToolCallPart(id=call_id, name=slot["name"], arguments=arguments)


def _usage(raw: Any) -> Usage:
    details = getattr(raw, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", None) or 0) if details else 0
    prompt = raw.prompt_tokens or 0
    return Usage(
        input_tokens=max(prompt - cached, 0),
        output_tokens=raw.completion_tokens or 0,
        cache_read_tokens=cached,
    )
