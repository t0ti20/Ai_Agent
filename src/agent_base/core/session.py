"""Conversation sessions with save, resume and fork (FR-03).

The message list is append-only on purpose: Claude binds thinking blocks to the
exact history that produced them, and an unchanged prefix is also what keeps
the prompt cache warm. Rewrite history only by forking.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_base.core.messages import Message, ToolResultPart


def _new_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


class Session(BaseModel):
    id: str = Field(default_factory=_new_id)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    agent: str = ""
    messages: list[Message] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def close_dangling_tool_calls(self) -> None:
        """Answer tool calls left without results (a crash or cancel mid-step).

        Every provider rejects a history whose last tool calls were never answered.
        """
        if not self.messages or self.messages[-1].role != "assistant":
            return
        calls = self.messages[-1].tool_calls
        if calls:
            self.append(
                Message(
                    role="user",
                    parts=[
                        ToolResultPart(
                            call_id=c.id, content="interrupted before running", is_error=True
                        )
                        for c in calls
                    ],
                )
            )

    def fork(self) -> Session:
        return Session(
            agent=self.agent,
            messages=[m.model_copy(deep=True) for m in self.messages],
            metadata={**self.metadata, "forked_from": self.id},
        )

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.id}.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> Session:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
