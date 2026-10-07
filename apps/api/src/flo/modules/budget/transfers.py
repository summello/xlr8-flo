"""Atomic sibling transfers, serialized by the two funding rows."""

from dataclasses import asdict
from typing import cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget.ledger import LedgerRepository, invalid, post_entry
from flo.modules.budget.models import LedgerType
from flo.modules.budget.postings import conflict
from flo.modules.budget.schemas import LedgerEntryRead, TransferCreate, TransferRead
from flo.modules.org.service import OrgService
from flo.modules.projects.service import ProjectService


def transfer(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    body: TransferCreate,
    actor_id: UUID,
    idempotency_key: str,
) -> TransferRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        if body.from_project_id == body.to_project_id:
            raise invalid("Choose two different projects.", "same_project", field="to_project_id")
        projects = ProjectService(conn, scope)
        repo = LedgerRepository(conn, scope)
        for project_id in sorted((body.from_project_id, body.to_project_id)):
            if repo.balance(project_id, lock=True) is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
        giving = projects.get(body.from_project_id)
        receiving = projects.get(body.to_project_id)
        prior = repo.entry("idempotency_key = %(key)s", {"key": idempotency_key + ":out"})
        if prior is None:
            detail = None
            if projects.depth_of(giving.id) != projects.depth_of(receiving.id):
                detail = "Projects have different hierarchy levels. Choose the same level."
            elif giving.parent_id != receiving.parent_id:
                detail = (
                    "Projects have different parents. Cross-hierarchy transfers arrive with "
                    "E07-S08; choose sibling projects."
                )
            elif giving.currency != receiving.currency or giving.currency != body.currency:
                detail = "Projects and transfer must have matching currencies."
            elif receiving.status not in {"draft", "active"}:
                detail = "The receiving project must be Draft or Active. Choose an open project."
            if detail:
                raise conflict("transfer_not_eligible", detail)
            if body.evidence_ref is None and any(
                OrgService(conn, scope).effective_setting(p.bu_id, "budget.evidence_required").value
                is True
                for p in (giving, receiving)
            ):
                raise invalid(
                    "Supporting evidence is required. Supply an evidence reference.",
                    field="evidence_ref",
                )
        group_id = (prior.transfer_group_id if prior else None) or uuid4()
        entries = []
        for project, amount, suffix in (
            (giving, -body.amount, ":out"),
            (receiving, body.amount, ":in"),
        ):
            entry = post_entry(
                conn,
                scope,
                project_id=project.id,
                entry_type=LedgerType.TRANSFER,
                amount=amount,
                currency=body.currency,
                source_type="transfer",
                source_id=group_id,
                transfer_group_id=group_id,
                effective_date=body.effective_date,
                actor_id=actor_id,
                department_code=project.department_code,
                ledger_account_code=project.ledger_account_code,
                idempotency_key=idempotency_key + suffix,
                reason=body.reason,
            )
            evidence = repo.execute(
                "SELECT ref FROM ledger_evidence WHERE org_id = %(org_id)s AND entry_id = %(id)s",
                {"id": entry.id},
            ).fetchone()
            if prior is not None and (evidence[0] if evidence else None) != body.evidence_ref:
                raise ProblemError(
                    ErrorCode.IDEMPOTENCY_KEY_REUSED,
                    detail="This key carries other evidence. Use a new key.",
                    checks={"problem": "idempotency_conflict"},
                )
            if prior is None and body.evidence_ref is not None:
                repo.execute(
                    "INSERT INTO ledger_evidence(entry_id, org_id, ref) "
                    "VALUES (%(id)s, %(org_id)s, %(ref)s)",
                    {"id": entry.id, "ref": body.evidence_ref},
                )
            entries.append(LedgerEntryRead(**asdict(entry)))
        if prior is None:
            AuditWriter(cast(AuditConnection, conn), scope).write(
                actor=AuditActor(ActorKind.USER, actor_id),
                action="budget.transfer",
                target_type="transfer",
                target_id=group_id,
                bu_id=giving.bu_id,
                outcome=Outcome.SUCCESS,
                after_source={"transfer_group_id": group_id},
                after_fields=("transfer_group_id",),
            )
        return TransferRead(transfer_group_id=group_id, entries=entries)
