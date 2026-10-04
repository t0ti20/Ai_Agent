from __future__ import annotations

from pathlib import Path

import pytest

from agent_base.capabilities.skills.loader import SkillSet, validate_skill
from agent_base.core.errors import SkillError


def make_skill(
    base: Path, name: str, description: str = "Does a thing.", body: str = "Steps."
) -> Path:
    folder = base / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8"
    )
    return folder


def test_later_paths_override_earlier_ones(tmp_path: Path) -> None:
    make_skill(tmp_path / "project", "triage", "Project version.")
    make_skill(tmp_path / "agent", "triage", "Agent version.")
    make_skill(tmp_path / "project", "refunds")
    skills = SkillSet.discover([tmp_path / "project", tmp_path / "agent", tmp_path / "missing"])
    assert [s.name for s in skills.catalog()] == ["refunds", "triage"]
    triage = skills.get("triage")
    assert triage is not None and triage.description == "Agent version."


def test_enabled_list_selects_and_reports_missing(tmp_path: Path) -> None:
    make_skill(tmp_path, "triage")
    make_skill(tmp_path, "refunds")
    assert len(SkillSet.discover([tmp_path], enabled=["triage"])) == 1
    with pytest.raises(SkillError, match="skills not found: billing"):
        SkillSet.discover([tmp_path], enabled=["billing"])


def test_validation_problems(tmp_path: Path) -> None:
    folder = tmp_path / "Bad_Name"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: other\n---\nbody\n", encoding="utf-8")
    problems = validate_skill(folder)
    assert "'name' (other) must match the folder name (Bad_Name)" in problems
    assert "'description' is required" in problems
    assert validate_skill(tmp_path / "nothing") == ["missing SKILL.md"]
    long_body = make_skill(tmp_path, "long", body="line\n" * 600)
    assert validate_skill(long_body)[0].startswith("warning: body is over 500 lines")


async def test_progressive_disclosure_tools(tmp_path: Path) -> None:
    folder = make_skill(tmp_path, "triage", body="Read references/rules.md first.")
    (folder / "references").mkdir()
    (folder / "references/rules.md").write_text("Rule 1: be kind.", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    load_skill, read_skill_file = SkillSet.discover([tmp_path]).tools()

    body = await load_skill.invoke({"name": "triage"})
    assert body.startswith("Read references/rules.md first.")
    assert "- references/rules.md" in body
    assert await read_skill_file.invoke({"skill": "triage", "path": "references/rules.md"}) == (
        "Rule 1: be kind."
    )
    with pytest.raises(SkillError, match="outside skill"):
        await read_skill_file.invoke({"skill": "triage", "path": "../secret.txt"})
    with pytest.raises(SkillError, match="unknown skill 'nope'"):
        await load_skill.invoke({"name": "nope"})


def test_no_skills_means_no_skill_tools(tmp_path: Path) -> None:
    assert SkillSet.discover([tmp_path]).tools() == []
