"""`@tool`: turn a typed Python function into a model-callable tool (FR-10).

The JSON Schema comes from the type hints, the description from the docstring's
first paragraph, and per-argument descriptions from a Google-style `Args:` block.
Arguments are validated with Pydantic before the function runs, so a model that
sends a wrong type gets a readable error back instead of crashing the tool.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, overload

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from agent_base.config.models import Risk
from agent_base.core.errors import ToolDefinitionError

_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_ARG_LINE = re.compile(r"^\s{2,}(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)$")


@dataclass(frozen=True)
class ToolSpec:
    """What the model sees: name, description and JSON Schema of the arguments."""

    name: str
    description: str
    input_schema: dict[str, Any]


class Tool:
    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        namespace: str = "default",
        risk: Risk = "read",
        timeout_s: float | None = None,
    ) -> None:
        self.fn = fn
        self.name = name or fn.__name__
        if not _NAME.match(self.name):
            raise ToolDefinitionError(
                f"tool name '{self.name}' must match {_NAME.pattern} to be valid for every provider"
            )
        summary, arg_docs = _parse_docstring(inspect.getdoc(fn) or "")
        self.description = description or summary or self.name
        self.namespace = namespace
        self.risk: Risk = risk
        self.timeout_s = timeout_s
        self.args_model = _args_model(fn, self.name, arg_docs)
        self.spec = ToolSpec(self.name, self.description, _clean_schema(self.args_model))

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}"

    async def invoke(self, arguments: dict[str, Any]) -> str:
        """Validate the arguments, run the function, and render its result as text."""
        try:
            parsed = self.args_model.model_validate(arguments)
        except ValidationError as exc:
            raise ValueError(f"invalid arguments for {self.name}: {exc}") from None
        kwargs = {key: getattr(parsed, key) for key in type(parsed).model_fields}
        if inspect.iscoroutinefunction(self.fn):
            result = await self.fn(**kwargs)
        else:
            result = await asyncio.to_thread(self.fn, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        return _render(result)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.fn(*args, **kwargs)

    def __repr__(self) -> str:
        return f"Tool({self.qualified_name!r}, risk={self.risk!r})"


@overload
def tool(fn: Callable[..., Any], /) -> Tool: ...
@overload
def tool(
    *,
    name: str | None = None,
    description: str | None = None,
    namespace: str = "default",
    risk: Risk = "read",
    timeout_s: float | None = None,
) -> Callable[[Callable[..., Any]], Tool]: ...
def tool(
    fn: Callable[..., Any] | None = None,
    /,
    *,
    name: str | None = None,
    description: str | None = None,
    namespace: str = "default",
    risk: Risk = "read",
    timeout_s: float | None = None,
) -> Tool | Callable[[Callable[..., Any]], Tool]:
    """Decorate a function as a tool. Use bare (`@tool`) or with options (`@tool(risk="write")`)."""

    def wrap(f: Callable[..., Any]) -> Tool:
        return Tool(
            f,
            name=name,
            description=description,
            namespace=namespace,
            risk=risk,
            timeout_s=timeout_s,
        )

    return wrap(fn) if fn is not None else wrap


def as_tool(obj: Tool | Callable[..., Any] | Callable[..., Awaitable[Any]]) -> Tool:
    return obj if isinstance(obj, Tool) else Tool(obj)


def _args_model(fn: Callable[..., Any], name: str, arg_docs: dict[str, str]) -> type[BaseModel]:
    try:
        hints = inspect.get_annotations(fn, eval_str=True)
    except NameError as exc:
        raise ToolDefinitionError(f"tool {name}: cannot resolve type hint ({exc})") from None
    fields: dict[str, Any] = {}
    for param in inspect.signature(fn).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise ToolDefinitionError(f"tool {name}: *args/**kwargs are not supported")
        if param.name not in hints:
            raise ToolDefinitionError(f"tool {name}: parameter '{param.name}' needs a type hint")
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param.name] = (
            hints[param.name],
            Field(default=default, description=arg_docs.get(param.name)),
        )
    return create_model(f"{name}_args", __config__=ConfigDict(extra="forbid"), **fields)


def _clean_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    schema.setdefault("properties", {})
    return schema


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    summary = doc.split("\n\n", 1)[0].strip().replace("\n", " ")
    args: dict[str, str] = {}
    in_args = False
    current: str | None = None
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped in ("Args:", "Arguments:", "Parameters:"):
            in_args = True
            continue
        if in_args:
            if stripped.endswith(":") and not line.startswith(" "):
                break
            match = _ARG_LINE.match(line)
            if match:
                current = str(match.group(1))
                args[current] = str(match.group(2)).strip()
            elif current and stripped:
                args[current] += " " + stripped
    return summary, args


def _render(result: Any) -> str:
    if result is None:
        return "ok"
    if isinstance(result, str):
        return result
    if isinstance(result, BaseModel):
        return result.model_dump_json()
    return json.dumps(result, default=str, ensure_ascii=False)
