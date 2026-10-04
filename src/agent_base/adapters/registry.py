"""Adapter lookup by name. Imports are lazy, so a missing optional SDK only matters when used."""

from __future__ import annotations

import importlib

from agent_base.adapters.llm.base import ModelAdapter
from agent_base.config.loader import unresolved_references
from agent_base.config.models import ModelProfile
from agent_base.core.errors import ConfigError

_BUILTIN = {
    "anthropic": "agent_base.adapters.llm.anthropic:AnthropicAdapter",
    "openai": "agent_base.adapters.llm.openai:OpenAIAdapter",
    "openai_compat": "agent_base.adapters.llm.openai_compat:OpenAICompatAdapter",
    "mock": "agent_base.adapters.llm.mock:MockAdapter",
}
_registered: dict[str, type[ModelAdapter]] = {}


def register_adapter(name: str, cls: type[ModelAdapter]) -> None:
    """Make a third-party adapter available to `adapter: <name>` in profiles."""
    _registered[name] = cls


def adapter_names() -> list[str]:
    return sorted({*_BUILTIN, *_registered})


def create_adapter(profile_name: str, profile: ModelProfile) -> ModelAdapter:
    missing = sorted(
        {
            var
            for value in (profile.model, profile.base_url, profile.api_key)
            if value
            for var in unresolved_references(value)
        }
    )
    if missing:
        raise ConfigError(
            f"profile '{profile_name}' needs environment variable(s) {', '.join(missing)}; "
            "set them (or add them to .env) or choose another profile"
        )
    cls = _registered.get(profile.adapter)
    if cls is None:
        target = _BUILTIN.get(profile.adapter)
        if target is None:
            raise ConfigError(
                f"profile '{profile_name}': unknown adapter '{profile.adapter}' "
                f"(available: {', '.join(adapter_names())})"
            )
        module, _, attr = target.partition(":")
        cls = getattr(importlib.import_module(module), attr)
    return cls(profile)
