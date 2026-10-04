"""Find agents by name and load their classes from `agents/<name>/agent.py`."""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import inspect
import re
import sys
import types
from pathlib import Path

from agent_base.core.agent import BaseAgent
from agent_base.core.errors import ConfigError

_PACKAGE = "agent_base_user_agents"


def resolve_agent_dir(name_or_path: str, root: Path) -> Path:
    """A path to an agent folder, or a name under `agents/` (`hello` finds `agents/hello_agent`)."""
    direct = Path(name_or_path).expanduser()
    candidates = [direct, root / "agents" / name_or_path, root / "agents" / f"{name_or_path}_agent"]
    for candidate in candidates:
        if candidate.is_dir() and (
            (candidate / "agent.yaml").is_file() or (candidate / "agent.py").is_file()
        ):
            return candidate.resolve()
    looked = ", ".join(str(c) for c in candidates)
    raise ConfigError(f"agent '{name_or_path}' not found (looked in: {looked})")


def load_agent_class(agent_dir: Path) -> type[BaseAgent]:
    """The BaseAgent subclass defined in `agent.py`, or BaseAgent for a config-only agent.

    The folder is imported as a package, so `agent.py` can use relative imports
    (`from .tools import lookup_order`).
    """
    agent_dir = agent_dir.resolve()
    if not (agent_dir / "agent.py").is_file():
        return BaseAgent
    if _PACKAGE not in sys.modules:
        root_package = types.ModuleType(_PACKAGE)
        root_package.__path__ = []
        sys.modules[_PACKAGE] = root_package
    package = _PACKAGE + "." + re.sub(r"\W", "_", agent_dir.name)
    if package not in sys.modules:
        spec = importlib.machinery.ModuleSpec(package, None, is_package=True)
        spec.submodule_search_locations = [str(agent_dir)]
        sys.modules[package] = importlib.util.module_from_spec(spec)
    module = importlib.import_module(f"{package}.agent")
    classes = [
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj)
        and issubclass(obj, BaseAgent)
        and obj is not BaseAgent
        and obj.__module__ == module.__name__
    ]
    if len(classes) != 1:
        found = ", ".join(c.__name__ for c in classes) or "none"
        raise ConfigError(
            f"{agent_dir / 'agent.py'} must define exactly one BaseAgent subclass (found: {found})"
        )
    return classes[0]
