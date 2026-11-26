"""Vendor approval documents: a `vendor_change` is what goes through the workflow (VEN-005)."""

import json
from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.schemas import (
    DocumentSnapshot,
    DocumentSummary,
    FieldType,
    Outcome,
    OutcomeContext,
)
from flo.modules.approvals.service import register_document_type
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.vendors.models import VendorRepository

TITLES = {
    "create": "New vendor",
    "availability": "Vendor availability change",
    "suspend": "Vendor suspension",
    "reactivate": "Vendor reactivation",
}


class VendorChangeAdapter:
    document_type = "vendor"
    approve_permission = "vendor.approve"
    read_permission = "vendor.view"
    accepts_project_assignment = False

    def fields(self) -> Mapping[str, FieldType]:
        return {}

    def _row(
        self, conn: psycopg.Connection[tuple[object, ...]], scope: Scope, id: UUID
    ) -> tuple[object, ...]:
        row = (
            VendorRepository(conn, scope)
            .execute(
                "SELECT c.vendor_id, c.kind, c.payload, c.reason, c.requested_by, v.availability, "
                "v.number, v.trading_name FROM vendor_change c JOIN vendor v "
                "ON v.org_id = c.org_id AND v.id = c.vendor_id "
                "WHERE c.org_id = %(org_id)s AND c.id = %(id)s",
                {"id": id},
            )
            .fetchone()
        )
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return row

    def snapshot(
        self, conn: psycopg.Connection[tuple[object, ...]], scope: Scope, document_id: UUID
    ) -> DocumentSnapshot:
        from flo.modules.vendors.service import VendorService

        row = self._row(conn, scope, document_id)
        # The workflow resolves in the organization (global vendor) or the vendor's first unit.
        target = AuthorizationTarget.organization(scope.org_id)
        if row[5] == "restricted":
            unit = (
                VendorRepository(conn, scope)
                .execute(
                    "SELECT u.id FROM vendor_unit vu JOIN org_unit u "
                    "ON u.org_id = vu.org_id AND u.id = vu.unit_id "
                    "WHERE vu.org_id = %(org_id)s AND vu.vendor_id = %(id)s "
                    "ORDER BY u.code LIMIT 1",
                    {"id": row[0]},
                )
                .fetchone()
            )
            if unit is not None:
                unit_id = cast(UUID, unit[0])
                target = AuthorizationTarget(ScopeType.BU, unit_id, "org_unit", unit_id)
        return DocumentSnapshot(
            1,
            cast(UUID, row[4]),
            cast(TargetProtocol, target),
            {},
            {
                "kind": str(row[1]),
                "reason": str(row[3] or ""),
                # the request's payload, score, duplicate result, documents and contacts
                "evidence": json.dumps(
                    VendorService(conn, scope).evidence(document_id), sort_keys=True, default=str
                ),
            },
        )

    def summaries(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_ids: Sequence[UUID],
    ) -> Mapping[UUID, DocumentSummary]:
        result = {}
        for id in document_ids:
            row = self._row(conn, scope, id)
            result[id] = DocumentSummary(
                str(row[6]),
                f"{TITLES[str(row[1])]}: {row[7]}",
                None,
                cast(UUID, row[4]),
                f"/vendors/{row[0]}",
                cast(str | None, row[3]),
            )
        return result

    def on_outcome(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_id: UUID,
        outcome: Outcome,
        context: OutcomeContext,
    ) -> None:
        from flo.modules.vendors.service import VendorService

        with tenant_transaction(cast(RlsSession, conn), scope):
            VendorService(conn, scope, context.actor_id).apply_outcome(document_id, outcome)


register_document_type(VendorChangeAdapter())
