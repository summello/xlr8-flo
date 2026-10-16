"""Daily vendor-document expiry notices through the outbox (VEN-011, OBS-004)."""

import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import PermissionHoldersResolver
from flo.kernel.jobs import Job, JobQueue
from flo.kernel.jobs.queue import JobConnection
from flo.kernel.logging import correlation_context
from flo.kernel.outbox.store import OutboxConnection, OutboxStore
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    AuthorizationTarget,
    IdentityAuthorizationConnection,
    ScopeType,
    emails_by_id,
)
from flo.modules.org.service import today
from flo.modules.vendors.documents import EXPIRING_DAYS, KIND
from flo.modules.vendors.models import VendorRepository

Connection = psycopg.Connection[tuple[object, ...]]
JOB_KIND = "vendor-document-expiry"
ROLE = "vendor-manager"
_logger = logging.getLogger(__name__)


def expired_required_count(conn: Connection, scope: Scope, on: date) -> int:
    """Expired documents of required types: the business-monitor figure (OBS-004)."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = (
            VendorRepository(conn, scope)
            .execute(
                "SELECT count(*) FROM vendor_document d WHERE d.org_id = %(org_id)s "
                "AND d.expiry_date < %(on)s AND EXISTS (SELECT 1 FROM master_record m "
                "WHERE m.org_id = d.org_id AND m.kind = %(kind)s AND m.code = d.type_code "
                "AND m.active AND m.attributes @> '{\"required\": true}'::jsonb)",
                {"on": on, "kind": KIND},
            )
            .fetchone()
        )
    return cast(int, row[0]) if row else 0


def run_org(conn: Connection, scope: Scope, *, on: date | None = None) -> int:
    """Notify each newly expiring or expired document once; returns digests enqueued."""
    on = on or today()
    digests = 0
    with correlation_context(JOB_KIND), tenant_transaction(cast(RlsSession, conn), scope):
        repo = VendorRepository(conn, scope)
        # Serialize runs for this organization so a document is stamped by exactly one.
        repo.execute("SELECT pg_advisory_xact_lock(hashtextextended(%(org_id)s::text, 9))")
        resolver = AuthorizationResolver(cast(AuthorizationConnection, conn), scope)
        rows = repo.execute(
            "SELECT d.id, d.vendor_id, v.number, v.trading_name, v.availability, d.type_code, "
            "d.expiry_date, d.notified_expiring_at IS NOT NULL FROM vendor_document d "
            "JOIN vendor v ON v.org_id = d.org_id AND v.id = d.vendor_id "
            "WHERE d.org_id = %(org_id)s AND d.expiry_date <= %(on)s::date + %(days)s "
            "AND d.notified_expired_at IS NULL ORDER BY d.expiry_date, d.id FOR UPDATE OF d",
            {"on": on, "days": EXPIRING_DAYS},
        ).fetchall()
        holders: dict[UUID, set[UUID]] = {}
        per_user: dict[UUID, list[dict[str, object]]] = defaultdict(list)
        expiring: list[UUID] = []
        expired: list[UUID] = []
        for id, vendor_id, number, name, availability, type_code, expiry, was_expiring in rows:
            is_expired = cast(date, expiry) < on
            if not is_expired and was_expiring:
                continue
            if vendor_id not in holders:
                targets = [cast(TargetProtocol, AuthorizationTarget.organization(scope.org_id))]
                if availability == "restricted":
                    units = repo.execute(
                        "SELECT unit_id FROM vendor_unit WHERE org_id = %(org_id)s "
                        "AND vendor_id = %(id)s",
                        {"id": vendor_id},
                    ).fetchall()
                    targets = [
                        cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, cast(UUID, u[0])))
                        for u in units
                    ]
                holders[cast(UUID, vendor_id)] = {
                    h
                    for t in targets
                    for h in cast(PermissionHoldersResolver, resolver).role_holders(ROLE, t)
                }
            recipients = holders[cast(UUID, vendor_id)]
            if not recipients:
                continue  # left unstamped: noticed once a vendor manager exists
            (expired if is_expired else expiring).append(cast(UUID, id))
            item: dict[str, object] = {
                "vendor_number": number,
                "vendor_name": name,
                "type_code": type_code,
                "threshold": "expired" if is_expired else "expiring",
                "expiry_date": cast(date, expiry).isoformat(),
            }
            for user in recipients:
                per_user[user].append(item)
        emails = emails_by_id(cast(IdentityAuthorizationConnection, conn), list(per_user))
        outbox = OutboxStore(cast(OutboxConnection, conn), scope)
        for user, items in per_user.items():
            recipient = emails.get(user)
            if recipient is None:
                continue
            prefix = f"vendor-document-expiry:{user}:{on.isoformat()}"
            earlier = repo.execute(
                "SELECT count(*) FROM outbox WHERE org_id = %(org_id)s "
                "AND idempotency_key LIKE %(like)s",
                {"like": prefix + ":%"},
            ).fetchone()
            outbox.add_email(
                to=recipient,
                template="vendor-document-expiry-v1",
                context={"items": items},
                idempotency_key=f"{prefix}:{(cast(int, earlier[0]) if earlier else 0) + 1}",
            )
            digests += 1
        if expiring or expired:
            repo.execute(
                "UPDATE vendor_document SET "
                "notified_expiring_at = coalesce(notified_expiring_at, now()), "
                "notified_expired_at = CASE WHEN id = ANY(%(expired)s) THEN now() "
                "ELSE notified_expired_at END WHERE org_id = %(org_id)s AND id = ANY(%(ids)s)",
                {"expired": expired, "ids": expiring + expired},
            )
    count = expired_required_count(conn, scope, on)
    _logger.info("vendor documents expired_required=%d digests=%d", count, digests)
    return digests


def enqueue_all(conn: Connection) -> int:
    """Enumerate tenant ids and queue one independent, retryable job per organization."""
    with conn.transaction():
        orgs = conn.execute(
            "SELECT DISTINCT org_id FROM identity_membership ORDER BY org_id"
        ).fetchall()
    for row in orgs:
        scope = Scope(cast(UUID, row[0]))
        with tenant_transaction(cast(RlsSession, conn), scope):
            JobQueue(cast(JobConnection, conn), scope).enqueue(JOB_KIND, {})
    return len(orgs)


def job_handler(connect: Callable[[], Connection]) -> Callable[[Job], None]:
    """Run on an independent connection while the runner owns its queue transaction."""

    def handle(job: Job) -> None:
        with connect() as conn:
            run_org(conn, Scope(job.org_id))

    return handle
