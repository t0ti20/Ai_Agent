"""OpenAI's own API, through Chat Completions.

Same wire format as `openai_compat`, with OpenAI's defaults: the official base
URL, a real API key (OPENAI_API_KEY unless the profile sets `api_key`), and
`max_completion_tokens`. Set `capabilities.thinking: true` on reasoning models
so the effort knob is sent as `reasoning_effort`.
"""

from __future__ import annotations

from typing import ClassVar

from agent_base.adapters.llm.base import Capabilities
from agent_base.adapters.llm.openai_compat import OpenAICompatAdapter


class OpenAIAdapter(OpenAICompatAdapter):
    name = "openai"
    default_capabilities: ClassVar[Capabilities] = Capabilities(
        tool_calling=True, parallel_tools=True, vision=True, json_mode=True, streaming=True
    )
    max_tokens_param: ClassVar[str] = "max_completion_tokens"

    def _api_key(self) -> str | None:
        return self.profile.api_key  # None lets the SDK read OPENAI_API_KEY

    def _base_url(self) -> str | None:
        return self.profile.base_url
