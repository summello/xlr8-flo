"""Monthly partition lifecycle for the Postgres audit log."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from psycopg import sql


class PartitionResult(Protocol):
    """Result surface needed for the partition existence check."""

    def fetchone(self) -> object | None: ...


class PartitionConnection(Protocol):
    """Database surface needed to manage audit partitions transactionally."""

    def execute(self, query: str | sql.Composed) -> PartitionResult: ...


@dataclass(frozen=True, slots=True)
class AuditPartition:
    """One UTC calendar month and its deterministic table name."""

    name: str
    start: datetime
    end: datetime


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("audit partition timestamps must be timezone-aware")
    return value.astimezone(UTC)


def monthly_partition(value: datetime) -> AuditPartition:
    """Resolve the UTC calendar partition containing ``value``."""

    instant = _aware_utc(value)
    start = datetime(instant.year, instant.month, 1, tzinfo=UTC)
    if instant.month == 12:
        end = datetime(instant.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(instant.year, instant.month + 1, 1, tzinfo=UTC)
    return AuditPartition(f"audit_log_{start:%Y_%m}", start, end)


def ensure_monthly_partition(
    connection: PartitionConnection, value: datetime
) -> AuditPartition:
    """Create and tenant-protect the target month before an audit insert."""

    partition = monthly_partition(value)
    identifier = sql.Identifier(partition.name)
    attached = connection.execute(
        sql.SQL(
            "SELECT 1 FROM pg_catalog.pg_inherits "
            "WHERE inhparent = 'audit_log'::regclass "
            "AND inhrelid = to_regclass({})"
        ).format(sql.Literal(f"public.{partition.name}"))
    ).fetchone()
    if attached is not None:
        return partition

    connection.execute(
        sql.SQL(
            "CREATE TABLE IF NOT EXISTS {} PARTITION OF audit_log "
            "FOR VALUES FROM ({}) TO ({})"
        ).format(identifier, sql.Literal(partition.start), sql.Literal(partition.end))
    )
    connection.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(identifier))
    connection.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(identifier))
    connection.execute(
        sql.SQL(
            "DO $audit_policy$ BEGIN "
            "IF NOT EXISTS ("
            "SELECT 1 FROM pg_catalog.pg_policy "
            "WHERE polrelid = {}::regclass AND polname = 'tenant_isolation'"
            ") THEN "
            "CREATE POLICY tenant_isolation ON {} "
            "USING (org_id = current_setting('app.org_id')::uuid) "
            "WITH CHECK (org_id = current_setting('app.org_id')::uuid); "
            "END IF; END $audit_policy$"
        ).format(sql.Literal(f"public.{partition.name}"), identifier)
    )
    connection.execute(
        sql.SQL("REVOKE UPDATE, DELETE ON TABLE {} FROM PUBLIC").format(identifier)
    )
    return partition


def detach_monthly_partition(
    connection: PartitionConnection, value: datetime
) -> AuditPartition:
    """Detach an already archived month without dropping its audit rows."""

    partition = monthly_partition(value)
    identifier = sql.Identifier(partition.name)
    # A detached archive is read-only and must not retain a dependency on the
    # live parent's id sequence, or the parent cannot be cleanly downgraded.
    connection.execute(
        sql.SQL("ALTER TABLE {} ALTER COLUMN id DROP DEFAULT").format(identifier)
    )
    connection.execute(
        sql.SQL("ALTER TABLE audit_log DETACH PARTITION {}").format(
            identifier
        )
    )
    return partition
