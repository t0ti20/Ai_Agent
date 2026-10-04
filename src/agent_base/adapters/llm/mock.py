"""Scripted model for tests and offline development (FR-25).

Each scripted turn is either a string (a final text answer) or a mapping:

    {"text": "Let me check.", "tool_calls": [{"name": "word_count", "arguments": {...}}]}

When the script runs out, the mock echoes the latest user text, so an agent
with a mock profile always runs. Turns can come from code or from YAML:

    profiles:
      mock: {adapter: mock, model: mock, options: {responses: ["Hello!"]}}
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
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
from agent_base.core.events import TextDelta, Usage
from agent_base.core.messages import Message, TextPart, ToolCallPart

ScriptTurn = str | dict[str, Any]


class MockAdapter(ModelAdapter):
    name = "mock"
    default_capabilities: ClassVar[Capabilities] = Capabilities(
        tool_calling=True, parallel_tools=True, streaming=True, max_context=1_000_000
    )

    def __init__(
        self, profile: ModelProfile, responses: Sequence[ScriptTurn] | None = None
    ) -> None:
        super().__init__(profile)
        self.script: list[ScriptTurn] = list(
            responses if responses is not None else profile.options.get("responses", [])
        )
        self.requests: list[ModelRequest] = []
        """Every request received, for assertions in tests."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        scripted = self.script.pop(0) if self.script else f"mock: {_last_user_text(request)}"
        turn: dict[str, Any] = {"text": scripted} if isinstance(scripted, str) else scripted
        text = str(turn.get("text", ""))
        for i, word in enumerate(text.split(" ") if text else []):
            yield TextDelta(text=word if i == 0 else " " + word)
        parts: list[Any] = [TextPart(text=text)] if text else []
        calls = [
            ToolCallPart(
                id=call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                name=call["name"],
                arguments=call.get("arguments", {}),
            )
            for call in turn.get("tool_calls", [])
        ]
        parts.extend(calls)
        default_stop: ModelStopReason = "tool_use" if calls else "end_turn"
        yield ModelResponse(
            message=Message(role="assistant", parts=parts),
            stop_reason=turn.get("stop_reason", default_stop),
            usage=Usage(input_tokens=_estimate(request), output_tokens=max(1, len(text) // 4)),
            model=self.profile.model,
        )


def _last_user_text(request: ModelRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user" and message.text:
            return message.text
    return ""


def _estimate(request: ModelRequest) -> int:
    return (len(request.system) + sum(len(m.text) for m in request.messages)) // 4 + 1
