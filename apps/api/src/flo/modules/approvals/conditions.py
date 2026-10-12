"""Pure condition evaluation and document attribute catalogue hooks."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import psycopg

from flo.kernel.money import Money
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import (
    AllCondition,
    AnyCondition,
    AttributeValue,
    Condition,
    FieldType,
    LeafCondition,
    MoneyValue,
    NotCondition,
)


@dataclass(frozen=True)
class LeafTrace:
    field: str
    op: str
    expected: str
    actual: str
    result: bool


@dataclass(frozen=True)
class Evaluation:
    result: bool
    trace: tuple[LeafTrace, ...]


class ConditionError(ValueError):
    def __init__(self, code: str, field: str, expected_currency: str, actual_currency: str) -> None:
        self.code = code
        self.field = field
        self.expected_currency = expected_currency
        self.actual_currency = actual_currency
        super().__init__(code, field, expected_currency, actual_currency)


class DocumentAdapter(Protocol):
    document_type: str
    approve_permission: str

    def fields(self) -> Mapping[str, FieldType]: ...


class AttributeProvider(Protocol):
    prefix: str

    def fields(self, document_type: str) -> Mapping[str, FieldType]: ...

    def values(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_type: str,
        document_id: UUID,
    ) -> Mapping[str, AttributeValue]: ...


_adapters: dict[str, DocumentAdapter] = {}
_providers: dict[str, AttributeProvider] = {}


def register_document_type(adapter: DocumentAdapter) -> None:
    _adapters[adapter.document_type] = adapter


def approve_permission(document_type: str) -> str:
    """Use the registered document contract; never invent a generic permission."""
    adapter = _adapters.get(document_type)
    if adapter is None:
        from flo.modules.approvals.schemas import invalid

        raise invalid(
            "/document_type",
            f"Approval integration for {document_type} is not available yet. "
            "Approver selection and users-rule workflow validation or publishing require "
            "a registered document adapter. Contact your operator to enable this integration.",
        )
    return adapter.approve_permission


def register_attribute_provider(provider: AttributeProvider) -> None:
    _providers[provider.prefix] = provider


def field_catalog(document_type: str) -> Mapping[str, FieldType]:
    adapter = _adapters.get(document_type)
    fields = dict(adapter.fields()) if adapter else {}
    # Custom namespaces are owned solely by providers.
    fields = {name: kind for name, kind in fields.items() if not name.startswith("custom.")}
    for provider in _providers.values():
        fields.update(
            {
                name: kind
                for name, kind in provider.fields(document_type).items()
                if name.startswith("custom.") and name.startswith(provider.prefix)
            }
        )
    return fields


def render(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, Money):
        return f"{value.amount} {value.currency}"
    if isinstance(value, MoneyValue):
        return f"{value.amount} {value.currency}"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (tuple, list)):
        return ", ".join(str(item) for item in value)
    return str(value)


def compare(leaf: LeafCondition, actual: AttributeValue) -> bool:
    op, expected = leaf.op, leaf.value
    if actual is None:
        return op in {"is_null", "ne"}
    if op in {"is_null", "not_null"}:
        return op == "not_null"
    if op in {"in", "not_in"}:
        found = actual in expected if isinstance(expected, list) else False
        return found if op == "in" else not found
    if op == "contains":
        if isinstance(actual, str) and isinstance(expected, str):
            return expected.casefold() in actual.casefold()
        return isinstance(actual, tuple) and expected in actual
    left: str | Decimal | date | bool
    right: str | Decimal | date | bool
    if isinstance(actual, Money) and isinstance(expected, MoneyValue):
        if actual.currency != expected.currency:
            raise ConditionError(
                "currency_mismatch", leaf.field, expected.currency, actual.currency
            )
        left, right = actual.amount, Decimal(expected.amount)
    elif isinstance(actual, Decimal) and isinstance(expected, str):
        if re.fullmatch(r"-?[0-9]{1,14}(\.[0-9]{1,4})?", expected) is None:
            raise ValueError("Decimal values must be fixed precision amount strings")
        left, right = actual, Decimal(expected)
    elif isinstance(actual, date) and isinstance(expected, str):
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expected) is None:
            raise ValueError("Date values must be ISO YYYY-MM-DD")
        left, right = actual, date.fromisoformat(expected)
    elif isinstance(actual, (str, bool)) and isinstance(expected, (str, bool)):
        left, right = actual, expected
    else:
        raise TypeError("Condition value must match the attribute type")
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if type(left) is not type(right):
        raise TypeError("Ordered values must have the same type")
    if op == "lt":
        return left < right  # type: ignore[operator]
    if op == "le":
        return left <= right  # type: ignore[operator]
    if op == "gt":
        return left > right  # type: ignore[operator]
    return left >= right  # type: ignore[operator]


def evaluate(expression: Condition, attributes: Mapping[str, AttributeValue]) -> Evaluation:
    normalized: dict[str, AttributeValue] = {}
    for key, value in attributes.items():
        if isinstance(value, float):
            raise TypeError("Float attributes are forbidden")
        normalized[key] = (
            Decimal(value) if isinstance(value, int) and not isinstance(value, bool) else value
        )
    trace: list[LeafTrace] = []

    def visit(node: Condition) -> bool:
        if isinstance(node, AllCondition):
            return all(visit(child) for child in node.all)
        if isinstance(node, AnyCondition):
            return any(visit(child) for child in node.any)
        if isinstance(node, NotCondition):
            return not visit(node.not_)
        actual = normalized.get(node.field)
        result = compare(node, actual)
        trace.append(LeafTrace(node.field, node.op, render(node.value), render(actual), result))
        return result

    return Evaluation(visit(expression), tuple(trace))
