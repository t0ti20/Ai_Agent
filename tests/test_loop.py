from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from agent_base import (
    BaseAgent,
    Message,
    ModelError,
    ModelProfile,
    PolicyDenied,
    RunContext,
    Session,
    tool,
)
from agent_base.adapters.llm.base import ModelRequest, StreamChunk
from agent_base.adapters.llm.mock import MockAdapter
from agent_base.core.events import FinalEvent, StateEvent, ToolResultEvent
from agent_base.core.messages import ToolCallPart, ToolResultPart
from agent_base.policy.approvals import approve_all

AgentFactory = Callable[..., tuple[BaseAgent, MockAdapter]]


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@tool(risk="write")
def save_note(text: str) -> str:
    """Save a note."""
    return "saved"


@tool(timeout_s=0.05)
async def slow() -> str:
    """Take too long."""
    await asyncio.sleep(1)
    return "late"


def call(name: str, **arguments: object) -> dict[str, object]:
    return {"tool_calls": [{"name": name, "arguments": arguments}]}


async def test_react_loop_runs_tools_until_a_final_answer(make_agent: AgentFactory) -> None:
    agent, model = make_agent([call("add", a=2, b=3), "The sum is 5."], [add])
    result = await agent.run("What is 2 + 3?")

    assert (result.output, result.stop_reason, result.steps) == ("The sum is 5.", "completed", 2)
    second = model.requests[1]
    assert [m.role for m in second.messages] == ["user", "assistant", "user"]
    assert second.messages[-1].tool_results[0].content == "5"
    assert [t.name for t in second.tools] == ["add"]
    assert result.usage.input_tokens > 0


async def test_parallel_calls_are_answered_in_one_message(make_agent: AgentFactory) -> None:
    turn = {
        "tool_calls": [
            {"name": "add", "arguments": {"a": 1, "b": 1}},
            {"name": "add", "arguments": {"a": 2, "b": 2}},
        ]
    }
    agent, model = make_agent([turn, "2 and 4"], [add])
    await agent.run("two sums")
    results = model.requests[1].messages[-1].tool_results
    assert [r.content for r in results] == ["2", "4"]


async def test_tool_problems_go_back_to_the_model_as_errors(make_agent: AgentFactory) -> None:
    agent, model = make_agent(
        [
            {
                "tool_calls": [
                    {"name": "nope", "arguments": {}},
                    {"name": "add", "arguments": {"a": "x", "b": 1}},
                    {"name": "slow", "arguments": {}},
                ]
            },
            "Sorry.",
        ],
        [add, slow],
    )
    events = [e async for e in agent.stream("break things")]
    results = [e.result for e in events if isinstance(e, ToolResultEvent)]
    assert all(r.is_error for r in results)
    assert "unknown tool 'nope'; available: add, slow" in results[0].content
    assert "invalid arguments for add" in results[1].content
    assert "timed out" in results[2].content
    assert isinstance(events[-1], FinalEvent) and events[-1].result.output == "Sorry."
    assert len(model.requests) == 2


async def test_risky_tools_need_approval(make_agent: AgentFactory) -> None:
    agent, _ = make_agent([call("save_note", text="hi"), "ok"], [save_note])
    events = [e async for e in agent.stream("save it")]
    result = next(e.result for e in events if isinstance(e, ToolResultEvent))
    assert result.is_error and result.content.startswith("denied:")
    assert any(isinstance(e, StateEvent) and e.state == "awaiting_approval" for e in events)

    agent, _ = make_agent([call("save_note", text="hi"), "ok"], [save_note], approver=approve_all)
    events = [e async for e in agent.stream("save it")]
    result = next(e.result for e in events if isinstance(e, ToolResultEvent))
    assert result.content == "saved"

    agent, _ = make_agent(
        [call("save_note", text="hi"), "ok"],
        [save_note],
        config={"tools": {"auto_approve": ["save_*"]}},
    )
    events = [e async for e in agent.stream("save it")]
    assert next(e.result for e in events if isinstance(e, ToolResultEvent)).content == "saved"


async def test_hooks_can_block_rewrite_and_observe(make_agent: AgentFactory) -> None:
    seen: list[str] = []

    class Hooked(BaseAgent):
        async def on_start(self, ctx: RunContext) -> None:
            seen.append("start")

        async def on_before_tool(self, ctx: RunContext, call: ToolCallPart) -> ToolCallPart | None:
            if call.arguments.get("a") == 13:
                raise PolicyDenied("unlucky")
            return call.model_copy(update={"arguments": {"a": 40, "b": 2}})

        async def on_after_tool(
            self, ctx: RunContext, call: ToolCallPart, result: ToolResultPart
        ) -> ToolResultPart | None:
            return result.model_copy(update={"content": f"result={result.content}"})

        async def on_finish(self, ctx: RunContext, result: object) -> None:
            seen.append("finish")

    agent, model = make_agent(
        [call("add", a=1, b=1), call("add", a=13, b=0), "done"], [add], base=Hooked
    )
    await agent.run("go")
    assert model.requests[1].messages[-1].tool_results[0].content == "result=42"
    assert model.requests[2].messages[-1].tool_results[0].content == "denied: unlucky"
    assert seen == ["start", "finish"]


async def test_budget_stops_gracefully_with_partial_output(make_agent: AgentFactory) -> None:
    looping = [{"text": f"step {i}", **call("add", a=i, b=i)} for i in range(10)]
    agent, _ = make_agent(looping, [add], config={"agent": {"budgets": {"max_steps": 3}}})
    result = await agent.run("loop forever")
    assert result.stop_reason == "budget_exceeded"
    assert result.steps == 3
    assert result.output == "step 2"
    assert "max_steps" in result.detail


async def test_refusal_ends_the_run_without_touching_history(make_agent: AgentFactory) -> None:
    agent, _ = make_agent([{"text": "partial", "stop_reason": "refusal"}])
    session = agent.new_session()
    result = await agent.run("something declined", session=session)
    assert result.stop_reason == "refused"
    assert [m.role for m in session.messages] == ["user"]


async def test_truncated_tool_call_is_answered_not_run(make_agent: AgentFactory) -> None:
    agent, _ = make_agent(
        [{**call("save_note", text="cut"), "stop_reason": "max_tokens"}], [save_note]
    )
    session = agent.new_session()
    result = await agent.run("write", session=session)
    assert result.stop_reason == "max_tokens"
    assert session.messages[-1].tool_results[0].content.startswith("not run")


async def test_conversation_continues_in_a_session(
    make_agent: AgentFactory, tmp_path: Path
) -> None:
    agent, model = make_agent(["Hi Khaled.", "You said your name is Khaled."])
    session = agent.new_session()
    await agent.run("My name is Khaled.", session=session)
    saved = Session.load(session.save(tmp_path))
    await agent.run("What is my name?", session=saved)
    assert [m.text for m in model.requests[1].messages] == [
        "My name is Khaled.",
        "Hi Khaled.",
        "What is my name?",
    ]


async def test_dangling_tool_calls_are_closed_on_resume(make_agent: AgentFactory) -> None:
    agent, model = make_agent(["ok"])
    session = agent.new_session()
    session.append(
        Message(role="assistant", parts=[ToolCallPart(id="call_1", name="add", arguments={})])
    )
    await agent.run("continue", session=session)
    roles_and_results = [(m.role, len(m.tool_results)) for m in model.requests[0].messages]
    assert roles_and_results == [("assistant", 0), ("user", 1), ("user", 0)]


class FlakyAdapter(MockAdapter):
    name = "flaky"

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        raise ModelError("overloaded", retryable=True, adapter="flaky")
        yield  # pragma: no cover


async def test_fallback_profile_takes_over_on_retryable_errors(make_agent: AgentFactory) -> None:
    agent, _ = make_agent(
        config={
            "profiles": {
                "mock": {"adapter": "mock", "model": "mock"},
                "backup": {
                    "adapter": "mock",
                    "model": "backup",
                    "options": {"responses": ["from backup"]},
                },
            },
            "model": {"profile": "mock", "fallback": ["backup"]},
        }
    )
    agent._adapters["mock"] = FlakyAdapter(ModelProfile(adapter="mock", model="flaky"))  # pyright: ignore[reportPrivateUsage]
    events = [e async for e in agent.stream("hello")]
    assert any(isinstance(e, StateEvent) and e.state == "fallback" for e in events)
    final = events[-1]
    assert isinstance(final, FinalEvent) and final.result.output == "from backup"


async def test_single_shot_loop_sends_no_tools(make_agent: AgentFactory) -> None:
    agent, model = make_agent(["just text"], [add], config={"agent": {"loop": "single_shot"}})
    result = await agent.run("hi")
    assert result.output == "just text"
    assert model.requests[0].tools == []


async def test_cost_comes_from_the_profile_price(make_agent: AgentFactory) -> None:
    agent, _ = make_agent(
        ["x" * 400],
        config={
            "profiles": {
                "mock": {
                    "adapter": "mock",
                    "model": "mock",
                    "price": {"input": 1.0, "output": 10.0},
                }
            }
        },
    )
    agent._adapters.clear()  # pyright: ignore[reportPrivateUsage]
    agent._adapters["mock"] = MockAdapter(agent.config.profiles["mock"], ["x" * 400])  # pyright: ignore[reportPrivateUsage]
    result = await agent.run("hi")
    expected = (result.usage.input_tokens * 1.0 + result.usage.output_tokens * 10.0) / 1_000_000
    assert result.usage.cost_usd == expected > 0
