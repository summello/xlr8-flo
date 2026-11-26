"""Built-in defaults; the nearest override replaces the entire value."""

from collections.abc import Callable

from pydantic import JsonValue

SETTING_DEFAULTS: dict[str, JsonValue] = {
    "allow_negative_budget": False,
    "budget.evidence_required": False,
    "funding_mode": "roll_down",
    "approval.max_depth": 10,
    "approval.separation_of_duty": True,
    "requisition.expiry_days": None,
    "vendor.allow_local_disable": False,
    "sourcing.default_criteria": None,
    "sourcing.require_non_lowest_justification": True,
    "tax.consumes_budget": True,
    "freight.consumes_budget": True,
    "po.letterhead_address": None,
    "po.standard_terms": None,
}


# A value validator never sees the scope, so the service refuses these keys with a unit.
ORG_ONLY_SETTINGS = frozenset(
    {"sourcing.default_criteria", "po.letterhead_address", "po.standard_terms"}
)


def _is_bool(value: JsonValue) -> bool:
    return isinstance(value, bool)


def valid_default_criteria(value: JsonValue) -> bool:
    """Decision 222: 1..20 unique-named `{name, weight}` items, weights 1..100 totalling 100."""
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        return False
    names, total = set(), 0
    for item in value:
        if not isinstance(item, dict) or item.keys() != {"name", "weight"}:
            return False
        name, weight = item["name"], item["weight"]
        if not isinstance(name, str) or not 1 <= len(name) <= 120 or name in names:
            return False
        if type(weight) is not int or not 1 <= weight <= 100:
            return False
        names.add(name)
        total += weight
    return total == 100


def _text(limit: int, lines: int | None = None) -> Callable[[JsonValue], bool]:
    """`None`, or a non-blank string of at most `limit` characters (and `lines` lines)."""

    def valid(value: JsonValue) -> bool:
        if value is None:
            return True
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            return False
        return lines is None or len(value.splitlines()) <= lines

    return valid


# Money policy reads these values, so a truthy string such as "false" must never be storable.
SETTING_VALIDATORS: dict[str, Callable[[JsonValue], bool]] = {
    "approval.max_depth": lambda value: type(value) is int and 1 <= value <= 50,
    "approval.separation_of_duty": _is_bool,
    "requisition.expiry_days": lambda value: (
        value is None or (type(value) is int and 1 <= value <= 3650)
    ),
    "allow_negative_budget": _is_bool,
    "budget.evidence_required": _is_bool,
    "vendor.allow_local_disable": _is_bool,
    "sourcing.default_criteria": valid_default_criteria,
    "sourcing.require_non_lowest_justification": _is_bool,
    "tax.consumes_budget": _is_bool,
    "freight.consumes_budget": _is_bool,
    "po.letterhead_address": _text(600, 8),
    "po.standard_terms": _text(4000),
    "funding_mode": lambda value: value in ("roll_down", "roll_up"),
}
