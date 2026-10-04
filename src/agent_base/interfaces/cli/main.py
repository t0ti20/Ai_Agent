"""The `agent` command line (FR-22): new, run, chat, config, skill."""

import asyncio
import functools
import json
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer

from agent_base.adapters.registry import adapter_names
from agent_base.capabilities.skills.loader import SKILL_FILE, validate_skill
from agent_base.capabilities.tools.decorator import Tool
from agent_base.config.loader import (
    find_project_root,
    load_config,
    resolved_items,
    unresolved_references,
)
from agent_base.config.models import json_schema
from agent_base.core.agent import BaseAgent
from agent_base.core.discovery import load_agent_class, resolve_agent_dir
from agent_base.core.errors import AgentBaseError
from agent_base.core.events import (
    ErrorEvent,
    Event,
    FinalEvent,
    RunResult,
    StateEvent,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    ToolResultEvent,
)
from agent_base.core.messages import ToolCallPart
from agent_base.core.session import Session
from agent_base.interfaces.cli.scaffold import create_agent, create_skill
from agent_base.policy.approvals import Approver, approve_all, deny_all

app = typer.Typer(help="Build, run and check agents built on agent-base.", no_args_is_help=True)
config_app = typer.Typer(help="Validate and inspect configuration.", no_args_is_help=True)
skill_app = typer.Typer(help="Create and validate Agent Skills.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(skill_app, name="skill")

F = TypeVar("F", bound=Callable[..., Any])

AgentArg = Annotated[str, typer.Argument(help="Agent name under agents/, or a path.")]
ProfileOpt = Annotated[
    str | None, typer.Option("--profile", "-p", help="Model profile to use (see models.yaml).")
]
SetOpt = Annotated[
    list[str] | None,
    typer.Option("--set", "-s", help="Override a config value: key.path=value (repeatable)."),
]
EnvOpt = Annotated[str | None, typer.Option("--env", help="Environment overlay (default: dev).")]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Approve every tool call.")]
SessionOpt = Annotated[str | None, typer.Option("--session", help="Resume a saved session ID.")]
ThinkingOpt = Annotated[bool, typer.Option("--thinking", help="Show the model's thinking.")]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")]


def _errors_to_exit(fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except AgentBaseError as exc:
            typer.secho(f"error: {exc}", fg="red", err=True)
            raise typer.Exit(1) from None

    return wrapper  # type: ignore[return-value]


# run / chat ---------------------------------------------------------------------------


@app.command()
@_errors_to_exit
def run(
    agent: AgentArg,
    prompt: Annotated[str, typer.Argument(help="What to ask the agent.")],
    profile: ProfileOpt = None,
    set_: SetOpt = None,
    env: EnvOpt = None,
    yes: YesOpt = False,
    session: SessionOpt = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print events as JSON lines.")
    ] = False,
    thinking: ThinkingOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Run an agent once on a prompt and stream the answer."""
    instance = _build_agent(agent, profile, set_, env, yes, verbose)
    store = _sessions_dir(instance)
    current = Session.load(store / f"{session}.json") if session else instance.new_session()
    result = asyncio.run(_drive(instance, prompt, current, json_output, thinking))
    current.save(store)
    if not json_output:
        _summary(result)
    if result.stop_reason != "completed":
        raise typer.Exit(2)


@app.command()
@_errors_to_exit
def chat(
    agent: AgentArg,
    profile: ProfileOpt = None,
    set_: SetOpt = None,
    env: EnvOpt = None,
    yes: YesOpt = False,
    session: SessionOpt = None,
    thinking: ThinkingOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Chat with an agent. Commands: /new, /usage, /exit."""
    instance = _build_agent(agent, profile, set_, env, yes, verbose)
    store = _sessions_dir(instance)
    current = Session.load(store / f"{session}.json") if session else instance.new_session()
    profile_name = instance.config.model.profile
    model = instance.config.profiles[profile_name].model
    typer.secho(f"{instance.name} on {profile_name} ({model}). /exit to quit.", dim=True, err=True)
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line == "/new":
            current.save(store)
            current = instance.new_session()
            typer.secho("started a new session", dim=True, err=True)
            continue
        if line == "/usage":
            typer.secho(
                f"session {current.id}: {len(current.messages)} messages", dim=True, err=True
            )
            continue
        typer.echo(f"\n{instance.name}> ", nl=False)
        try:
            result = asyncio.run(_drive(instance, line, current, False, thinking))
        except KeyboardInterrupt:
            typer.secho("\n(interrupted)", dim=True, err=True)
            continue
        current.save(store)
        if result.stop_reason != "completed":
            typer.secho(f"\n[{result.stop_reason}: {result.detail}]", fg="yellow", err=True)
    path = current.save(store)
    typer.secho(f"\nsaved session {current.id} ({path})", dim=True, err=True)


async def _drive(
    agent: BaseAgent, prompt: str, session: Session, json_output: bool, thinking: bool
) -> RunResult:
    result: RunResult | None = None
    in_thinking = False
    async for event in agent.stream(prompt, session=session):
        if json_output:
            typer.echo(event.model_dump_json())
        else:
            in_thinking = _render(event, thinking, in_thinking)
        if isinstance(event, FinalEvent):
            result = event.result
    assert result is not None
    if not json_output:
        typer.echo()
    return result


def _render(event: Event, thinking: bool, in_thinking: bool) -> bool:
    """Print one event for a human. Returns whether a thinking block is open."""
    if isinstance(event, ThinkingDelta):
        if thinking:
            typer.secho(event.text, dim=True, nl=False, err=True)
            return True
        return in_thinking
    if in_thinking:
        typer.echo(err=True)
    if isinstance(event, TextDelta):
        typer.echo(event.text, nl=False)
    elif isinstance(event, ToolCallEvent):
        args = json.dumps(event.call.arguments, ensure_ascii=False)
        typer.secho(f"\n→ {event.call.name}({_clip(args, 200)})", fg="cyan", err=True)
    elif isinstance(event, ToolResultEvent):
        color = "red" if event.result.is_error else "green"
        content = _clip(event.result.content.replace("\n", " "), 200)
        typer.secho(f"  ← {content} ({event.duration_ms} ms)", fg=color, err=True)
    elif isinstance(event, StateEvent) and event.state == "fallback":
        typer.secho(f"\n[fallback] {event.detail}", fg="yellow", err=True)
    elif isinstance(event, ErrorEvent):
        typer.secho(f"\n{event.message}", fg="red", err=True)
    return False


def _summary(result: RunResult) -> None:
    u = result.usage
    cost = f" · ${u.cost_usd:.4f}" if u.cost_usd else ""
    sent = u.input_tokens + u.cache_read_tokens + u.cache_write_tokens
    tokens = f"{sent:,} in / {u.output_tokens:,} out"
    line = f"[{result.stop_reason} · {result.steps} step(s) · {tokens} tokens{cost}]"
    typer.secho(line, dim=True, err=True)
    if result.detail:
        typer.secho(result.detail, fg="yellow", err=True)
    typer.secho(f"resume with --session {result.session_id}", dim=True, err=True)


# new ----------------------------------------------------------------------------------


@app.command()
@_errors_to_exit
def new(
    name: Annotated[str, typer.Argument(help="Agent name: lowercase letters, digits, - and _.")],
    description: Annotated[str, typer.Option(help="One line saying what the agent does.")] = (
        "A new agent built on agent-base."
    ),
    directory: Annotated[Path, typer.Option("--dir", help="Where agents live.")] = Path("agents"),
) -> None:
    """Create a new agent from the template."""
    root = find_project_root(Path.cwd())
    target = create_agent(name, description, root / directory)
    typer.secho(f"created {target}", fg="green")
    typer.echo(f'try it offline:  agent run {name} "hello" --profile mock')
    typer.echo(f"run its tests:   uv run pytest {target}")


# config -------------------------------------------------------------------------------


@config_app.command("validate")
@_errors_to_exit
def config_validate(
    agent: Annotated[
        str | None, typer.Argument(help="Agent to validate (default: base only).")
    ] = None,
    set_: SetOpt = None,
    env: EnvOpt = None,
) -> None:
    """Load and validate the merged config; with an agent, also its skills, tools and prompt."""
    root = find_project_root(Path.cwd())
    if agent is None:
        loaded = load_config(root=root, env=env, overrides=set_ or [])
        config = loaded.config
        summary = f"base config ({loaded.env})"
    else:
        instance = _build_agent(agent, None, set_, env, False, False)
        instance.system_prompt()
        config = instance.config
        summary = (
            f"{instance.name}: {len(instance.tool_registry)} tool(s), "
            f"{len(instance.skill_set)} skill(s)"
        )
    problems = []
    for name in [config.model.profile, *config.model.fallback]:
        profile = config.profiles[name]
        if profile.adapter not in adapter_names():
            problems.append(f"profile '{name}': unknown adapter '{profile.adapter}'")
        missing = {
            var
            for value in (profile.model, profile.base_url, profile.api_key)
            if value
            for var in unresolved_references(value)
        }
        if missing:
            problems.append(
                f"profile '{name}' needs environment variable(s): {', '.join(sorted(missing))}"
            )
    if problems:
        for problem in problems:
            typer.secho(f"✗ {problem}", fg="red", err=True)
        raise typer.Exit(1)
    profile = config.profiles[config.model.profile]
    typer.secho(
        f"✓ {summary}; profile {config.model.profile} → {profile.adapter}/{profile.model}",
        fg="green",
    )


@config_app.command("show")
@_errors_to_exit
def config_show(
    agent: Annotated[str | None, typer.Argument(help="Agent whose config to show.")] = None,
    resolved: Annotated[
        bool, typer.Option("--resolved", help="Show where each value came from.")
    ] = False,
    set_: SetOpt = None,
    env: EnvOpt = None,
) -> None:
    """Print the final merged config (secrets redacted)."""
    root = find_project_root(Path.cwd())
    agent_dir = resolve_agent_dir(agent, root) if agent else None
    config_file = load_agent_class(agent_dir).config_file if agent_dir else "agent.yaml"
    loaded = load_config(
        agent_dir, config_file=config_file, root=root, env=env, overrides=set_ or []
    )
    for path, value, source in resolved_items(loaded):
        rendered = json.dumps(value, ensure_ascii=False)
        typer.echo(f"{path} = {rendered}" + (f"    # {source}" if resolved else ""))


@config_app.command("schema")
@_errors_to_exit
def config_schema(
    out: Annotated[
        Path | None, typer.Option("--out", help="Write to a file instead of stdout.")
    ] = None,
) -> None:
    """Print the JSON Schema for agent config files (for editor autocomplete)."""
    text = json.dumps(json_schema(), indent=2) + "\n"
    if out is None:
        typer.echo(text, nl=False)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        typer.secho(f"wrote {out}", fg="green")


# skill --------------------------------------------------------------------------------


@skill_app.command("new")
@_errors_to_exit
def skill_new(
    name: Annotated[str, typer.Argument(help="Skill name: lowercase letters, digits, hyphens.")],
    description: Annotated[
        str, typer.Option(help="What the skill does and when to use it.")
    ] = "Describe what this skill does and when the agent should use it.",
    directory: Annotated[Path, typer.Option("--dir", help="Folder to create it in.")] = Path(
        "skills"
    ),
) -> None:
    """Create a skill folder with a SKILL.md to fill in."""
    target = create_skill(name, description, directory)
    typer.secho(f"created {target / SKILL_FILE}", fg="green")


@skill_app.command("validate")
def skill_validate(
    paths: Annotated[list[Path], typer.Argument(help="Skill folders, or folders of skills.")],
) -> None:
    """Check skills against the Agent Skills format."""
    folders: list[Path] = []
    for path in paths:
        if (path / SKILL_FILE).is_file():
            folders.append(path)
        elif path.is_dir():
            folders.extend(sorted(p for p in path.iterdir() if (p / SKILL_FILE).is_file()))
    if not folders:
        typer.secho("no skills found", fg="red", err=True)
        raise typer.Exit(1)
    failed = False
    for folder in folders:
        problems = validate_skill(folder)
        errors = [p for p in problems if not p.startswith("warning:")]
        failed = failed or bool(errors)
        mark = typer.style("✗", fg="red") if errors else typer.style("✓", fg="green")
        typer.echo(f"{mark} {folder}")
        for problem in problems:
            typer.echo(f"    {problem}")
    if failed:
        raise typer.Exit(1)


# helpers ------------------------------------------------------------------------------


def _build_agent(
    agent: str,
    profile: str | None,
    sets: list[str] | None,
    env: str | None,
    yes: bool,
    verbose: bool,
) -> BaseAgent:
    root = find_project_root(Path.cwd())
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))  # lets agents import project-level tools/
    agent_dir = resolve_agent_dir(agent, root)
    cls = load_agent_class(agent_dir)
    overrides = list(sets or [])
    if profile:
        overrides += [f"model.profile={profile}", "model.fallback=[]"]
    if yes:
        approver: Approver = approve_all
    elif sys.stdin.isatty():
        approver = _ask_on_terminal
    else:
        approver = deny_all
    instance = cls(agent_dir=agent_dir, overrides=overrides, env=env, root=root, approver=approver)
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("agent_base").setLevel("DEBUG" if verbose else instance.config.logging.level)
    return instance


async def _ask_on_terminal(call: ToolCallPart, tool: Tool) -> bool:
    args = _clip(json.dumps(call.arguments, ensure_ascii=False), 500)
    typer.secho(f"\n? {tool.name} [{tool.risk}] {args}", fg="yellow", err=True)
    sys.stderr.write("  allow? [y/N] ")
    sys.stderr.flush()
    answer = await asyncio.to_thread(sys.stdin.readline)
    return answer.strip().lower() in ("y", "yes")


def _sessions_dir(agent: BaseAgent) -> Path:
    return agent.loaded.root / ".agent-base" / "sessions" / re.sub(r"[^\w.-]", "_", agent.name)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


if __name__ == "__main__":
    app()
