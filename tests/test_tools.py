from __future__ import annotations

import asyncio
from typing import Literal

import pytest

from agent_base import tool
from agent_base.capabilities.tools.registry import ToolRegistry
from agent_base.core.errors import ToolDefinitionError


@tool(namespace="orders", risk="external")
async def lookup_order(order_id: str, include_items: bool = False) -> dict[str, object]:
    """Look up an order by its ID.

    Args:
        order_id: The order number, like "A-1001".
        include_items: Also return the line items.
    """
    await asyncio.sleep(0)
    return {"id": order_id, "items": ["book"] if include_items else []}


@tool
def convert(amount: float, unit: Literal["km", "mi"]) -> str:
    """Convert a distance."""
    return f"{amount * (0.621 if unit == 'km' else 1.609):.1f}"


def test_schema_comes_from_type_hints_and_docstring() -> None:
    spec = lookup_order.spec
    assert spec.name == "lookup_order"
    assert spec.description == "Look up an order by its ID."
    assert spec.input_schema["required"] == ["order_id"]
    props = spec.input_schema["properties"]
    assert props["order_id"] == {
        "type": "string",
        "description": 'The order number, like "A-1001".',
    }
    assert props["include_items"]["default"] is False
    assert spec.input_schema["additionalProperties"] is False
    assert convert.spec.input_schema["properties"]["unit"]["enum"] == ["km", "mi"]


def test_metadata() -> None:
    assert lookup_order.qualified_name == "orders.lookup_order"
    assert lookup_order.risk == "external"
    assert convert.risk == "read"


async def test_invoke_validates_and_renders() -> None:
    assert await lookup_order.invoke({"order_id": "A-1", "include_items": True}) == (
        '{"id": "A-1", "items": ["book"]}'
    )
    assert await convert.invoke({"amount": "10", "unit": "km"}) == "6.2"
    with pytest.raises(ValueError, match="invalid arguments for convert"):
        await convert.invoke({"amount": 1, "unit": "parsecs"})
    with pytest.raises(ValueError, match="invalid arguments"):
        await convert.invoke({"amount": 1, "unit": "km", "extra": True})


def test_decorated_function_stays_callable() -> None:
    assert convert(1, "mi") == "1.6"


def test_bad_definitions_fail_early() -> None:
    with pytest.raises(ToolDefinitionError, match="needs a type hint"):

        @tool
        def untyped(x):  # type: ignore[no-untyped-def]
            return x

    with pytest.raises(ToolDefinitionError, match="must match"):
        tool(name="has spaces")(convert.fn)


def test_registry_filters_by_name_and_namespace_glob() -> None:
    registry = ToolRegistry([lookup_order, convert])
    assert [s.name for s in registry.specs()] == ["convert", "lookup_order"]
    assert [t.name for t in registry.filtered(["orders.*"], [])] == ["lookup_order"]
    assert [t.name for t in registry.filtered(None, ["convert"])] == ["lookup_order"]


def test_registry_rejects_duplicate_names() -> None:
    @tool(name="convert")
    def convert_currency(amount: float) -> float:
        """Convert money."""
        return amount

    other = convert_currency
    with pytest.raises(ToolDefinitionError, match="two tools are named 'convert'"):
        ToolRegistry([convert, other])
