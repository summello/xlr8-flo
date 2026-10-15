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
}


def _is_bool(value: JsonValue) -> bool:
    return isinstance(value, bool)


# Money policy reads these values, so a truthy string such as "false" must never be storable.
SETTING_VALIDATORS: dict[str, Callable[[JsonValue], bool]] = {
    "approval.max_depth": lambda value: type(value) is int and 1 <= value <= 50,
    "approval.separation_of_duty": _is_bool,
    "requisition.expiry_days": lambda value: (
        value is None or (type(value) is int and 1 <= value <= 3650)
    ),
    "allow_negative_budget": _is_bool,
    "budget.evidence_required": _is_bool,
    "funding_mode": lambda value: value in ("roll_down", "roll_up"),
}
