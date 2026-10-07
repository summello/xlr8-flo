"""Allow-list construction of JSON-safe audit snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import cast
from uuid import UUID

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


class AuditFieldMissing(KeyError):
    """Raised when a declared audit field disappeared from its source model."""


def _source_values(source: object) -> Mapping[str, object]:
    if isinstance(source, Mapping):
        return cast(Mapping[str, object], source)
    if is_dataclass(source) and not isinstance(source, type):
        return {field.name: getattr(source, field.name) for field in fields(source)}
    try:
        values = vars(source)
    except TypeError as exc:
        raise TypeError("audit snapshots require a mapping, dataclass, or model object") from exc
    return cast(Mapping[str, object], values)


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("audit snapshot datetimes must be timezone-aware")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError("audit snapshot object keys must be strings")
            normalized[key] = _json_value(nested)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported audit snapshot value: {type(value).__name__}")


def allowlisted_snapshot(source: object, fields_to_include: Sequence[str]) -> dict[str, JsonValue]:
    """Return only explicitly named fields, failing if a named field is missing.

    Failing on a missing declared field keeps model refactors from silently turning
    audit coverage into a test that passes by absence.
    """

    values = _source_values(source)
    snapshot: dict[str, JsonValue] = {}
    for field_name in fields_to_include:
        if field_name not in values:
            raise AuditFieldMissing(field_name)
        snapshot[field_name] = _json_value(values[field_name])
    return snapshot
