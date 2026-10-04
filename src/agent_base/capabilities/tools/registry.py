"""Tool registry with namespaces and per-agent enable/disable (FR-11)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from fnmatch import fnmatchcase

from agent_base.capabilities.tools.decorator import Tool, ToolSpec
from agent_base.core.errors import ToolDefinitionError


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.register(t)

    def register(self, tool: Tool) -> None:
        existing = self._tools.get(tool.name)
        if existing is not None and existing is not tool:
            raise ToolDefinitionError(
                f"two tools are named '{tool.name}' ({existing.qualified_name} and "
                f"{tool.qualified_name}); rename one with @tool(name=...)"
            )
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def specs(self) -> list[ToolSpec]:
        """Specs in a stable order, so the request prefix (and the prompt cache) stays stable."""
        return [self._tools[name].spec for name in sorted(self._tools)]

    def filtered(self, enabled: list[str] | None, disabled: list[str]) -> ToolRegistry:
        """A registry with only the tools that `enabled` matches and `disabled` does not.

        Patterns match a tool's plain name (`word_count`) or its qualified name
        (`text.word_count`), and may use globs (`text.*`).
        """
        return ToolRegistry(
            t
            for t in self._tools.values()
            if (enabled is None or matches(t, enabled)) and not matches(t, disabled)
        )


def matches(tool: Tool, patterns: Iterable[str]) -> bool:
    return any(fnmatchcase(tool.name, p) or fnmatchcase(tool.qualified_name, p) for p in patterns)
