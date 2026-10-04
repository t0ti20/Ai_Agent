"""Versioned Jinja2 prompt templates (FR-21).

Templates are looked up in the agent's `prompts/`, then the project's
`prompts/`, then the ones shipped with the package, so an agent can override
any of them or `{% include %}` shared pieces. A per-profile variant wins when
it exists: `system/default.local.md.j2` is used instead of
`system/default.md.j2` when the `local` profile is active.

Keep system prompts free of volatile values (dates, request IDs): the rendered
text must not change between requests in a conversation, or the prompt cache
and Claude's thinking-block checks both reset.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from agent_base.core.errors import ConfigError

BUILTIN_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def render_prompt(
    name: str, search_dirs: Iterable[Path], variables: dict[str, Any], profile: str | None = None
) -> str:
    dirs = [str(d) for d in [*search_dirs, BUILTIN_PROMPTS] if d.is_dir()]
    env = Environment(
        loader=FileSystemLoader(dirs),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )
    candidates = [_variant(name, profile), name] if profile else [name]
    for candidate in candidates:
        try:
            template = env.get_template(candidate)
        except TemplateNotFound:
            continue
        return template.render(**variables).strip()
    raise ConfigError(f"prompt template '{name}' not found in: {', '.join(dirs)}")


def _variant(name: str, profile: str) -> str:
    path = PurePosixPath(name)
    stem, dot, rest = path.name.partition(".")
    return str(path.with_name(f"{stem}.{profile}{dot}{rest}"))
