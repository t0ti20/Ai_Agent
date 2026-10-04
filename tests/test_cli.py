from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_base.interfaces.cli.main import app
from agent_base.testing import load_test_agent

runner = CliRunner()


def test_run_on_the_mock_profile(project: Path) -> None:
    result = runner.invoke(app, ["run", "hello", "hi there", "--profile", "mock"])
    assert result.exit_code == 0, result.output
    assert "mock: hi there" in result.stdout
    assert len(list((project / ".agent-base/sessions/hello-agent").glob("*.json"))) == 1


def test_run_resumes_a_session_and_emits_json(project: Path) -> None:
    first = runner.invoke(app, ["run", "hello", "one", "--profile", "mock", "--json"])
    events = [json.loads(line) for line in first.stdout.splitlines()]
    assert events[-1]["type"] == "final"
    session_id = events[-1]["result"]["session_id"]

    second = runner.invoke(app, ["run", "hello", "two", "-p", "mock", "--session", session_id])
    assert second.exit_code == 0, second.output
    saved = json.loads(
        (project / f".agent-base/sessions/hello-agent/{session_id}.json").read_text()
    )
    assert [m["parts"][0]["text"] for m in saved["messages"]] == [
        "one",
        "mock: one",
        "two",
        "mock: two",
    ]


def test_config_validate_and_show(project: Path) -> None:
    ok = runner.invoke(app, ["config", "validate", "hello", "--set", "model.profile=mock"])
    assert ok.exit_code == 0, ok.output
    assert "hello-agent: 4 tool(s), 2 skill(s)" in ok.stdout

    missing = runner.invoke(app, ["config", "validate", "--set", "model.profile=openai"])
    assert missing.exit_code == 1
    assert "needs environment variable(s): OPENAI_API_KEY" in missing.output

    shown = runner.invoke(app, ["config", "show", "hello", "--resolved"])
    assert 'model.profile = "smart"    # agents/hello_agent/agent.yaml:13' in shown.stdout
    assert 'profiles.smart.model = "claude-opus-5-5"    # configs/models.yaml:' in shown.stdout


def test_bad_config_is_a_clean_error(project: Path) -> None:
    agent_yaml = project / "agents/hello_agent/agent.yaml"
    agent_yaml.write_text(agent_yaml.read_text() + "\nmystery: true\n")
    result = runner.invoke(app, ["run", "hello", "hi", "-p", "mock"])
    assert result.exit_code == 1
    assert (
        "agents/hello_agent/agent.yaml:16: mystery: Extra inputs are not permitted" in result.output
    )


async def test_new_agent_from_template_works(project: Path) -> None:
    result = runner.invoke(
        app, ["new", "billing-bot", "--description", "Answers: billing questions"]
    )
    assert result.exit_code == 0, result.output
    agent_dir = project / "agents/billing-bot"
    assert sorted(p.name for p in agent_dir.iterdir()) == [
        "agent.py",
        "agent.yaml",
        "tests",
        "tools.py",
    ]

    agent, model = load_test_agent(
        agent_dir, [{"tool_calls": [{"name": "echo", "arguments": {"text": "ping"}}]}, "pong"]
    )
    assert type(agent).__name__ == "BillingBotAgent"
    assert agent.config.agent.description == "Answers: billing questions"
    assert (await agent.run("ping")).output == "pong"
    assert model.requests[1].messages[-1].tool_results[0].content == "ping"

    again = runner.invoke(app, ["new", "billing-bot"])
    assert again.exit_code == 1 and "already exists" in again.output


def test_skill_new_and_validate(project: Path) -> None:
    created = runner.invoke(
        app, ["skill", "new", "release-notes", "--description", "Write release notes."]
    )
    assert created.exit_code == 0, created.output
    checked = runner.invoke(app, ["skill", "validate", "skills", "agents/hello_agent/skills"])
    assert checked.exit_code == 0, checked.output
    assert checked.stdout.count("✓") == 3

    bad = project / "skills/Broken"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: broken\n---\n")
    failed = runner.invoke(app, ["skill", "validate", "skills"])
    assert failed.exit_code == 1
    assert "'description' is required" in failed.stdout
