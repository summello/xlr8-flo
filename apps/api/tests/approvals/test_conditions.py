"""Pure condition contract tests; adapters/providers here never access a database."""

from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from flo.kernel.errors import ProblemError
from flo.kernel.money import Money
from flo.modules.approvals import conditions
from flo.modules.approvals.schemas import AttributeValue, FieldType, parse_condition
from flo.modules.approvals.service import (
    ConditionError,
    evaluate,
    field_catalog,
    register_attribute_provider,
    register_document_type,
)


def leaf(op: str, value: object = None, field: str = "test") -> dict[str, object]:
    result: dict[str, object] = {"field": field, "op": op}
    if op not in {"is_null", "not_null"}:
        result["value"] = value
    return result


SAMPLES = [
    ("text", "Alpha", "Beta", "Alpha"),
    ("code", "ENG", "OPS", "ENG"),
    ("decimal", Decimal("2"), Decimal("1"), "2.0000"),
    ("date", date(2026, 1, 2), date(2026, 1, 1), "2026-01-02"),
    ("boolean", True, False, True),
    (
        "money",
        Money(Decimal("2"), "USD"),
        Money(Decimal("1"), "USD"),
        {"amount": "2.0000", "currency": "USD"},
    ),
]
CASES = []
for kind, same, different, expected in SAMPLES:
    for op in ("eq", "ne"):
        CASES.extend(
            [(kind, op, same, expected, op == "eq"), (kind, op, different, expected, op == "ne")]
        )
    if kind in {"decimal", "date", "money"}:
        for op in ("lt", "le", "gt", "ge"):
            CASES.extend(
                [
                    (kind, op, different, expected, op in {"lt", "le"}),
                    (kind, op, same, expected, op in {"le", "ge"}),
                ]
            )
        higher = {
            "decimal": Decimal("3"),
            "date": date(2026, 1, 3),
            "money": Money(Decimal("3"), "USD"),
        }[kind]
        CASES.extend(
            [
                (kind, "gt", higher, expected, True),
                (kind, "ge", higher, expected, True),
                (kind, "le", higher, expected, False),
                (kind, "lt", higher, expected, False),
            ]
        )
    if kind in {"text", "code"}:
        for op in ("in", "not_in"):
            CASES.extend(
                [
                    (kind, op, same, [same], op == "in"),
                    (kind, op, different, [same], op == "not_in"),
                ]
            )
CASES.extend(
    [
        ("text", "contains", "Straße", "STRASSE", True),
        ("text", "contains", "Alpha", "Beta", False),
        ("code_list", "contains", ("ENG", "OPS"), "ENG", True),
        ("code_list", "contains", ("ENG",), "eng", False),
    ]
)
for kind, same, _, _ in SAMPLES + [("code_list", ("ENG",), (), None)]:
    for op in ("is_null", "not_null"):
        CASES.extend(
            [(kind, op, same, None, op == "not_null"), (kind, op, None, None, op == "is_null")]
        )


@pytest.mark.parametrize("kind,op,actual,expected,result", CASES)
def test_operator_type_matrix(kind, op, actual, expected, result):
    class Adapter:
        document_type = "condition-test"

        def fields(self):
            return {"test": kind}

    register_document_type(Adapter())
    assert field_catalog("condition-test")["test"] == kind
    assert evaluate(parse_condition(leaf(op, expected)), {"test": actual}).result is result


@pytest.mark.parametrize(
    "amount,result", [("250000.01", True), ("250000", False), ("99999999999999.9999", True)]
)
def test_exact_money_threshold(amount, result):
    threshold = "99999999999999.9998" if amount.startswith("999") else "250000.00"
    expression = parse_condition(leaf("gt", {"amount": threshold, "currency": "USD"}, "amount"))
    assert evaluate(expression, {"amount": Money(Decimal(amount), "USD")}).result is result


@pytest.mark.parametrize("op", ["eq", "ne", "lt", "le", "gt", "ge"])
def test_currency_mismatch(op):
    with pytest.raises(ConditionError) as error:
        evaluate(
            parse_condition(leaf(op, {"amount": "1", "currency": "USD"})),
            {"test": Money(Decimal("1"), "EUR")},
        )
    assert (
        error.value.code,
        error.value.field,
        error.value.expected_currency,
        error.value.actual_currency,
    ) == ("currency_mismatch", "test", "USD", "EUR")


@pytest.mark.parametrize(
    "value",
    [1, 1.5, {"amount": 1.5, "currency": "USD"}, {"amount": 1, "currency": "USD"}, ["ENG", 1.5]],
)
def test_parser_rejects_numbers(value):
    with pytest.raises(ProblemError):
        parse_condition(leaf("eq", value))


@pytest.mark.parametrize("amount", ["1e2", "NaN", "1.00001", "100000000000000", "+1", ".1"])
def test_money_string_guard(amount):
    with pytest.raises(ProblemError):
        parse_condition(leaf("eq", {"amount": amount, "currency": "USD"}))


def test_float_attribute_rejected_even_when_unused():
    with pytest.raises(TypeError):
        evaluate(
            parse_condition(leaf("eq", "ENG")),
            cast(dict[str, AttributeValue], {"test": "ENG", "unused": 1.5}),
        )


def test_provider_integer_boundary():
    assert evaluate(
        parse_condition(leaf("eq", "2")), cast(dict[str, AttributeValue], {"test": 2})
    ).result
    assert evaluate(parse_condition(leaf("eq", True)), {"test": True}).result


@pytest.mark.parametrize(
    "op", ["eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "contains", "is_null", "not_null"]
)
@pytest.mark.parametrize("attributes", [{}, {"test": None}])
def test_missing_and_null(op, attributes):
    expected = (
        ["ENG"] if op in {"in", "not_in"} else ("1" if op in {"lt", "le", "gt", "ge"} else "ENG")
    )
    assert evaluate(parse_condition(leaf(op, expected)), attributes).result is (
        op in {"is_null", "ne"}
    )


def test_depth_and_leaf_limits():
    expression = leaf("eq", "ENG")
    for _ in range(5):
        expression = {"not": expression}
    parse_condition(expression)
    with pytest.raises(ProblemError) as error:
        parse_condition({"not": expression})
    assert error.value.errors[0].field == "/not/not/not/not/not/not"
    fifty = {
        "all": [
            {"all": [leaf("eq", "ENG") for _ in range(20)]},
            {"all": [leaf("eq", "ENG") for _ in range(20)]},
            {"all": [leaf("eq", "ENG") for _ in range(10)]},
        ]
    }
    parse_condition(fifty)
    fifty["all"][2]["all"].append(leaf("eq", "ENG"))
    with pytest.raises(ProblemError) as error:
        parse_condition(fifty)
    assert error.value.errors[0].field == "/all/2/all/10"


@pytest.mark.parametrize(
    "expression",
    [
        {"all": []},
        {"any": [leaf("eq", "a")] * 21},
        leaf("in", []),
        leaf("in", ["a"] * 101),
        leaf("contains", True),
        {"not": leaf("eq", "a"), "extra": True},
        {"field": "test", "op": "eq"},
        {"field": "test", "op": "is_null", "value": None},
        {"field": "test", "op": "unknown", "value": "a"},
        {"field": "test", "op": "eq", "value": "a", "extra": True},
        leaf("eq", {"amount": "1", "currency": "invalid"}),
        leaf("eq", "a", ""),
        {"field": 1, "op": "eq", "value": "a"},
        {"op": "eq", "value": "a"},
        leaf("eq", {"amount": "1", "currency": "USD", "extra": True}),
    ],
)
def test_grammar_guards(expression):
    with pytest.raises(ProblemError):
        parse_condition(expression)


@pytest.mark.parametrize("group,first,expected", [("all", False, False), ("any", True, True)])
def test_short_circuit_trace(group, first, expected):
    expression = parse_condition({group: [leaf("eq", "ENG"), leaf("eq", "OPS")]})
    result = evaluate(expression, {"test": "ENG" if first else "OTHER"})
    assert result.result is expected
    assert len(result.trace) == 1
    trace = result.trace[0]
    assert (trace.field, trace.op, trace.expected, trace.actual, trace.result) == (
        "test",
        "eq",
        "ENG",
        "ENG" if first else "OTHER",
        first,
    )


def test_nested_trace_order_and_not():
    expression = parse_condition(
        {"all": [leaf("eq", "ENG", "department"), {"not": leaf("eq", "OPS", "department")}]}
    )
    result = evaluate(expression, {"department": "ENG"})
    assert result.result
    assert [entry.result for entry in result.trace] == [True, False]


def test_custom_provider_hook(monkeypatch):
    monkeypatch.setattr(conditions, "_providers", {})
    monkeypatch.setattr(conditions, "_adapters", {})

    class Requisition:
        document_type = "requisition"

        def fields(self) -> dict[str, FieldType]:
            return {"amount": "money"}

    class Provider:
        prefix = "custom."

        def fields(self, document_type) -> dict[str, FieldType]:
            return {"custom.cost_center": "code"}

        def values(self, conn, scope, document_type, document_id):
            return {"custom.cost_center": "ENG"}

    register_document_type(Requisition())
    assert field_catalog("requisition") == {"amount": "money"}
    provider = Provider()
    register_attribute_provider(provider)
    assert field_catalog("requisition") == {"amount": "money", "custom.cost_center": "code"}
    assert evaluate(
        parse_condition(leaf("eq", "ENG", "custom.cost_center")),
        provider.values(None, None, "requisition", None),
    ).result


@pytest.mark.parametrize("value", ["1e2", "NaN", "1.00001", "100000000000000", "2026-02-30"])
def test_ordered_value_guard(value):
    with pytest.raises(ProblemError):
        parse_condition(leaf("gt", value))


@pytest.mark.parametrize("value", ["1e2", "NaN", "1.00001", "100000000000000"])
def test_decimal_attribute_value_guard(value):
    with pytest.raises(ValueError):
        evaluate(parse_condition(leaf("eq", value)), {"test": Decimal("1")})


def test_date_attribute_value_guard():
    with pytest.raises(ValueError):
        evaluate(parse_condition(leaf("eq", "20260101")), {"test": date(2026, 1, 1)})


def test_exact_decimal_scale_and_text_equality():
    assert evaluate(
        parse_condition(leaf("eq", "250000.0000")), {"test": Decimal("250000.00")}
    ).result
    assert not evaluate(parse_condition(leaf("eq", "eng")), {"test": "ENG"}).result


def test_provider_namespace_guard(monkeypatch):
    monkeypatch.setattr(conditions, "_providers", {})
    monkeypatch.setattr(conditions, "_adapters", {})

    class Adapter:
        document_type = "test"

        def fields(self):
            return {"custom.unowned": "code", "amount": "money"}

    class Provider:
        prefix = "custom.mine."

        def fields(self, document_type):
            return {"amount": "text", "custom.other": "text", "custom.mine.code": "code"}

    register_document_type(Adapter())
    register_attribute_provider(Provider())
    assert field_catalog("test") == {"amount": "money", "custom.mine.code": "code"}
