"""Agent Skills loader (FR-07, FR-08). Format: https://agentskills.io

A skill is a folder holding `SKILL.md` (YAML front matter + Markdown body) and
optional `scripts/`, `references/` and `assets/`. Disclosure is progressive:

1. Only `name` and `description` go into the system prompt (the catalog).
2. The model calls `load_skill` to read the body when a skill is relevant.
3. The model calls `read_skill_file` for files the body refers to.

Steps 2 and 3 arrive as tool results, so the history stays append-only and the
system prompt never changes mid-conversation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from agent_base.capabilities.tools.decorator import Tool
from agent_base.core.errors import SkillError

SKILL_FILE = "SKILL.md"
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_BODY_LINES = 500
MAX_FILE_BYTES = 256 * 1024
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)

    def body(self) -> str:
        return _split_front_matter((self.path / SKILL_FILE).read_text(encoding="utf-8"))[1].strip()

    def files(self) -> list[str]:
        found = sorted(
            str(p.relative_to(self.path))
            for p in self.path.rglob("*")
            if p.is_file() and p.name != SKILL_FILE and not p.name.startswith(".")
        )
        return found[:200]

    def read_file(self, relative: str) -> str:
        target = (self.path / relative).resolve()
        if not target.is_relative_to(self.path.resolve()):
            raise SkillError(f"'{relative}' is outside skill '{self.name}'")
        if not target.is_file():
            raise SkillError(f"skill '{self.name}' has no file '{relative}'")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise SkillError(f"'{relative}' is larger than {MAX_FILE_BYTES // 1024} KB")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise SkillError(f"'{relative}' is not a text file") from None


def parse_skill(folder: Path) -> Skill:
    problems, skill = _inspect(folder)
    errors = [p for p in problems if not p.startswith("warning:")]
    if errors or skill is None:
        raise SkillError(f"{folder}: " + "; ".join(errors))
    return skill


def validate_skill(folder: Path) -> list[str]:
    """All problems with a skill folder; entries starting with 'warning:' are not fatal."""
    return _inspect(folder)[0]


def _inspect(folder: Path) -> tuple[list[str], Skill | None]:
    skill_md = folder / SKILL_FILE
    if not skill_md.is_file():
        return [f"missing {SKILL_FILE}"], None
    text = skill_md.read_text(encoding="utf-8")
    try:
        meta, body = _split_front_matter(text)
        data = YAML(typ="safe").load(meta) if meta else None
    except (SkillError, YAMLError) as exc:
        return [f"front matter: {exc}"], None
    if not isinstance(data, dict):
        return ["front matter must be a YAML mapping with name and description"], None

    problems: list[str] = []
    name, description = data.get("name"), data.get("description")
    if not isinstance(name, str) or not name:
        problems.append("'name' is required")
    else:
        if len(name) > MAX_NAME or not _NAME.match(name):
            problems.append(
                f"'name' must be 1-{MAX_NAME} lowercase letters, digits and single hyphens"
            )
        if name != folder.name:
            problems.append(f"'name' ({name}) must match the folder name ({folder.name})")
    if not isinstance(description, str) or not description.strip():
        problems.append("'description' is required")
    elif len(description) > MAX_DESCRIPTION:
        problems.append(f"'description' is longer than {MAX_DESCRIPTION} characters")
    if body.count("\n") > MAX_BODY_LINES:
        problems.append(
            f"warning: body is over {MAX_BODY_LINES} lines; move detail into references/"
        )
    if any(not p.startswith("warning:") for p in problems):
        return problems, None
    extra = {k: v for k, v in data.items() if k not in ("name", "description")}
    return problems, Skill(str(name), str(description).strip(), folder, extra)


def _split_front_matter(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        raise SkillError("SKILL.md must start with '---' front matter")
    parts = re.split(r"^---[ \t]*$", text, maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:
        raise SkillError("front matter is not closed with '---'")
    return parts[1], parts[2]


class SkillSet:
    def __init__(self, skills: Iterable[Skill] = ()) -> None:
        self._skills = {s.name: s for s in skills}

    @classmethod
    def discover(cls, paths: Iterable[Path], enabled: list[str] | None = None) -> SkillSet:
        """Load every skill under `paths`; a skill in a later path overrides one with its name."""
        found: dict[str, Skill] = {}
        searched: list[str] = []
        for base in paths:
            base = base.expanduser()
            searched.append(str(base))
            if not base.is_dir():
                continue
            for folder in sorted(p for p in base.iterdir() if (p / SKILL_FILE).is_file()):
                skill = parse_skill(folder)
                found[skill.name] = skill
        if enabled is not None:
            missing = [n for n in enabled if n not in found]
            if missing:
                raise SkillError(
                    f"skills not found: {', '.join(missing)} (searched {', '.join(searched)})"
                )
            found = {n: found[n] for n in enabled}
        return cls(found.values())

    def __len__(self) -> int:
        return len(self._skills)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def catalog(self) -> list[Skill]:
        return [self._skills[n] for n in sorted(self._skills)]

    def tools(self) -> list[Tool]:
        """The two tools that give the model progressive access to skills."""
        if not self._skills:
            return []

        def load_skill(name: str) -> str:
            """Read a skill's full instructions. Call this before following a skill.

            Args:
                name: Skill name, exactly as listed in the system prompt.
            """
            skill = self._require(name)
            files = skill.files()
            listing = "\n".join(f"- {f}" for f in files) if files else "(none)"
            footer = f"Files in this skill (read with read_skill_file):\n{listing}"
            return f"{skill.body()}\n\n---\n{footer}"

        def read_skill_file(skill: str, path: str) -> str:
            """Read a file bundled with a skill, such as a reference document or template.

            Args:
                skill: Skill name.
                path: File path relative to the skill folder, as listed by load_skill.
            """
            return self._require(skill).read_file(path)

        return [Tool(load_skill, namespace="skills"), Tool(read_skill_file, namespace="skills")]

    def _require(self, name: str) -> Skill:
        skill = self._skills.get(name)
        if skill is None:
            raise SkillError(
                f"unknown skill '{name}'; available: {', '.join(sorted(self._skills))}"
            )
        return skill
