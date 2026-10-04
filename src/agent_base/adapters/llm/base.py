"""The model adapter interface (AD-01) and capability flags (AD-03).

An adapter turns a provider-neutral `ModelRequest` into one provider call and
streams back `TextDelta` / `ThinkingDelta` chunks, ending with exactly one
`ModelResponse`. Adding a provider means writing one subclass and registering
it in `agent_base.adapters.registry`; the runtime never changes.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from pydantic import BaseModel

from agent_base.capabilities.tools.decorator import ToolSpec
from agent_base.config.models import Effort, ModelProfile
from agent_base.core.events import TextDelta, ThinkingDelta, Usage
from agent_base.core.messages import Message


class Capabilities(BaseModel):
    tool_calling: bool = True
    parallel_tools: bool = False
    vision: bool = False
    json_mode: bool = False
    streaming: bool = True
    thinking: bool = False
    max_context: int | None = None
    max_output: int | None = None


@dataclass
class ModelRequest:
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    effort: Effort | None = None
    max_output_tokens: int | None = None


ModelStopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "pause", "other"]


class ModelResponse(BaseModel):
    message: Message
    stop_reason: ModelStopReason
    usage: Usage
    model: str


StreamChunk = TextDelta | ThinkingDelta | ModelResponse


class ModelAdapter(ABC):
    name: ClassVar[str]
    default_capabilities: ClassVar[Capabilities] = Capabilities()

    def __init__(self, profile: ModelProfile) -> None:
        self.profile = profile

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        """Yield deltas as they arrive, then one final `ModelResponse`."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        response: ModelResponse | None = None
        async for chunk in self.stream(request):
            if isinstance(chunk, ModelResponse):
                response = chunk
        if response is None:
            raise RuntimeError(f"{self.name} adapter finished without a response")
        return response

    async def count_tokens(self, request: ModelRequest) -> int:
        """Rough estimate (about 4 characters per token); adapters override with real counts."""
        size = len(request.system) + sum(len(m.model_dump_json()) for m in request.messages)
        size += sum(len(json.dumps(t.input_schema)) + len(t.description) for t in request.tools)
        return size // 4

    def capabilities(self) -> Capabilities:
        merged = self.default_capabilities.model_dump() | self.profile.capabilities
        return Capabilities.model_validate(merged)

    def cost(self, usage: Usage) -> float:
        price = self.profile.price
        if price is None:
            return 0.0
        cache_read = price.cache_read if price.cache_read is not None else price.input
        cache_write = price.cache_write if price.cache_write is not None else price.input
        return (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_read_tokens * cache_read
            + usage.cache_write_tokens * cache_write
        ) / 1_000_000
