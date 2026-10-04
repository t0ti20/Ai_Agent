"""Structured event stream (FR-02), consumed by the CLI today and the API/GUI later."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from agent_base.core.messages import ToolCallPart, ToolResultPart


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


StopReason = Literal["completed", "max_tokens", "refused", "budget_exceeded", "error"]


class RunResult(BaseModel):
    output: str
    stop_reason: StopReason
    detail: str = ""
    steps: int
    usage: Usage
    session_id: str


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ThinkingDelta(BaseModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    text: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    call: ToolCallPart


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    result: ToolResultPart
    duration_ms: int


class StateEvent(BaseModel):
    type: Literal["state"] = "state"
    state: Literal["model_call", "tool_calls", "awaiting_approval", "fallback"]
    step: int
    detail: str = ""


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


class FinalEvent(BaseModel):
    type: Literal["final"] = "final"
    result: RunResult


Event = Annotated[
    TextDelta
    | ThinkingDelta
    | ToolCallEvent
    | ToolResultEvent
    | StateEvent
    | ErrorEvent
    | FinalEvent,
    Field(discriminator="type"),
]
