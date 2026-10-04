"""Provider-neutral message format (AD-02).

Every adapter converts to and from these types. Assistant messages also keep
the provider's own content blocks in `native`, so the same adapter can replay
them unchanged on the next request. That matters for Claude, where thinking
blocks must be passed back byte-for-byte and history must stay append-only.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ThinkingPart(BaseModel):
    type: Literal["thinking"] = "thinking"
    text: str = ""


class ToolCallPart(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    parse_error: str | None = None
    """Set when the model's arguments were not valid JSON; the call is not run."""


class ToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    content: str
    is_error: bool = False


Part = Annotated[
    TextPart | ThinkingPart | ToolCallPart | ToolResultPart, Field(discriminator="type")
]


class NativeContent(BaseModel):
    """The provider's own representation of a message, replayed as-is."""

    adapter: str
    model: str
    content: Any


class Message(BaseModel):
    role: Literal["user", "assistant"]
    parts: list[Part] = Field(default_factory=list)
    native: NativeContent | None = None

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(role="user", parts=[TextPart(text=text)])

    @classmethod
    def assistant(cls, text: str) -> Message:
        return cls(role="assistant", parts=[TextPart(text=text)])

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return [p for p in self.parts if isinstance(p, ToolCallPart)]

    @property
    def tool_results(self) -> list[ToolResultPart]:
        return [p for p in self.parts if isinstance(p, ToolResultPart)]
