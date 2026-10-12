"""Requisition implementation of the frozen document-adapter protocol."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget as Target
from flo.kernel.errors import conflict
from flo.kernel.money import Money
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import (
    DocumentSnapshot,
    DocumentSummary,
    FieldType,
    Outcome,
    OutcomeContext,
)
from flo.modules.budget.service import lock_balance, reservation_remaining
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.requisitions.funding import release_live
from flo.modules.requisitions.lifecycle import authorize, pending_terminal, read_row, write_status
from flo.modules.requisitions.models import RequisitionRepository


class RequisitionAdapter:
    document_type = "requisition"
    approve_permission = "requisition.approve"
    read_permission = "requisition.view"
    accepts_project_assignment = True

    def fields(self) -> Mapping[str, FieldType]:
        return {"amount": "money"}

    def snapshot(
        self, conn: psycopg.Connection[tuple[object, ...]], scope: Scope, document_id: UUID
    ) -> DocumentSnapshot:
        repo = RequisitionRepository(conn, scope)
        row = read_row(repo, document_id)
        lines = repo.execute(
            "SELECT catalogue_item_id FROM requisition_line WHERE "
            "org_id=%(org_id)s AND requisition_id=%(id)s",
            {"id": document_id},
        ).fetchall()
        return DocumentSnapshot(
            cast(int, row["version"]),
            cast(UUID, row["requester_id"]),
            cast(
                Target,
                AuthorizationTarget(
                    ScopeType.PROJECT if row["project_id"] else ScopeType.BU,
                    cast(UUID, row["project_id"] or row["bu_id"]),
                ),
            ),
            {"amount": Money(cast(Decimal, row["estimate"]), str(row["currency"]))},
            {
                "amount": format(cast(Decimal, row["estimate"]), ".4f"),
                "project_id": str(row["project_id"]) if row["project_id"] else "",
                "ledger_account_code": str(row["ledger_account_code"] or ""),
                "catalogue_items": ",".join(
                    sorted(str(line[0]) if line[0] else "-" for line in lines)
                ),
                "line_count": str(len(lines)),
            },
        )

    def summaries(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_ids: Sequence[UUID],
    ) -> Mapping[UUID, DocumentSummary]:
        rows = (
            RequisitionRepository(conn, scope)
            .execute(
                "SELECT id,number,title,estimate,currency,requester_id FROM "
                "requisition WHERE org_id=%(org_id)s AND id=ANY(%(ids)s)",
                {"ids": list(document_ids)},
            )
            .fetchall()
        )
        return {
            cast(UUID, r[0]): DocumentSummary(
                str(r[1]),
                str(r[2]),
                Money(cast(Decimal, r[3]), str(r[4])),
                cast(UUID, r[5]),
                f"/requisitions/{r[0]}",
                None,
            )
            for r in rows
        }

    def on_outcome(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_id: UUID,
        outcome: Outcome,
        context: OutcomeContext,
    ) -> None:
        repo = RequisitionRepository(conn, scope)
        before = read_row(repo, document_id)
        if before["reservation_entry_id"] is not None:
            held = reservation_remaining(conn, scope, cast(int, before["reservation_entry_id"]))
            lock_balance(conn, scope, held.project_id)
        row = read_row(repo, document_id, lock=True)
        if row["status"] != "approval_pending":
            raise conflict("invalid_transition", "This requisition is no longer pending approval.")
        if outcome == Outcome.APPROVED:
            raise conflict(
                "approval_effects_unavailable",
                "Approval effects are not available yet. Retry after the approval "
                "effects story is deployed.",
            )
        if outcome == Outcome.WITHDRAWN:
            to, reason = pending_terminal.get() or ("withdrawn", "withdrawn")
            authorize(
                row,
                to,
                context.actor_id,
                lambda permission: context.permitted(
                    permission, self.snapshot(conn, scope, document_id).target
                ),
            )
            release_reason = to
        elif outcome == Outcome.RETURNED:
            to, reason, release_reason = "draft", "returned", None
        elif outcome == Outcome.REJECTED:
            to = "draft" if context.reject_mode == "return_to_draft" else "rejected"
            reason = release_reason = "returned_to_draft" if to == "draft" else "rejected"
        else:
            raise conflict("invalid_transition", "This approval outcome is unavailable.")
        ledger = (
            release_live(conn, scope, row, release_reason, actor_id=context.actor_id)
            if release_reason
            else None
        )
        write_status(conn, scope, row, to, context.actor_id, reason, ledger, context.decision_id)


def open_document_count(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, project_id: UUID
) -> int:
    row = (
        RequisitionRepository(conn, scope)
        .execute(
            "SELECT count(*) FROM requisition WHERE org_id=%(org_id)s AND "
            "project_id=%(id)s AND status IN"
            " ('approval_pending','sourcing_in_progress','sourced','awarded','open_for_purchase')",
            {"id": project_id},
        )
        .fetchone()
    )
    assert row is not None
    return int(str(row[0]))
