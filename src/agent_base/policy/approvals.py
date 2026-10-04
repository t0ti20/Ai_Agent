"""Approval gate for tool calls, enforced in code, never by prompt (SEC-01, SEC-02).

Phase 1 covers the default-ask rule: tools whose risk level is listed in
`tools.ask_for_risks` (write, destructive, external by default), or that are
named in `tools.require_approval`, need an approver's yes before they run.
`tools.auto_approve` exempts tools by name or glob. The full policy engine
(paths, hosts, shell commands, audit log) is Phase 2.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from agent_base.capabilities.tools.decorator import Tool
from agent_base.capabilities.tools.registry import matches
from agent_base.config.models import ToolsConfig
from agent_base.core.messages import ToolCallPart

Approver = Callable[[ToolCallPart, Tool], Awaitable[bool]]
Decision = Literal["allow", "ask"]


async def deny_all(call: ToolCallPart, tool: Tool) -> bool:
    """The default approver: with nobody to ask, risky calls do not run."""
    return False


async def approve_all(call: ToolCallPart, tool: Tool) -> bool:
    return True


class ApprovalPolicy:
    def __init__(self, config: ToolsConfig, approver: Approver | None = None) -> None:
        self.config = config
        self.approver = approver or deny_all

    def decide(self, tool: Tool) -> Decision:
        if matches(tool, self.config.require_approval):
            return "ask"
        if matches(tool, self.config.auto_approve):
            return "allow"
        return "ask" if tool.risk in self.config.ask_for_risks else "allow"

    async def approve(self, call: ToolCallPart, tool: Tool) -> bool:
        return self.decide(tool) == "allow" or await self.approver(call, tool)
