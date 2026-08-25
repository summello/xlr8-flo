"""Tenant-scoped Postgres storage for idempotent request outcomes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Protocol, cast
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession

IDEMPOTENCY_TTL = timedelta(hours=24)

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type DatabaseRow = Sequence[object] | Mapping[str, object]


class QueryResult(Protocol):
    """Small result surface used by the idempotency store."""

    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> DatabaseRow | None: ...


class IdempotencyConnection(RlsSession, Protocol):
    """One connection shared by the claim, command, and completion write."""

    def execute(
        self,
        query: str | sql.Composed,
        params: Mapping[str, object] | None = None,
    ) -> QueryResult: ...


class ClaimKind(StrEnum):
    """All possible outcomes of trying to claim an organization key."""

    CLAIMED = "claimed"
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    REUSED = "reused"


@dataclass(frozen=True, slots=True)
class StoredResponse:
    """The successful response replayed for a completed request."""

    status_code: int
    body: JsonValue
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Claim classification with a response only for completed requests."""

    kind: ClaimKind
    response: StoredResponse | None = None


def _value(row: DatabaseRow, index: int, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]


def _advisory_lock_id(org_id: UUID, key: str) -> int:
    digest = sha256(f"{org_id}\0{key}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class IdempotencyStore(ScopedRepo[StoredResponse]):
    """Claim and complete keys without escaping the active tenant transaction."""

    def __init__(self, connection: IdempotencyConnection, scope: Scope) -> None:
        super().__init__(connection, scope)
        self._connection = connection

    def claim(self, key: str, endpoint: str, request_hash: str) -> ClaimResult:
        """Claim a key or classify the tenant's existing request atomically."""

        existing = self._existing(key)
        if existing is not None:
            return self._classify(existing, endpoint, request_hash)

        lock_row = self._connection.execute(
            "SELECT pg_try_advisory_xact_lock(%(lock_id)s)",
            {"lock_id": _advisory_lock_id(self.scope.org_id, key)},
        ).fetchone()
        if lock_row is None or _value(lock_row, 0, "pg_try_advisory_xact_lock") is not True:
            return ClaimResult(ClaimKind.IN_PROGRESS)

        inserted = self._connection.execute(
            """
            INSERT INTO idempotency_key
                (org_id, key, endpoint, request_hash, state)
            VALUES
                (%(org_id)s, %(key)s, %(endpoint)s, %(request_hash)s, 'in_progress')
            ON CONFLICT DO NOTHING
            """,
            self.scoped_params({"key": key, "endpoint": endpoint, "request_hash": request_hash}),
        )
        if inserted.rowcount == 1:
            return ClaimResult(ClaimKind.CLAIMED)

        existing = self._existing(key)
        if existing is None:
            raise RuntimeError("idempotency claim conflict did not leave a visible row")
        return self._classify(existing, endpoint, request_hash)

    def complete(
        self,
        key: str,
        *,
        status_code: int,
        serialized_response_body: str,
        response_headers: Mapping[str, str],
    ) -> None:
        """Store the exact successful outcome before the business transaction commits."""

        updated = self._connection.execute(
            """
            UPDATE idempotency_key
            SET state = 'completed',
                status_code = %(status_code)s,
                response_body = %(response_body)s::jsonb,
                response_headers = %(response_headers)s,
                completed_at = clock_timestamp()
            WHERE org_id = %(org_id)s AND key = %(key)s AND state = 'in_progress'
            """,
            self.scoped_params(
                {
                    "key": key,
                    "status_code": status_code,
                    "response_body": serialized_response_body,
                    "response_headers": Jsonb(dict(response_headers)),
                }
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeError("idempotency completion did not update its claimed row")

    def delete_expired(self, now: datetime) -> int:
        """Delete this tenant's keys older than the fixed 24-hour retention window."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("idempotency cleanup time must be timezone-aware")
        deleted = self._connection.execute(
            "DELETE FROM idempotency_key WHERE org_id = %(org_id)s AND created_at < %(cutoff)s",
            self.scoped_params({"cutoff": now - IDEMPOTENCY_TTL}),
        )
        return deleted.rowcount

    def _existing(self, key: str) -> DatabaseRow | None:
        return self._connection.execute(
            """
            SELECT endpoint, request_hash, state, status_code, response_body,
                   response_headers
            FROM idempotency_key
            WHERE org_id = %(org_id)s AND key = %(key)s
            """,
            self.scoped_params({"key": key}),
        ).fetchone()

    @staticmethod
    def _classify(
        row: DatabaseRow,
        endpoint: str,
        request_hash: str,
    ) -> ClaimResult:
        stored_endpoint = cast(str, _value(row, 0, "endpoint"))
        stored_hash = cast(str, _value(row, 1, "request_hash"))
        state = cast(str, _value(row, 2, "state"))
        if state == "in_progress":
            return ClaimResult(ClaimKind.IN_PROGRESS)
        if state != "completed":
            raise RuntimeError("idempotency row has an unsupported state")
        if stored_endpoint != endpoint or stored_hash != request_hash:
            return ClaimResult(ClaimKind.REUSED)

        status_code = _value(row, 3, "status_code")
        if not isinstance(status_code, int):
            raise RuntimeError("completed idempotency row has no status code")
        body = cast(JsonValue, _value(row, 4, "response_body"))
        headers_value = _value(row, 5, "response_headers")
        if not isinstance(headers_value, dict) or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in headers_value.items()
        ):
            raise RuntimeError("completed idempotency row has invalid response headers")
        headers = cast(dict[str, str], headers_value)
        return ClaimResult(
            ClaimKind.COMPLETED,
            StoredResponse(status_code=status_code, body=body, headers=headers),
        )
