"""Loop strategies: how an agent turns a prompt into model calls and tool calls.

`ReActLoop` (the default) repeats: call the model, run the tools it asked for,
append the results, until the model answers without tools or a budget runs
out. `SingleShotLoop` makes one model call without tools.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_base.adapters.llm.base import ModelRequest, ModelResponse, StreamChunk
from agent_base.capabilities.tools.decorator import Tool
from agent_base.core.budgets import BudgetTracker
from agent_base.core.errors import BudgetExceeded, ModelError, PolicyDenied
from agent_base.core.events import (
    ErrorEvent,
    Event,
    FinalEvent,
    RunResult,
    StateEvent,
    StopReason,
    ToolCallEvent,
    ToolResultEvent,
)
from agent_base.core.messages import Message, ToolCallPart, ToolResultPart
from agent_base.core.session import Session

if TYPE_CHECKING:
    from agent_base.core.agent import BaseAgent

log = logging.getLogger(__name__)


@dataclass
class RunContext:
    """Per-run state handed to every hook."""

    run_id: str
    agent: BaseAgent
    session: Session
    budget: BudgetTracker

    @property
    def step(self) -> int:
        return self.budget.steps


class LoopStrategy(ABC):
    @abstractmethod
    def run(self, agent: BaseAgent, prompt: str, session: Session) -> AsyncIterator[Event]:
        """Yield events for one run, ending with exactly one FinalEvent."""


class ReActLoop(LoopStrategy):
    use_tools = True

    async def run(self, agent: BaseAgent, prompt: str, session: Session) -> AsyncIterator[Event]:
        budget = BudgetTracker(agent.config.agent.budgets)
        ctx = RunContext(run_id=uuid.uuid4().hex, agent=agent, session=session, budget=budget)
        session.close_dangling_tool_calls()
        session.append(Message.user(prompt))
        output = ""
        stop: StopReason = "completed"
        detail = ""
        try:
            await agent.on_start(ctx)
            while True:
                budget.check()
                request = ModelRequest(
                    system=agent.system_prompt(),
                    messages=list(session.messages),
                    tools=agent.tool_registry.specs() if self.use_tools else [],
                    effort=agent.config.model.effort,
                )
                request = await agent.on_before_model(ctx, request) or request
                yield StateEvent(state="model_call", step=budget.steps + 1)

                response: ModelResponse | None = None
                async for chunk in self._call_model(agent, request, budget):
                    if isinstance(chunk, ModelResponse):
                        response = chunk
                    else:
                        yield chunk
                assert response is not None
                budget.steps += 1
                budget.record(response.usage)
                await agent.on_after_model(ctx, response)

                if response.stop_reason == "refusal":
                    # A declined turn is not added to history; the partial text is discarded.
                    stop, detail = "refused", "the model declined this request"
                    break
                session.append(response.message)
                output = response.message.text or output
                if response.stop_reason == "pause":
                    continue
                calls = response.message.tool_calls if self.use_tools else []
                if not calls:
                    if response.stop_reason == "max_tokens":
                        stop, detail = "max_tokens", "the answer was cut off at the output limit"
                    break
                if response.stop_reason == "max_tokens":
                    # Tool input may be truncated; answer the calls without running them.
                    session.append(_results(calls, "not run: output limit reached mid-call"))
                    stop, detail = "max_tokens", "a tool call was cut off at the output limit"
                    break

                yield StateEvent(state="tool_calls", step=budget.steps, detail=str(len(calls)))
                results: list[ToolResultPart] = []
                async for event in self._run_tools(agent, ctx, calls, results):
                    yield event
                session.append(Message(role="user", parts=list(results)))
        except BudgetExceeded as exc:
            stop, detail = "budget_exceeded", str(exc)
        except Exception as exc:
            await agent.on_error(ctx, exc)
            yield ErrorEvent(message=f"{type(exc).__name__}: {exc}")
            raise

        result = RunResult(
            output=output,
            stop_reason=stop,
            detail=detail,
            steps=budget.steps,
            usage=budget.usage,
            session_id=session.id,
        )
        await agent.on_finish(ctx, result)
        yield FinalEvent(result=result)

    async def _call_model(
        self, agent: BaseAgent, request: ModelRequest, budget: BudgetTracker
    ) -> AsyncIterator[StreamChunk | StateEvent]:
        """Stream one model call, trying fallback profiles on retryable errors (AD-06)."""
        profiles = [agent.config.model.profile, *agent.config.model.fallback]
        for index, name in enumerate(profiles):
            adapter = agent.adapter(name)
            started = False
            try:
                async for chunk in _within_budget(adapter.stream(request), budget):
                    if isinstance(chunk, ModelResponse):
                        chunk.usage.cost_usd = adapter.cost(chunk.usage)
                    started = True
                    yield chunk
                return
            except ModelError as exc:
                # Once text has streamed, switching models would repeat it; fail instead.
                if started or not exc.retryable or index == len(profiles) - 1:
                    raise
                log.warning(
                    "profile %s failed (%s); falling back to %s", name, exc, profiles[index + 1]
                )
                yield StateEvent(
                    state="fallback",
                    step=budget.steps + 1,
                    detail=f"{name} failed: {exc}; trying {profiles[index + 1]}",
                )

    async def _run_tools(
        self,
        agent: BaseAgent,
        ctx: RunContext,
        calls: list[ToolCallPart],
        results: list[ToolResultPart],
    ) -> AsyncIterator[Event]:
        """Approve calls one by one, run the approved ones concurrently (FR-13)."""
        done: dict[str, ToolResultPart] = {}
        approved: list[tuple[ToolCallPart, Tool]] = []
        for call in calls:
            yield ToolCallEvent(call=call)
            tool = agent.tool_registry.get(call.name)
            if call.parse_error:
                done[call.id] = _error(
                    call.id, f"arguments were not valid JSON ({call.parse_error})"
                )
                continue
            if tool is None:
                names = ", ".join(sorted(t.name for t in agent.tool_registry))
                done[call.id] = _error(call.id, f"unknown tool '{call.name}'; available: {names}")
                continue
            try:
                changed = await agent.on_before_tool(ctx, call) or call
                if agent.approvals.decide(tool) == "ask":
                    yield StateEvent(state="awaiting_approval", step=ctx.step, detail=call.name)
                if not await agent.approvals.approve(changed, tool):
                    raise PolicyDenied(f"{call.name} needs approval and was not approved")
            except PolicyDenied as exc:
                done[call.id] = _error(call.id, f"denied: {exc}")
                continue
            approved.append((changed.model_copy(update={"id": call.id}), tool))

        default_timeout = agent.config.tools.timeout_s

        async def execute(call: ToolCallPart, tool: Tool) -> tuple[ToolResultPart, int]:
            started = time.monotonic()
            timeout = tool.timeout_s or default_timeout
            remaining = ctx.budget.remaining_seconds()
            if remaining is not None:
                timeout = max(0.0, min(timeout, remaining))
            try:
                async with asyncio.timeout(timeout):
                    content = await tool.invoke(call.arguments)
                result = ToolResultPart(call_id=call.id, content=content)
            except TimeoutError:
                result = _error(call.id, f"timed out after {timeout:.0f}s")
            except Exception as exc:
                result = _error(call.id, f"{type(exc).__name__}: {exc}")
            result = await agent.on_after_tool(ctx, call, result) or result
            return result, int((time.monotonic() - started) * 1000)

        outcomes = await asyncio.gather(*(execute(c, t) for c, t in approved))
        durations: dict[str, int] = {}
        for (call, _), (result, ms) in zip(approved, outcomes, strict=True):
            done[call.id] = result
            durations[call.id] = ms
        for call in calls:
            result = done[call.id]
            results.append(result)
            yield ToolResultEvent(result=result, duration_ms=durations.get(call.id, 0))


class SingleShotLoop(ReActLoop):
    use_tools = False


LOOPS: dict[str, type[LoopStrategy]] = {"react": ReActLoop, "single_shot": SingleShotLoop}


async def _within_budget(
    stream: AsyncIterator[StreamChunk], budget: BudgetTracker
) -> AsyncIterator[StreamChunk]:
    """Bound each wait for the next chunk by the run's remaining wall-clock budget."""
    iterator = aiter(stream)
    while True:
        try:
            async with asyncio.timeout(budget.remaining_seconds()):
                chunk = await anext(iterator)
        except StopAsyncIteration:
            return
        except TimeoutError:
            aclose = getattr(iterator, "aclose", None)
            if aclose is not None:
                await aclose()
            raise BudgetExceeded(
                "timeout_s", f"ran longer than timeout_s ({budget.budgets.timeout_s}s)"
            ) from None
        yield chunk


def _error(call_id: str, message: str) -> ToolResultPart:
    return ToolResultPart(call_id=call_id, content=message, is_error=True)


def _results(calls: list[ToolCallPart], message: str) -> Message:
    return Message(role="user", parts=[_error(c.id, message) for c in calls])
