"""Built-in defaults; the nearest override replaces the entire value."""

from collections.abc import Callable

from pydantic import JsonValue

SETTING_DEFAULTS: dict[str, JsonValue] = {
    "allow_negative_budget": False,
    "funding_mode": "roll_down",
    "fiscal_year_start_month": 1,
}


def _is_bool(value: JsonValue) -> bool:
    return isinstance(value, bool)


def _is_month(value: JsonValue) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 12


# Money policy reads these values, so a truthy string such as "false" must never be storable.
SETTING_VALIDATORS: dict[str, Callable[[JsonValue], bool]] = {
    "allow_negative_budget": _is_bool,
    "funding_mode": lambda value: value in ("roll_down", "roll_up"),
    "fiscal_year_start_month": _is_month,
}
