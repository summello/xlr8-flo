"""The purchase order's implementation of the frozen document-adapter protocol (E13-S04)."""

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget as Target
from flo.kernel.money import Money
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import (
    DocumentSnapshot,
    DocumentSummary,
    FieldType,
    Outcome,
    OutcomeContext,
)
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.purchasing.service import PurchaseOrderService

type Conn = psycopg.Connection[tuple[object, ...]]


class PoAdapter:
    document_type = "po"
    approve_permission = "purchase_order.approve"
    read_permission = "purchase_order.view"
    accepts_project_assignment = False  # the PO inherits its project from the requisition

    def fields(self) -> Mapping[str, FieldType]:
        return {
            "total": "money",
            "budget_total_project": "money",
            "vendor_id": "code",
            "unit_id": "code",
        }

    def snapshot(self, conn: Conn, scope: Scope, document_id: UUID) -> DocumentSnapshot:
        service = PurchaseOrderService(conn, scope)
        po = service.get(document_id)
        return DocumentSnapshot(
            po.version,
            service.creator(document_id),
            cast(
                Target,
                AuthorizationTarget(ScopeType.BU, po.unit_id, "purchase_order", po.id),
            ),
            {
                "total": Money(Decimal(po.total), po.currency),
                "budget_total_project": Money(
                    Decimal(po.budget_total_project), po.converted_currency
                ),
                "vendor_id": str(po.vendor_id),
                "unit_id": str(po.unit_id),
            },
            {
                "total": po.total,
                "lines": ";".join(f"{x.line_no}:{x.quantity}:{x.unit_price}" for x in po.lines),
                "evidence": json.dumps(service.evidence(po), sort_keys=True, default=str),
            },
        )

    def summaries(
        self, conn: Conn, scope: Scope, document_ids: Sequence[UUID]
    ) -> Mapping[UUID, DocumentSummary]:
        service = PurchaseOrderService(conn, scope)
        out = {}
        for id in document_ids:
            po = service.get(id)
            out[id] = DocumentSummary(
                po.number,
                f"Purchase order {po.number}",
                Money(Decimal(po.total), po.currency),
                service.creator(id),
                f"/purchase-orders/{id}",
                None,
            )
        return out

    def on_outcome(
        self,
        conn: Conn,
        scope: Scope,
        document_id: UUID,
        outcome: Outcome,
        context: OutcomeContext,
    ) -> None:
        PurchaseOrderService(conn, scope, context.actor_id).settle(document_id, outcome, context)
