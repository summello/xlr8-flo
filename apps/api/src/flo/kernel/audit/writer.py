"""Transactional writer and permission-gated reader for business audit events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from ipaddress import ip_address, ip_network
from typing import Protocol, cast
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from flo.kernel.audit.partition import ensure_monthly_partition
from flo.kernel.audit.redact import JsonValue, allowlisted_snapshot
from flo.kernel.db.repo import ScopedRepo
from flo.kernel.logging import current_correlation_id
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession

AUDIT_READ_PERMISSION = "audit.read"
type DatabaseRow = Sequence[object] | Mapping[str, object]


class ActorKind(StrEnum):
    """Closed set of principals that can cause an audit event."""

    USER = "user"
    SYSTEM = "system"
    JOB = "job"
    IMPORT = "import"


class Outcome(StrEnum):
    """Closed set of audited action outcomes."""

    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


class CorrelationMissing(RuntimeError):
    """Raised when work has lost the originating request correlation id."""


class QueryResult(Protocol):
    """Result surface shared by psycopg and focused test doubles."""

    def fetchone(self) -> DatabaseRow | None: ...

    def fetchall(self) -> list[DatabaseRow]: ...


class AuditConnection(RlsSession, Protocol):
    """One connection shared with the business change and its audit write."""

    def execute(
        self,
        query: str | sql.Composed,
        params: Mapping[str, object] | None = None,
    ) -> QueryResult: ...


class PermissionChecker(Protocol):
    """Permission boundary supplied by the RBAC kernel from E02-S05."""

    def require(self, permission: str) -> None: ...


@dataclass(frozen=True, slots=True)
class AuditActor:
    """The attributable principal responsible for an event."""

    kind: ActorKind
    id: UUID | None = None

    def __post_init__(self) -> None:
        if self.kind is ActorKind.USER and self.id is None:
            raise ValueError("user audit actors require an actor id")


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Stored audit data returned to an authorized reader."""

    id: int
    org_id: UUID
    bu_id: UUID | None
    occurred_at: datetime
    actor_id: UUID | None
    actor_kind: ActorKind
    action: str
    target_type: str
    target_id: UUID | None
    outcome: Outcome
    reason: str | None
    correlation_id: str
    before: dict[str, JsonValue] | None
    after: dict[str, JsonValue] | None
    ip_prefix: str | None
    user_agent: str | None


def _value(row: DatabaseRow, index: int, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]


def _ip_prefix(address: str | None) -> str | None:
    if address is None:
        return None
    try:
        parsed = ip_address(address)
    except ValueError:
        raise ValueError("audit IP address is invalid") from None
    prefix_length = 24 if parsed.version == 4 else 48
    return str(ip_network((parsed, prefix_length), strict=False))


class AuditWriter(ScopedRepo[AuditRecord]):
    """Append an event on the caller's active business transaction."""

    def __init__(self, connection: AuditConnection, scope: Scope) -> None:
        super().__init__(connection, scope)
        self._connection = connection

    def write(
        self,
        *,
        actor: AuditActor,
        action: str,
        target_type: str,
        outcome: Outcome,
        target_id: UUID | None = None,
        bu_id: UUID | None = None,
        reason: str | None = None,
        before_source: object | None = None,
        before_fields: Sequence[str] = (),
        after_source: object | None = None,
        after_fields: Sequence[str] = (),
        ip_address_value: str | None = None,
        user_agent: str | None = None,
    ) -> AuditRecord:
        """Write exactly one allow-listed event without committing the transaction."""

        correlation_id = current_correlation_id()
        if not correlation_id:
            raise CorrelationMissing("audit writes require a correlation id")
        if not action or not target_type:
            raise ValueError("audit action and target type must be non-empty")
        if before_source is None and before_fields:
            raise ValueError("before fields require a before source")
        if after_source is None and after_fields:
            raise ValueError("after fields require an after source")

        before = (
            allowlisted_snapshot(before_source, before_fields)
            if before_source is not None
            else None
        )
        after = (
            allowlisted_snapshot(after_source, after_fields)
            if after_source is not None
            else None
        )
        occurred = self._connection.execute("SELECT clock_timestamp()").fetchone()
        if occurred is None:
            raise RuntimeError("database clock did not return a timestamp")
        occurred_at = cast(datetime, _value(occurred, 0, "clock_timestamp"))
        ensure_monthly_partition(self._connection, occurred_at)

        stored = self._connection.execute(
            """
            INSERT INTO audit_log (
                org_id, bu_id, occurred_at, actor_id, actor_kind, action,
                target_type, target_id, outcome, reason, correlation_id,
                before, after, ip_prefix, user_agent
            ) VALUES (
                %(org_id)s, %(bu_id)s, %(occurred_at)s, %(actor_id)s, %(actor_kind)s,
                %(action)s, %(target_type)s, %(target_id)s, %(outcome)s, %(reason)s,
                %(correlation_id)s, %(before)s, %(after)s, %(ip_prefix)s, %(user_agent)s
            )
            RETURNING id, org_id, bu_id, occurred_at, actor_id, actor_kind, action,
                      target_type, target_id, outcome, reason, correlation_id,
                      before, after, ip_prefix, user_agent
            """,
            self.scoped_params(
                {
                    "bu_id": bu_id,
                    "occurred_at": occurred_at,
                    "actor_id": actor.id,
                    "actor_kind": actor.kind.value,
                    "action": action,
                    "target_type": target_type,
                    "target_id": target_id,
                    "outcome": outcome.value,
                    "reason": reason,
                    "correlation_id": correlation_id,
                    "before": Jsonb(before) if before is not None else None,
                    "after": Jsonb(after) if after is not None else None,
                    "ip_prefix": _ip_prefix(ip_address_value),
                    "user_agent": user_agent,
                }
            ),
        ).fetchone()
        if stored is None:
            raise RuntimeError("audit insert did not return its stored row")
        return _record(stored)


class AuditReader(ScopedRepo[AuditRecord]):
    """Read tenant audit rows only after RBAC approval, then audit the read."""

    def __init__(
        self,
        connection: AuditConnection,
        scope: Scope,
        permissions: PermissionChecker,
    ) -> None:
        super().__init__(connection, scope)
        self._connection = connection
        self._permissions = permissions
        self._writer = AuditWriter(connection, scope)

    def read(self, *, reader: AuditActor, limit: int = 100) -> list[AuditRecord]:
        """Return recent events and append an event naming the reader."""

        if limit < 1 or limit > 1000:
            raise ValueError("audit read limit must be between 1 and 1000")
        self._permissions.require(AUDIT_READ_PERMISSION)
        rows = self._connection.execute(
            """
            SELECT id, org_id, bu_id, occurred_at, actor_id, actor_kind, action,
                   target_type, target_id, outcome, reason, correlation_id,
                   before, after, ip_prefix, user_agent
            FROM audit_log
            WHERE org_id = %(org_id)s
            ORDER BY occurred_at DESC, id DESC
            LIMIT %(limit)s
            """,
            self.scoped_params({"limit": limit}),
        ).fetchall()
        records = [_record(row) for row in rows]
        self._writer.write(
            actor=reader,
            action="audit.read",
            target_type="audit_log",
            outcome=Outcome.SUCCESS,
            reason=f"Read {len(records)} audit events",
        )
        return records


def _record(row: DatabaseRow) -> AuditRecord:
    before = cast(dict[str, JsonValue] | None, _value(row, 12, "before"))
    after = cast(dict[str, JsonValue] | None, _value(row, 13, "after"))
    return AuditRecord(
        id=cast(int, _value(row, 0, "id")),
        org_id=cast(UUID, _value(row, 1, "org_id")),
        bu_id=cast(UUID | None, _value(row, 2, "bu_id")),
        occurred_at=cast(datetime, _value(row, 3, "occurred_at")),
        actor_id=cast(UUID | None, _value(row, 4, "actor_id")),
        actor_kind=ActorKind(cast(str, _value(row, 5, "actor_kind"))),
        action=cast(str, _value(row, 6, "action")),
        target_type=cast(str, _value(row, 7, "target_type")),
        target_id=cast(UUID | None, _value(row, 8, "target_id")),
        outcome=Outcome(cast(str, _value(row, 9, "outcome"))),
        reason=cast(str | None, _value(row, 10, "reason")),
        correlation_id=cast(str, _value(row, 11, "correlation_id")),
        before=before,
        after=after,
        ip_prefix=cast(str | None, _value(row, 14, "ip_prefix")),
        user_agent=cast(str | None, _value(row, 15, "user_agent")),
    )
