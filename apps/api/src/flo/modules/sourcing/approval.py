"""The award's implementation of the frozen document-adapter protocol (E12-S08)."""

from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget as Target
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import (
    DocumentSnapshot,
    DocumentSummary,
    FieldType,
    Outcome,
    OutcomeContext,
)
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.sourcing.awards import AwardService

type Conn = psycopg.Connection[tuple[object, ...]]


def _target(unit_id: UUID, rfq_id: UUID) -> Target:
    return cast(Target, AuthorizationTarget(ScopeType.BU, unit_id, "rfq", rfq_id))


class AwardAdapter:
    document_type = "award"
    approve_permission = "rfq.approve"
    read_permission = "rfq.view"
    accepts_project_assignment = False  # the award inherits the RFQ's project

    def fields(self) -> Mapping[str, FieldType]:
        return {"amount": "money"}

    def snapshot(self, conn: Conn, scope: Scope, document_id: UUID) -> DocumentSnapshot:
        award, unit_id, total, _, material = AwardService(conn, scope).snapshot_facts(document_id)
        return DocumentSnapshot(
            cast(int, award["version"]),
            cast(UUID, award["created_by"]),
            _target(unit_id, cast(UUID, award["rfq_id"])),
            {"amount": total},
            {"lines": material, "rationale": cast(str | None, award["rationale"]) or ""},
        )

    def summaries(
        self, conn: Conn, scope: Scope, document_ids: Sequence[UUID]
    ) -> Mapping[UUID, DocumentSummary]:
        out = {}
        for id in document_ids:
            award, _, total, number, _ = AwardService(conn, scope).snapshot_facts(id)
            out[id] = DocumentSummary(
                cast(str, award["number"]),
                f"Award of {number}",
                total,
                cast(UUID, award["created_by"]),
                f"/sourcing/rfqs/{award['rfq_id']}",
                cast(str | None, award["rationale"]),
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
        if outcome == Outcome.WITHDRAWN:
            award, unit_id, *_ = AwardService(conn, scope).snapshot_facts(document_id)
            if not context.permitted("rfq.edit", _target(unit_id, cast(UUID, award["rfq_id"]))):
                raise ProblemError(
                    ErrorCode.FORBIDDEN, detail="You need RFQ Edit on this RFQ's unit to withdraw."
                )
        AwardService(conn, scope, context.actor_id).settle(document_id, outcome.value, context)
