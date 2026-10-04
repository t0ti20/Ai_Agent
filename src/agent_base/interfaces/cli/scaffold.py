"""Generate new agents and skills from the templates shipped in `agent_base/templates`."""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from agent_base.capabilities.skills.loader import MAX_DESCRIPTION, SKILL_FILE
from agent_base.core.errors import ConfigError

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
_AGENT_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def create_agent(name: str, description: str, directory: Path) -> Path:
    if not _AGENT_NAME.match(name):
        raise ConfigError(f"agent name '{name}' must be lowercase letters, digits, - or _")
    target = directory / name
    if target.exists():
        raise ConfigError(f"{target} already exists")
    words = re.split(r"[-_]", name)
    class_name = "".join(w.capitalize() for w in words)
    if not class_name.endswith("Agent"):
        class_name += "Agent"
    _render_tree(
        TEMPLATES / "agent",
        target,
        {"name": name, "class_name": class_name, "description": description},
    )
    return target


def create_skill(name: str, description: str, directory: Path) -> Path:
    if len(name) > 64 or not _SKILL_NAME.match(name):
        raise ConfigError(
            f"skill name '{name}' must be 1-64 lowercase letters, digits and single hyphens"
        )
    if len(description) > MAX_DESCRIPTION:
        raise ConfigError(f"description is longer than {MAX_DESCRIPTION} characters")
    target = directory / name
    if (target / SKILL_FILE).exists():
        raise ConfigError(f"{target / SKILL_FILE} already exists")
    title = name.replace("-", " ").capitalize()
    _render_tree(
        TEMPLATES / "skill", target, {"name": name, "description": description, "title": title}
    )
    return target


def _render_tree(source: Path, target: Path, variables: dict[str, str]) -> None:
    env = Environment(
        loader=FileSystemLoader(str(source)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    for path in sorted(source.rglob("*")):
        if path.is_dir() or path.name == "__pycache__" or path.suffix == ".pyc":
            continue
        relative = path.relative_to(source)
        out = target / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".j2":
            out = out.with_suffix("")
            out.write_text(
                env.get_template(relative.as_posix()).render(**variables), encoding="utf-8"
            )
        else:
            out.write_bytes(path.read_bytes())
