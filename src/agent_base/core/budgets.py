"""Per-run budget tracking (FR-04)."""

from __future__ import annotations

import time

from agent_base.config.models import Budgets
from agent_base.core.errors import BudgetExceeded
from agent_base.core.events import Usage


class BudgetTracker:
    def __init__(self, budgets: Budgets) -> None:
        self.budgets = budgets
        self.started = time.monotonic()
        self.steps = 0
        self.usage = Usage()

    def remaining_seconds(self) -> float | None:
        if self.budgets.timeout_s is None:
            return None
        return self.budgets.timeout_s - (time.monotonic() - self.started)

    def check(self) -> None:
        """Raise BudgetExceeded if another model step would break a limit."""
        b = self.budgets
        if self.steps >= b.max_steps:
            raise BudgetExceeded("max_steps", f"reached max_steps ({b.max_steps})")
        if b.max_tokens is not None and self.usage.total_tokens >= b.max_tokens:
            raise BudgetExceeded(
                "max_tokens", f"used {self.usage.total_tokens} of max_tokens ({b.max_tokens})"
            )
        if b.max_cost_usd is not None and self.usage.cost_usd >= b.max_cost_usd:
            raise BudgetExceeded(
                "max_cost_usd",
                f"spent ${self.usage.cost_usd:.4f} of max_cost_usd (${b.max_cost_usd})",
            )
        remaining = self.remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise BudgetExceeded("timeout_s", f"ran longer than timeout_s ({b.timeout_s}s)")

    def record(self, usage: Usage) -> None:
        self.usage = self.usage + usage
