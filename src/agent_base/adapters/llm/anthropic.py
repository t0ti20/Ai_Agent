"""Claude through the Anthropic Messages API.

Choices worth knowing:

- Always streams (no HTTP timeouts on long turns) and assembles the final
  message with the SDK's `get_final_message()`.
- Assistant turns are replayed from `Message.native`, byte for byte, so
  thinking blocks stay valid. The runtime keeps history append-only for the
  same reason.
- `output_config.effort` carries the unified effort knob. Current models think
  adaptively by default and reject `temperature`, so it is only sent (through
  `extra_body`) when a profile sets it for an older model.
- Server-side refusal fallback is on by default (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`): a request the model declines is re-run on
  Anthropic's recommended fallback model inside the same call. Set
  `options.fallbacks: null` to turn it off, or a list of model IDs to pin targets.
- Automatic prompt caching (`cache_control` at the top level) is on by default.

Profile options: `fallbacks`, `thinking_display` ("summarized" to stream
readable thinking), `prompt_cache` (bool).
"""

from __future__ import annotations

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
from agent_base.config.models import ModelProfile
from agent_base.core.errors import ConfigError, ModelError
from agent_base.core.events import TextDelta, ThinkingDelta, Usage
from agent_base.core.messages import (
    Message,
    NativeContent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
)

DEFAULT_MAX_TOKENS = 64_000
FALLBACK_DEFAULT_BETA = "server-side-fallback-2026-07-01"
FALLBACK_LIST_BETA = "server-side-fallback-2026-06-01"
OPTIONS = {"fallbacks", "thinking_display", "prompt_cache"}

_STOP: dict[str, ModelStopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
    "pause_turn": "pause",
}


class AnthropicAdapter(ModelAdapter):
    name = "anthropic"
    default_capabilities: ClassVar[Capabilities] = Capabilities(
        tool_calling=True,
        parallel_tools=True,
        vision=True,
        json_mode=True,
        streaming=True,
        thinking=True,
        max_context=1_000_000,
        max_output=128_000,
    )

    def __init__(self, profile: ModelProfile) -> None:
        super().__init__(profile)
        try:
            import anthropic
        except ImportError:
            raise ConfigError(
                "the anthropic adapter needs the SDK: pip install 'agent-base[anthropic]'"
            ) from None
        unknown = set(profile.options) - OPTIONS
        if unknown:
            raise ConfigError(
                f"unknown anthropic options: {', '.join(sorted(unknown))} "
                f"(known: {', '.join(sorted(OPTIONS))})"
            )
        self._sdk = anthropic
        self._client = anthropic.AsyncAnthropic(
            api_key=profile.api_key,
            base_url=profile.base_url,
            timeout=profile.timeout_s,
            max_retries=profile.max_retries,
        )
        self._fallbacks: str | list[str] | None = profile.options.get("fallbacks", "default")
        self._thinking_display: str | None = profile.options.get("thinking_display")
        self._prompt_cache: bool = profile.options.get("prompt_cache", True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        params = self._params(request)
        if self._fallbacks:
            manager: Any = self._client.beta.messages.stream(
                **params,
                fallbacks="default"
                if self._fallbacks == "default"
                else [{"model": m} for m in self._fallbacks],
                betas=[
                    FALLBACK_DEFAULT_BETA if self._fallbacks == "default" else FALLBACK_LIST_BETA
                ],
            )
        else:
            manager = self._client.messages.stream(**params)

        with self._translate_errors():
            async with manager as stream:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    if event.delta.type == "text_delta":
                        yield TextDelta(text=event.delta.text)
                    elif event.delta.type == "thinking_delta" and event.delta.thinking:
                        yield ThinkingDelta(text=event.delta.thinking)
                final = await stream.get_final_message()
        yield self._to_response(final)

    async def count_tokens(self, request: ModelRequest) -> int:
        params = self._params(request)
        allowed = ("model", "messages", "system", "tools", "thinking", "output_config")
        with self._translate_errors():
            result = await self._client.messages.count_tokens(
                **{k: v for k, v in params.items() if k in allowed}
            )
        return result.input_tokens

    def _params(self, request: ModelRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.profile.model,
            "max_tokens": request.max_output_tokens
            or self.profile.max_output_tokens
            or DEFAULT_MAX_TOKENS,
            "messages": to_anthropic_messages(request.messages),
        }
        if request.system:
            params["system"] = request.system
        if request.tools:
            # Eager streaming sends tool input as it is generated; the runtime validates
            # every input against the tool's schema before running it.
            params["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "eager_input_streaming": True,
                }
                for t in request.tools
            ]
        if request.effort:
            params["output_config"] = {"effort": request.effort}
        if self._thinking_display:
            params["thinking"] = {"type": "adaptive", "display": self._thinking_display}
        if self._prompt_cache:
            params["cache_control"] = {"type": "ephemeral"}
        if self.profile.temperature is not None:
            params["extra_body"] = {"temperature": self.profile.temperature}
        return params

    def _to_response(self, final: Any) -> ModelResponse:
        parts: list[Any] = []
        for block in final.content:
            if block.type == "text" and block.text:
                parts.append(TextPart(text=block.text))
            elif block.type == "thinking":
                parts.append(ThinkingPart(text=block.thinking or ""))
            elif block.type == "tool_use":
                valid = isinstance(block.input, dict)
                parts.append(
                    ToolCallPart(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if valid else {},
                        parse_error=None if valid else "tool input was not a JSON object",
                    )
                )
        usage = final.usage
        return ModelResponse(
            message=Message(
                role="assistant",
                parts=parts,
                native=NativeContent(
                    adapter=self.name,
                    model=final.model,
                    content=[block.to_dict() for block in final.content],
                ),
            ),
            stop_reason=_STOP.get(final.stop_reason or "", "other"),
            usage=Usage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ),
            model=final.model,
        )

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
            raise ModelError(
                f"connection failed: {exc}", retryable=True, adapter=self.name
            ) from exc
        except ValueError as exc:
            # The SDK raises ValueError for tool input JSON it cannot parse at all.
            raise ModelError(
                f"model sent unparseable tool input: {exc}", retryable=True, adapter=self.name
            ) from exc
        except TypeError as exc:
            if "authentication" not in str(exc):
                raise
            raise ConfigError(
                "no Anthropic credentials found: set ANTHROPIC_API_KEY (for example in .env), "
                "set api_key: ${MY_KEY_VAR} on the profile, or log in with `ant auth login`"
            ) from None


def to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "assistant" and message.native and message.native.adapter == "anthropic":
            out.append({"role": "assistant", "content": message.native.content})
            continue
        blocks: list[dict[str, Any]] = []
        for part in message.parts:
            if isinstance(part, TextPart) and part.text:
                blocks.append({"type": "text", "text": part.text})
            elif isinstance(part, ToolCallPart):
                blocks.append(
                    {"type": "tool_use", "id": part.id, "name": part.name, "input": part.arguments}
                )
            elif isinstance(part, ToolResultPart):
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": part.call_id,
                        "content": part.content or "(no output)",
                        "is_error": part.is_error,
                    }
                )
            # A ThinkingPart from another provider has no signature and cannot be replayed.
        # Tool results must lead the user turn that answers a tool call.
        blocks.sort(key=lambda b: b["type"] != "tool_result")
        if blocks:
            out.append({"role": message.role, "content": blocks})
    return out
