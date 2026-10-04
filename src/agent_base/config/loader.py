"""Layered configuration loading (CF-01 to CF-04).

Layers, later wins:

1. Built-in defaults (the Pydantic models)
2. `configs/default.yaml`
3. `configs/models.yaml`
4. `configs/env/<env>.yaml`, where env comes from the `env` argument, then `AGENT_ENV`, then "dev"
5. The agent's own `agent.yaml`
6. Environment variables: `AGENT_BASE__MODEL__PROFILE=fast` sets `model.profile`
7. Explicit overrides (CLI `--set model.profile=fast`)

Mappings merge key by key; lists and scalars replace. Every value remembers the
file and line it came from, so errors and `agent config show` can point at it.
`${VAR}` and `${VAR:-default}` are interpolated after merging. A reference to an
unset variable with no default is left as-is, and only fails when the profile
that uses it is actually selected.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import ValidationError
from ruamel.yaml import YAML, CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from agent_base.config.models import AgentConfig
from agent_base.core.errors import ConfigError

ENV_PREFIX = "AGENT_BASE__"
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_SECRET_KEYS = ("api_key", "token", "secret", "password")

Tree = dict[str, Any]
Sources = dict[str, str]


@dataclass
class LoadedConfig:
    config: AgentConfig
    root: Path
    env: str
    agent_dir: Path | None = None
    raw: Tree = field(default_factory=dict)
    """Merged values before interpolation, used to show secret references safely."""
    sources: Sources = field(default_factory=dict)
    """Dotted path -> where the value came from ("configs/models.yaml:12")."""


def find_project_root(start: Path) -> Path:
    """The nearest ancestor with a `configs/` folder, else `start` itself."""
    start = start.resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "configs").is_dir():
            return candidate
    return start


def load_config(
    agent_dir: Path | None = None,
    *,
    config_file: str = "agent.yaml",
    root: Path | None = None,
    env: str | None = None,
    overrides: Mapping[str, Any] | Iterable[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> LoadedConfig:
    root = (root or find_project_root(agent_dir or Path.cwd())).resolve()
    if environ is None:
        load_dotenv(root / ".env", override=False)
        environ = os.environ
    env = env or environ.get("AGENT_ENV") or "dev"

    merged: Tree = {}
    sources: Sources = {}
    files = [
        root / "configs" / "default.yaml",
        root / "configs" / "models.yaml",
        root / "configs" / "env" / f"{env}.yaml",
    ]
    if agent_dir is not None:
        files.append(agent_dir / config_file)
    for path in files:
        if path.is_file():
            data, data_sources = _read_yaml(path, root)
            _merge(merged, data, sources, data_sources)

    env_layer, env_sources = _from_environ(environ)
    _merge(merged, env_layer, sources, env_sources)
    if overrides is not None:
        cli_layer, cli_sources = _from_overrides(overrides)
        _merge(merged, cli_layer, sources, cli_sources)

    _check_secret_references(merged, sources)
    resolved = _interpolate(copy.deepcopy(merged), environ)
    try:
        config = AgentConfig.model_validate(resolved)
    except ValidationError as exc:
        raise ConfigError(_format_errors(exc, sources)) from None
    return LoadedConfig(
        config=config, root=root, env=env, agent_dir=agent_dir, raw=merged, sources=sources
    )


def resolved_items(loaded: LoadedConfig) -> list[tuple[str, Any, str]]:
    """Every leaf of the final config as (path, display value, source), secrets redacted."""
    items: list[tuple[str, Any, str]] = []
    for path, value in _leaves(loaded.config.model_dump(mode="json")):
        if any(key in path.rsplit(".", 1)[-1] for key in _SECRET_KEYS) and value is not None:
            raw = _get_path(loaded.raw, path)
            value = raw if isinstance(raw, str) and "${" in raw else "***"
        source = _source_for(path, loaded.sources) if _has_path(loaded.raw, path) else None
        items.append((path, value, source or "default"))
    return items


def unresolved_references(value: str) -> list[str]:
    return [m.group(1) for m in _VAR.finditer(value)]


# YAML reading -------------------------------------------------------------------


def _read_yaml(path: Path, root: Path) -> tuple[Tree, Sources]:
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    try:
        doc = YAML(typ="rt").load(path.read_text(encoding="utf-8"))
    except YAMLError as exc:
        raise ConfigError(f"{rel}: invalid YAML: {exc}") from None
    if doc is None:
        return {}, {}
    if not isinstance(doc, CommentedMap):
        raise ConfigError(f"{rel}: top level must be a mapping")
    sources: Sources = {}
    return _plain(doc, rel, "", sources), sources


def _plain(node: Any, rel: str, prefix: str, sources: Sources) -> Any:
    if isinstance(node, CommentedMap):
        out: Tree = {}
        for key, value in node.items():
            path = f"{prefix}{key}"
            line = node.lc.key(key)[0] + 1
            sources[path] = f"{rel}:{line}"
            out[str(key)] = _plain(value, rel, path + ".", sources)
        return out
    if isinstance(node, CommentedSeq):
        return [_plain(item, rel, prefix, {}) for item in node]
    if isinstance(node, bool) or node is None:
        return node
    for kind in (int, float, str):
        if isinstance(node, kind):
            return kind(node)
    return node


# Merging ------------------------------------------------------------------------


def _merge(
    base: Tree, layer: Tree, sources: Sources, layer_sources: Sources, prefix: str = ""
) -> None:
    for key, value in layer.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value, sources, layer_sources, path + ".")
            continue
        base[key] = copy.deepcopy(value)
        for stale in [p for p in sources if p == path or p.startswith(path + ".")]:
            del sources[stale]
        sources.update(
            {p: s for p, s in layer_sources.items() if p == path or p.startswith(path + ".")}
        )


def _from_environ(environ: Mapping[str, str]) -> tuple[Tree, Sources]:
    layer: Tree = {}
    sources: Sources = {}
    for name, value in environ.items():
        if name.startswith(ENV_PREFIX) and len(name) > len(ENV_PREFIX):
            path = name[len(ENV_PREFIX) :].lower().replace("__", ".")
            _set_path(layer, path, _scalar(value))
            sources[path] = f"env:{name}"
    return layer, sources


def _from_overrides(overrides: Mapping[str, Any] | Iterable[str]) -> tuple[Tree, Sources]:
    pairs: list[tuple[str, Any]] = []
    if isinstance(overrides, Mapping):
        pairs = list(overrides.items())
    else:
        for item in overrides:
            if "=" not in item:
                raise ConfigError(f"override '{item}' must look like key.path=value")
            key, value = item.split("=", 1)
            pairs.append((key.strip(), _scalar(value)))
    layer: Tree = {}
    sources: Sources = {}
    for path, value in pairs:
        _set_path(layer, path, value)
        sources[path] = f"cli:--set {path}"
    return layer, sources


def _scalar(text: str) -> Any:
    """Parse an env/CLI value the way YAML would ("3" -> 3, "true" -> True, "[a]" -> list)."""
    try:
        return YAML(typ="safe").load(text)
    except YAMLError:
        return text


def _set_path(tree: Tree, path: str, value: Any) -> None:
    *parents, last = path.split(".")
    node = tree
    for part in parents:
        node = node.setdefault(part, {})
    node[last] = value


def _get_path(tree: Tree, path: str) -> Any:
    node: Any = tree
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _has_path(tree: Tree, path: str) -> bool:
    node: Any = tree
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


# Secrets, interpolation, errors --------------------------------------------------


def _check_secret_references(merged: Tree, sources: Sources) -> None:
    for name, profile in (merged.get("profiles") or {}).items():
        key = profile.get("api_key") if isinstance(profile, dict) else None
        if isinstance(key, str) and "${" not in key:
            where = _source_for(f"profiles.{name}.api_key", sources)
            raise ConfigError(
                f"{where}: profiles.{name}.api_key must reference an environment variable, "
                "like ${MY_API_KEY}; secrets are never stored in config files"
            )


def _interpolate(node: Any, environ: Mapping[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: _interpolate(v, environ) for k, v in node.items()}
    if isinstance(node, list):
        return [_interpolate(v, environ) for v in node]
    if isinstance(node, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in environ:
                return environ[name]
            return default if default is not None else match.group(0)

        return _VAR.sub(replace, node)
    return node


def _source_for(path: str, sources: Sources) -> str | None:
    while path:
        if path in sources:
            return sources[path]
        path = path.rpartition(".")[0]
    return None


def _format_errors(exc: ValidationError, sources: Sources) -> str:
    lines = []
    for err in exc.errors():
        path = ".".join(str(part) for part in err["loc"])
        message = err["msg"].removeprefix("Value error, ")
        where = _source_for(path, sources) if path else None
        prefix = f"{where}: " if where else ""
        lines.append(f"{prefix}{path + ': ' if path else ''}{message}")
    return "invalid configuration:\n  " + "\n  ".join(lines)


def _leaves(node: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(node, dict) and node:
        for key, value in node.items():
            yield from _leaves(value, f"{prefix}{key}.")
    else:
        yield prefix.rstrip("."), node
