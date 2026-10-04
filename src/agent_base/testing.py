"""Helpers for testing agents against the scripted mock model, offline and deterministic."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent_base.adapters.llm.mock import MockAdapter, ScriptTurn
from agent_base.config.models import ModelProfile
from agent_base.core.agent import BaseAgent
from agent_base.core.discovery import load_agent_class
from agent_base.policy.approvals import Approver, approve_all

TEST_PROFILE = "__test__"


def load_test_agent(
    agent_dir: Path,
    responses: Sequence[ScriptTurn] = (),
    *,
    approver: Approver = approve_all,
    overrides: dict[str, Any] | None = None,
) -> tuple[BaseAgent, MockAdapter]:
    """Load the agent in `agent_dir` with its model replaced by a scripted mock.

    Returns the agent and the mock; `mock.requests` holds every request the
    agent sent, for assertions.
    """
    mock = MockAdapter(ModelProfile(adapter="mock", model="mock"), responses)
    layer: dict[str, Any] = {
        "profiles": {TEST_PROFILE: {"adapter": "mock", "model": "mock"}},
        "model": {"profile": TEST_PROFILE, "fallback": []},
        **(overrides or {}),
    }
    cls = load_agent_class(agent_dir)
    agent = cls(agent_dir=agent_dir, overrides=layer, approver=approver, adapter=mock)
    return agent, mock
