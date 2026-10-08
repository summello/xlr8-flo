"""Module-owned guards consulted by the settings writer inside its transaction."""

from collections.abc import Callable
from uuid import UUID

import psycopg
from pydantic import JsonValue

from flo.kernel.tenancy.context import Scope

type SettingGuard = Callable[
    [psycopg.Connection[tuple[object, ...]], Scope, UUID | None, JsonValue | None], None
]

_GUARDS: dict[str, SettingGuard] = {}


def register(key: str, guard: SettingGuard) -> None:
    _GUARDS[key] = guard


def check(
    key: str,
    connection: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    unit_id: UUID | None,
    value: JsonValue | None,
) -> None:
    if guard := _GUARDS.get(key):
        guard(connection, scope, unit_id, value)
