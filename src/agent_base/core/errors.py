"""Exception hierarchy shared by every layer."""

from __future__ import annotations


class AgentBaseError(Exception):
    """Base class for all agent-base errors."""


class ConfigError(AgentBaseError):
    """Invalid or unresolvable configuration."""


class ModelError(AgentBaseError):
    """A model adapter call failed.

    `retryable` is True for rate limits, timeouts, connection errors and 5xx
    responses; the runtime may then try the next profile in the fallback chain.
    """

    def __init__(self, message: str, *, retryable: bool = False, adapter: str = "") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.adapter = adapter


class BudgetExceeded(AgentBaseError):
    """A per-run budget (steps, tokens, cost, wall clock) ran out."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class PolicyDenied(AgentBaseError):
    """A tool call was blocked by policy, an approver, or a hook."""


class ToolDefinitionError(AgentBaseError):
    """A function could not be turned into a tool."""


class SkillError(AgentBaseError):
    """A skill folder does not follow the Agent Skills format."""
