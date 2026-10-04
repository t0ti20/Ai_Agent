from __future__ import annotations

from pathlib import Path

import pytest

from agent_base.config.loader import load_config, resolved_items
from agent_base.config.models import json_schema
from agent_base.core.errors import ConfigError

REPO = Path(__file__).resolve().parents[1]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write(
        tmp_path / "configs/default.yaml",
        "model:\n  profile: smart\n  effort: medium\nagent:\n  budgets:\n    max_steps: 25\n",
    )
    write(
        tmp_path / "configs/models.yaml",
        "profiles:\n"
        "  smart: {adapter: anthropic, model: claude-opus-5-5}\n"
        "  local:\n"
        "    adapter: openai_compat\n"
        "    model: ${LOCAL_MODEL:-qwen3:14b}\n"
        "    base_url: ${OLLAMA_URL}\n"
        "    capabilities: {thinking: true}\n",
    )
    write(tmp_path / "configs/env/prod.yaml", "agent:\n  budgets:\n    max_steps: 5\n")
    write(tmp_path / "agents/a/agent.yaml", "agent:\n  name: a\nmodel:\n  effort: high\n")
    return tmp_path


def test_layers_merge_in_order_and_remember_sources(root: Path) -> None:
    loaded = load_config(
        root / "agents/a",
        root=root,
        env="prod",
        environ={"AGENT_BASE__MODEL__PROFILE": "local"},
        overrides=["agent.budgets.timeout_s=30"],
    )
    config = loaded.config
    assert config.agent.name == "a"
    assert config.model.effort == "high"  # agent.yaml beats default.yaml
    assert config.agent.budgets.max_steps == 5  # env overlay beats default.yaml
    assert config.model.profile == "local"  # env var beats files
    assert config.agent.budgets.timeout_s == 30  # CLI override, parsed as a number
    assert loaded.sources["model.effort"] == "agents/a/agent.yaml:4"
    assert loaded.sources["agent.budgets.max_steps"] == "configs/env/prod.yaml:3"
    assert loaded.sources["model.profile"] == "env:AGENT_BASE__MODEL__PROFILE"


def test_interpolation_uses_defaults_and_leaves_unset_references(root: Path) -> None:
    config = load_config(root=root, environ={}).config
    assert config.profiles["local"].model == "qwen3:14b"
    assert config.profiles["local"].base_url == "${OLLAMA_URL}"
    config = load_config(root=root, environ={"OLLAMA_URL": "http://box:11434/v1"}).config
    assert config.profiles["local"].base_url == "http://box:11434/v1"


def test_yaml_booleans_stay_booleans(root: Path) -> None:
    config = load_config(root=root, environ={}).config
    assert config.profiles["local"].capabilities["thinking"] is True


def test_literal_api_key_is_rejected_with_its_location(root: Path) -> None:
    write(
        root / "agents/a/agent.yaml",
        "profiles:\n  smart:\n    api_key: sk-ant-very-secret\n",
    )
    with pytest.raises(ConfigError, match=r"agents/a/agent.yaml:3: profiles.smart.api_key"):
        load_config(root / "agents/a", root=root, environ={})


def test_validation_errors_point_at_file_and_line(root: Path) -> None:
    write(root / "agents/a/agent.yaml", "agent:\n  name: a\n  loop: wander\n  extends: base\n")
    with pytest.raises(ConfigError) as info:
        load_config(root / "agents/a", root=root, environ={})
    message = str(info.value)
    assert "agents/a/agent.yaml:3: agent.loop" in message
    assert "agents/a/agent.yaml:4: agent.extends: Extra inputs are not permitted" in message


def test_unknown_profile_is_an_error(root: Path) -> None:
    with pytest.raises(ConfigError, match="model profile 'nope' is not defined"):
        load_config(root=root, environ={}, overrides=["model.profile=nope"])


def test_resolved_items_redact_secrets(root: Path) -> None:
    write(root / "agents/a/agent.yaml", "profiles:\n  smart:\n    api_key: ${MY_KEY}\n")
    loaded = load_config(root / "agents/a", root=root, environ={"MY_KEY": "sk-secret"})
    assert loaded.config.profiles["smart"].api_key == "sk-secret"
    items = {path: (value, source) for path, value, source in resolved_items(loaded)}
    assert items["profiles.smart.api_key"] == ("${MY_KEY}", "agents/a/agent.yaml:3")
    assert items["agent.loop"] == ("react", "default")
    assert all("sk-secret" not in str(value) for value, _ in items.values())


def test_repository_configs_are_valid() -> None:
    config = load_config(REPO / "agents/hello_agent", root=REPO, environ={}).config
    assert config.agent.name == "hello-agent"
    assert set(config.profiles) >= {"smart", "fast", "openai", "local", "mock"}


def test_committed_json_schema_is_current() -> None:
    import json

    committed = json.loads((REPO / "configs/schema/agent.schema.json").read_text())
    assert committed == json_schema(), (
        "run: agent config schema --out configs/schema/agent.schema.json"
    )
