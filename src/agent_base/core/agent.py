"""BaseAgent: the class every agent inherits from (FR-01).

A new agent is a subclass plus an `agent.yaml` next to it:

    class SupportAgent(BaseAgent):
        skills = ["triage", "refunds"]          # resolved from skills/ folders
        tools = [lookup_order, create_ticket]   # @tool functions

        async def on_before_tool(self, ctx, call):
            ...

Every lifecycle hook is optional. The loop strategy can be replaced through
config (`agent.loop`) or in code (`loop_strategy = MyLoop`).
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

from agent_base.adapters.llm.base import ModelAdapter, ModelRequest, ModelResponse
from agent_base.adapters.registry import create_adapter
from agent_base.capabilities.skills.loader import SkillSet
from agent_base.capabilities.tools.decorator import Tool, as_tool
from agent_base.capabilities.tools.registry import ToolRegistry
from agent_base.config.loader import LoadedConfig, find_project_root, load_config
from agent_base.config.models import AgentConfig
from agent_base.core.events import Event, FinalEvent, RunResult
from agent_base.core.loop import LOOPS, LoopStrategy, RunContext
from agent_base.core.messages import ToolCallPart, ToolResultPart
from agent_base.core.prompts import render_prompt
from agent_base.core.session import Session
from agent_base.policy.approvals import ApprovalPolicy, Approver


class BaseAgent:
    config_file: ClassVar[str] = "agent.yaml"
    skills: ClassVar[list[str] | None] = None
    """Skill names to load. `skills.enabled` in config takes precedence; None loads all."""
    tools: ClassVar[Sequence[Tool | Callable[..., Any]]] = ()
    loop_strategy: ClassVar[type[LoopStrategy] | None] = None

    def __init__(
        self,
        *,
        agent_dir: Path | None = None,
        config: AgentConfig | LoadedConfig | None = None,
        env: str | None = None,
        overrides: dict[str, Any] | list[str] | None = None,
        root: Path | None = None,
        approver: Approver | None = None,
        adapter: ModelAdapter | None = None,
    ) -> None:
        self.agent_dir = (agent_dir or self._default_agent_dir()).resolve()
        if isinstance(config, LoadedConfig):
            self.loaded = config
        elif isinstance(config, AgentConfig):
            self.loaded = LoadedConfig(
                config=config,
                root=(root or find_project_root(self.agent_dir)).resolve(),
                env=env or "dev",
                agent_dir=self.agent_dir,
            )
        else:
            self.loaded = load_config(
                self.agent_dir,
                config_file=self.config_file,
                root=root,
                env=env,
                overrides=overrides,
            )
        self.config = self.loaded.config

        enabled = self.config.skills.enabled
        self.skill_set = SkillSet.discover(
            self._skill_paths(), enabled if enabled is not None else type(self).skills
        )
        own = ToolRegistry(as_tool(t) for t in type(self).tools)
        self.tool_registry = own.filtered(self.config.tools.enabled, self.config.tools.disabled)
        for skill_tool in self.skill_set.tools():
            self.tool_registry.register(skill_tool)

        self.approvals = ApprovalPolicy(self.config.tools, approver)
        self._adapters: dict[str, ModelAdapter] = {}
        if adapter is not None:
            self._adapters[self.config.model.profile] = adapter
        self._system_prompt: str | None = None

    @property
    def name(self) -> str:
        return self.config.agent.name

    def adapter(self, profile: str | None = None) -> ModelAdapter:
        """The adapter for a profile (default: `model.profile`), created on first use."""
        profile = profile or self.config.model.profile
        if profile not in self._adapters:
            self._adapters[profile] = create_adapter(profile, self.config.profiles[profile])
        return self._adapters[profile]

    def system_prompt(self) -> str:
        """Rendered once per agent instance, so every request carries the same prefix."""
        if self._system_prompt is None:
            section = self.config.agent
            self._system_prompt = render_prompt(
                section.system_prompt,
                [self.agent_dir / "prompts", self.loaded.root / "prompts"],
                {
                    "agent": section,
                    "instructions": section.instructions.strip(),
                    "skills": self.skill_set.catalog(),
                    **section.prompt_vars,
                },
                profile=self.config.model.profile,
            )
        return self._system_prompt

    def new_session(self) -> Session:
        return Session(agent=self.name)

    async def run(self, prompt: str, *, session: Session | None = None) -> RunResult:
        """Run to completion and return the result. Pass a session to continue a conversation."""
        result: RunResult | None = None
        async for event in self.stream(prompt, session=session):
            if isinstance(event, FinalEvent):
                result = event.result
        assert result is not None
        return result

    async def stream(self, prompt: str, *, session: Session | None = None) -> AsyncIterator[Event]:
        """Run and yield every event (text deltas, tool calls, results, final)."""
        strategy = self.loop_strategy or LOOPS[self.config.agent.loop]
        async for event in strategy().run(self, prompt, session or self.new_session()):
            yield event

    # Lifecycle hooks. Override any of them; the defaults do nothing. -----------------

    async def on_start(self, ctx: RunContext) -> None:
        """Called once per run, after the user prompt is added to the session."""

    async def on_before_model(self, ctx: RunContext, request: ModelRequest) -> ModelRequest | None:
        """Return a changed request, or None to keep it. Do not edit earlier messages."""
        return None

    async def on_after_model(self, ctx: RunContext, response: ModelResponse) -> None:
        """Called after every model response, before tools run."""

    async def on_before_tool(self, ctx: RunContext, call: ToolCallPart) -> ToolCallPart | None:
        """Return changed arguments, None to keep them, or raise PolicyDenied to block the call."""
        return None

    async def on_after_tool(
        self, ctx: RunContext, call: ToolCallPart, result: ToolResultPart
    ) -> ToolResultPart | None:
        """Return a replacement result, or None to keep it."""
        return None

    async def on_error(self, ctx: RunContext, error: BaseException) -> None:
        """Called when a run fails with an exception, before it propagates."""

    async def on_finish(self, ctx: RunContext, result: RunResult) -> None:
        """Called once per run with the final result."""

    # Internals ---------------------------------------------------------------------

    def _default_agent_dir(self) -> Path:
        cls = type(self)
        if cls is BaseAgent:
            return Path.cwd()
        return Path(inspect.getfile(cls)).resolve().parent

    def _skill_paths(self) -> list[Path]:
        cfg = self.config.skills
        paths = [self.loaded.root / p for p in cfg.paths]
        paths.append(self.agent_dir / "skills")
        if cfg.user_path:
            paths.append(Path(cfg.user_path).expanduser())
        unique: list[Path] = []
        for path in paths:
            if path.resolve() not in [p.resolve() for p in unique]:
                unique.append(path)
        return unique
