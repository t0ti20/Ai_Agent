"""Offline tests for the hello agent, using the scripted mock model."""

from pathlib import Path

from agent_base.testing import load_test_agent

AGENT_DIR = Path(__file__).resolve().parents[1]


async def test_counts_words_with_its_tool() -> None:
    agent, model = load_test_agent(
        AGENT_DIR,
        responses=[
            {"tool_calls": [{"name": "word_count", "arguments": {"text": "one two three"}}]},
            "That text has 3 words.",
        ],
    )
    result = await agent.run("How many words are in 'one two three'?")

    assert result.stop_reason == "completed"
    assert result.output == "That text has 3 words."
    tool_result = model.requests[1].messages[-1].tool_results[0]
    assert '"words": 3' in tool_result.content


async def test_system_prompt_lists_its_skills() -> None:
    agent, _ = load_test_agent(AGENT_DIR)
    prompt = agent.system_prompt()

    assert "hello-agent" in prompt
    assert "**greeting**" in prompt
    assert "**plain-language**" in prompt


async def test_greeting_skill_loads_on_demand() -> None:
    agent, model = load_test_agent(
        AGENT_DIR,
        responses=[
            {"tool_calls": [{"name": "load_skill", "arguments": {"name": "greeting"}}]},
            {
                "tool_calls": [
                    {
                        "name": "read_skill_file",
                        "arguments": {"skill": "greeting", "path": "references/greetings.md"},
                    }
                ]
            },
            "Merhaba!",
        ],
    )
    result = await agent.run("Say hello in Turkish")

    assert result.output == "Merhaba!"
    skill_body = model.requests[1].messages[-1].tool_results[0].content
    assert "references/greetings.md" in skill_body
    reference = model.requests[2].messages[-1].tool_results[0].content
    assert "Merhaba" in reference
