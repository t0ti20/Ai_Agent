from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from agent_base import AgentConfig, BaseAgent, ModelProfile
from agent_base.adapters.llm.mock import MockAdapter, ScriptTurn
from agent_base.policy.approvals import Approver

REPO = Path(__file__).resolve().parents[1]

AgentFactory = Callable[..., tuple[BaseAgent, MockAdapter]]


@pytest.fixture
def make_agent(tmp_path: Path) -> AgentFactory:
    """Build a config-only agent with the given tools, wired to a scripted mock model."""

    def factory(
        responses: Sequence[ScriptTurn] = (),
        tools: Sequence[Any] = (),
        *,
        config: dict[str, Any] | None = None,
        approver: Approver | None = None,
        base: type[BaseAgent] = BaseAgent,
    ) -> tuple[BaseAgent, MockAdapter]:
        data: dict[str, Any] = {
            "agent": {"name": "test-agent"},
            "profiles": {"mock": {"adapter": "mock", "model": "mock"}},
            "model": {"profile": "mock"},
            "skills": {"paths": [], "user_path": None},
        }
        for key, value in (config or {}).items():
            data[key] = {**data.get(key, {}), **value}
        mock = MockAdapter(ModelProfile(adapter="mock", model="mock"), responses)
        cls = type("ScriptedAgent", (base,), {"tools": list(tools)})
        agent = cls(
            agent_dir=tmp_path,
            root=tmp_path,
            config=AgentConfig.model_validate(data),
            approver=approver,
            adapter=mock,
        )
        return agent, mock

    return factory


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the template project (configs, skills, hello agent) as the working directory."""
    root = tmp_path / "project"
    shutil.copytree(REPO / "configs", root / "configs")
    shutil.copytree(REPO / "skills", root / "skills")
    shutil.copytree(
        REPO / "agents" / "hello_agent",
        root / "agents" / "hello_agent",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    monkeypatch.chdir(root)
    for name in ("AGENT_ENV", "OPENAI_MODEL", "LOCAL_MODEL", "OLLAMA_URL"):
        monkeypatch.delenv(name, raising=False)
    return root
