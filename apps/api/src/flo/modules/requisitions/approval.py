"""Requisition implementation of the frozen document-adapter protocol."""

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget as Target
from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.money import Money, currency_exponent, quantize
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import (
    DocumentSnapshot,
    DocumentSummary,
    FieldType,
    Outcome,
    OutcomeContext,
)
from flo.modules.budget.schemas import AllocationCreate
from flo.modules.budget.service import (
    allocate,
    get_balance,
    lock_balance,
    reservation_remaining,
    reserve,
)
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService, codes_read, today
from flo.modules.projects.schemas import ProjectRead
from flo.modules.projects.service import (
    ProjectService,
    apply_sponsors,
    create_generated_project,
    depth_of,
)
from flo.modules.requisitions import revisions
from flo.modules.requisitions.funding import lock_funding, move_funds, release_live
from flo.modules.requisitions.lifecycle import authorize, pending_terminal, read_row, write_status
from flo.modules.requisitions.models import RequisitionRepository
from flo.modules.requisitions.schemas import invalid

logger = logging.getLogger(__name__)


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
        row = read_row(repo, document_id, include_deleted=True)
        header = revisions.live_snapshot(conn, scope, document_id)
        amendment = None
        if row["pending_amendment_id"] is not None:
            # A running amendment presents its proposal and the requisition's version (WF-001).
            amendment = repo.execute(
                "SELECT snapshot FROM requisition_revision WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": row["pending_amendment_id"]},
            ).fetchone()
        proposal = cast(revisions.Snapshot, amendment[0]) if amendment else header
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
            {"amount": Money(Decimal(proposal["estimate"]), str(row["currency"]))},
            revisions.snapshot_material(proposal),
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
                "SELECT id,number,title,estimate,currency,requester_id,department_code,"
                "ledger_account_code FROM "
                "requisition WHERE org_id=%(org_id)s AND id=ANY(%(ids)s)",
                {"ids": list(document_ids)},
            )
            .fetchall()
        )
        note_rows = (
            RequisitionRepository(conn, scope)
            .execute(
                "SELECT DISTINCT ON (requisition_id) requisition_id,note FROM requisition_revision "
                "WHERE org_id=%(org_id)s AND requisition_id=ANY(%(ids)s) AND "
                "(kind='submission' OR status='pending') ORDER BY requisition_id,revision_no DESC",
                {"ids": list(document_ids)},
            )
            .fetchall()
        )
        notes = {r[0]: r[1] for r in note_rows}
        return {
            cast(UUID, r[0]): DocumentSummary(
                str(r[1]),
                str(r[2]),
                Money(cast(Decimal, r[3]), str(r[4])),
                cast(UUID, r[5]),
                f"/requisitions/{r[0]}",
                cast(str | None, notes.get(r[0])),
                codes_read(conn, scope, cast(str | None, r[6]), cast(str | None, r[7])),
            )
            for r in rows
        }

    def prepare_approval(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_id: UUID,
        actor_id: UUID,
        substitutions: dict[str, str],
    ) -> None:
        """Optional hook: refuse approval over an expired code unless it is substituted."""
        from flo.modules.requisitions.codes import prepare_approval

        prepare_approval(conn, scope, document_id, actor_id, substitutions)

    def on_outcome(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_id: UUID,
        outcome: Outcome,
        context: OutcomeContext,
    ) -> None:
        repo = RequisitionRepository(conn, scope)
        current = read_row(repo, document_id)
        if current["status"] == "sourcing_in_progress" and current["pending_amendment_id"]:
            _amendment_outcome(conn, scope, repo, document_id, outcome, context)
            return
        if outcome == Outcome.APPROVED:
            _approved(conn, scope, repo, document_id, context)
            return
        before = read_row(repo, document_id)
        if before["reservation_entry_id"] is not None:
            held = reservation_remaining(conn, scope, cast(int, before["reservation_entry_id"]))
            lock_balance(conn, scope, held.project_id)
        row = read_row(repo, document_id, lock=True)
        if row["status"] != "approval_pending":
            raise conflict("invalid_transition", "This requisition is no longer pending approval.")
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


class ProjectRequestAdapter(RequisitionAdapter):
    """A standalone requisition under its own document type and workflow (D-M2-18).

    Outcomes are the requisition's: `_approved` already branches on `standalone`.
    """

    document_type = "project_request"

    def fields(self) -> Mapping[str, FieldType]:
        return {"amount": "money", "bu": "code"}

    def snapshot(
        self, conn: psycopg.Connection[tuple[object, ...]], scope: Scope, document_id: UUID
    ) -> DocumentSnapshot:
        base = super().snapshot(conn, scope, document_id)
        code = (
            RequisitionRepository(conn, scope)
            .execute(
                "SELECT u.code FROM requisition r JOIN org_unit u ON u.org_id=r.org_id AND "
                "u.id=r.bu_id WHERE r.org_id=%(org_id)s AND r.id=%(id)s",
                {"id": document_id},
            )
            .fetchone()
        )
        base.attributes["bu"] = str(code[0]) if code else None
        return base


def _amendment_outcome(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    repo: RequisitionRepository,
    document_id: UUID,
    outcome: Outcome,
    context: OutcomeContext,
) -> None:
    """Settle a running amendment. Lock order after the instance: project_balance, requisition.

    Only approval moves money: the round's remaining is released once (reason `resubmit`) and
    the new total reserved as round + 1, under the lock the decision already holds.
    """
    names = {
        Outcome.APPROVED: ("applied", "approved"),
        Outcome.REJECTED: ("rejected", "rejected"),
        Outcome.RETURNED: ("returned", "returned"),
        Outcome.WITHDRAWN: ("withdrawn", "withdrawn"),
    }
    if outcome not in names:
        raise conflict("invalid_transition", "This approval outcome is unavailable.")
    status, label = names[outcome]
    lock_funding(conn, scope, read_row(repo, document_id))
    row = read_row(repo, document_id, lock=True)
    revision = repo.execute(
        "SELECT id,status,snapshot FROM requisition_revision WHERE org_id=%(org_id)s AND id=%(id)s"
        " FOR UPDATE",
        {"id": row["pending_amendment_id"]},
    ).fetchone()
    if revision is None or revision[1] != "pending":
        raise conflict("invalid_transition", "This amendment is no longer pending approval.")
    ledger_entry: int | None = None
    if outcome == Outcome.APPROVED:
        snap = cast(revisions.Snapshot, revision[2])
        total = Decimal(snap["estimate"])
        if row["reservation_entry_id"] is not None:
            held = reservation_remaining(conn, scope, cast(int, row["reservation_entry_id"]))
            if held.remaining != total:
                round_ = cast(int, row["funding_round"]) + 1
                # Sub-project funded (E09-S16): the difference moves main <-> sub (D-M2-19).
                main_id = cast(UUID, row["project_id"])
                delta = total - held.remaining
                sub_funded = held.project_id != main_id
                if sub_funded and delta > 0:
                    _move(conn, scope, row, main_id, held.project_id, delta, round_, context)
                release_live(conn, scope, row, "resubmit", actor_id=context.actor_id)
                entry = reserve(
                    conn,
                    scope,
                    project_id=held.project_id,
                    amount=total,
                    currency=str(row["currency"]),
                    source_type="requisition",
                    source_id=document_id,
                    effective_date=today(),
                    actor_id=context.actor_id,
                    department_code=str(snap["header"]["department_code"]),
                    ledger_account_code=str(snap["header"]["ledger_account_code"]),
                    idempotency_key=f"req:{document_id}:reserve:{round_}",
                )
                ledger_entry = entry.id
                if sub_funded and delta < 0:
                    _move(conn, scope, row, held.project_id, main_id, -delta, round_, context)
                repo.execute(
                    "UPDATE requisition SET funding_round=%(round)s,reservation_entry_id=%(entry)s"
                    " WHERE org_id=%(org_id)s AND id=%(id)s",
                    {"id": document_id, "round": round_, "entry": entry.id},
                )
                row.update(funding_round=round_, reservation_entry_id=entry.id)
        revisions.apply_snapshot(conn, scope, document_id, snap)
    repo.execute(
        "UPDATE requisition SET pending_amendment_id=NULL WHERE org_id=%(org_id)s AND id=%(id)s",
        {"id": document_id},
    )
    revisions.set_status(repo, revision[0], status)
    revisions.amendment_event(
        conn,
        scope,
        row,
        context.actor_id,
        label,
        ledger_entry_id=ledger_entry,
        decision_id=context.decision_id,
    )


def _move(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    row: dict[str, object],
    source_id: UUID,
    target_id: UUID,
    amount: Decimal,
    round_: int,
    context: OutcomeContext,
) -> None:
    move_funds(
        conn,
        scope,
        row,
        source_id=source_id,
        target_id=target_id,
        amount=amount,
        key=f"req:{row['id']}:amend:{round_}",
        reason=f"Requisition {row['number']} amended, funds moved between projects",
        actor_id=context.actor_id,
    )


def _fund_sub_project(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    repo: RequisitionRepository,
    row: dict[str, object],
    sub: ProjectRead,
    context: OutcomeContext,
) -> int:
    """Move an approved requisition's money onto its sub-project (D-M2-19, ACC-001).

    The caller holds the main project's lock and the requisition row. Release the main-project
    reservation, transfer main -> sub, reserve on the sub: the main project's allocated and
    reserved each fall by the amount and its available is unchanged.
    """
    document_id = cast(UUID, row["id"])
    main_id = cast(UUID, row["project_id"])
    round_ = cast(int, row["funding_round"])
    amount = cast(Decimal, row["estimate"])
    # The sub-project is new and unreachable by other transactions; locking it second cannot
    # deadlock, and the main project is already locked by the caller.
    for project_id in sorted({main_id, sub.id}):
        lock_balance(conn, scope, project_id)
    release_live(conn, scope, row, "approved", actor_id=context.actor_id)
    move_funds(
        conn,
        scope,
        row,
        source_id=main_id,
        target_id=sub.id,
        amount=amount,
        key=f"req:{document_id}:fund:{round_}",
        reason=f"Requisition {row['number']} approved, funds moved to sub-project",
        actor_id=context.actor_id,
    )
    entry = reserve(
        conn,
        scope,
        project_id=sub.id,
        amount=amount,
        currency=str(row["currency"]),
        source_type="requisition",
        source_id=document_id,
        effective_date=today(),
        actor_id=context.actor_id,
        department_code=str(row["department_code"]),
        ledger_account_code=str(row["ledger_account_code"]),
        idempotency_key=f"req:{document_id}:reserve:{round_}:sub",
    )
    repo.execute(
        "UPDATE requisition SET reservation_entry_id=%(entry)s"
        " WHERE org_id=%(org_id)s AND id=%(id)s",
        {"id": document_id, "entry": entry.id},
    )
    row["reservation_entry_id"] = entry.id
    return entry.id


def _money(amount: Decimal, currency: str) -> str:
    return format(quantize(amount, currency), f",.{currency_exponent(currency)}f")


def _generate(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    row: dict[str, object],
    actor_id: UUID,
    *,
    bu_id: UUID,
    parent_id: UUID | None,
    currency: str,
    generated_by: UUID | None,
    source: UUID,
    reason: str,
) -> ProjectRead:
    # settings=None: numbering falls back to Settings(), as ProjectService.create does.
    return create_generated_project(
        conn,
        scope,
        settings=None,
        actor_id=actor_id,
        parent_id=parent_id,
        bu_id=bu_id,
        name=str(row["title"]),
        description=f"Created from {row['number']} on approval.",
        department_code=str(row["department_code"]),
        ledger_account_code=str(row["ledger_account_code"]),
        currency=currency,
        owner_id=cast(UUID, row["requester_id"]),
        planned_end=cast(date | None, row["needed_by"]),
        generated_by_requisition_id=generated_by,
        source_requisition_id=source,
        reason=reason,
    )


def _mismatch(document_id: UUID, what: str) -> ProblemError:
    logger.error("requisition %s reservation mismatch: %s", document_id, what)
    return conflict(
        "reservation_mismatch",
        "The reserved amount no longer matches the estimate. Ask an administrator "
        "to review the reservation.",
    )


def _approved(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    repo: RequisitionRepository,
    document_id: UUID,
    context: OutcomeContext,
) -> None:
    """Final approval: sub-project, and the reservation for a standalone request (D-M2-5/15).

    Runs inside the decision's transaction. Lock order after the instance: organization
    (inside allocate), project_balance, requisition.
    """
    before = read_row(repo, document_id)
    if before["status"] != "approval_pending":
        raise conflict("invalid_transition", "This requisition is no longer pending approval.")
    standalone = bool(before["standalone"])
    assigned = context.assign_project_id
    if assigned is not None and not standalone:
        raise invalid(
            "assign_project_id", "Only a standalone request can be assigned to a project."
        )
    estimate = cast(Decimal, before["estimate"])
    currency = str(before["currency"])
    number = str(before["number"])
    actor = context.actor_id
    projects = ProjectService(conn, scope, actor)
    new_project = standalone and assigned is None
    target_id = assigned or cast(UUID | None, before["project_id"])
    created = None
    allocation_entry_id: int | None = None
    entry_id = cast(int | None, before["reservation_entry_id"])
    round_ = cast(int, before["funding_round"])
    if new_project:
        bu_id = cast(UUID, before["bu_id"])
        if not context.permitted(
            "budget.allocate", cast(Target, AuthorizationTarget(ScopeType.BU, bu_id))
        ):
            raise ProblemError(
                ErrorCode.FORBIDDEN,
                detail=(
                    "Approving a standalone request creates budget, which needs Budget Allocate "
                    f"on {OrgService(conn, scope).get_unit(bu_id).name}. Ask someone who holds it "
                    "to approve, or assign an existing project."
                ),
            )
        created = _generate(
            conn,
            scope,
            before,
            actor,
            bu_id=bu_id,
            parent_id=None,
            currency=currency,
            generated_by=None,
            source=document_id,
            reason=f"Standalone requisition {number} approved",
        )
        target_id = created.id
        apply_sponsors(
            conn,
            scope,
            created.id,
            [
                cast(UUID, r[0])
                for r in repo.execute(
                    "SELECT user_id FROM requisition_sponsor WHERE org_id=%(org_id)s AND "
                    "requisition_id=%(id)s ORDER BY user_id",
                    {"id": document_id},
                ).fetchall()
            ],
        )
        allocation = allocate(
            conn,
            scope,
            created.id,
            AllocationCreate(
                amount=format(estimate, "f"),  # type: ignore[arg-type]
                currency=currency,
                effective_date=today(),
                reason=f"Standalone requisition {number} approved",
                evidence_ref=f"requisition:{number}",
            ),
            actor,
            idempotency_key=f"req:{document_id}:allocate:1",
        )
        allocation_entry_id = allocation.entries[0].id
    elif assigned is not None:
        project = projects.get(assigned)  # 404 for an unknown or foreign project
        if not context.permitted(
            "requisition.approve", cast(Target, AuthorizationTarget(ScopeType.PROJECT, assigned))
        ):
            raise ProblemError(
                ErrorCode.FORBIDDEN,
                detail=(
                    f"You need requisition.approve on {project.name} to assign this request "
                    "to it. Ask an administrator for access."
                ),
            )
        lock_balance(conn, scope, assigned)
    else:
        assert target_id is not None
        if entry_id is not None:
            lock_balance(conn, scope, reservation_remaining(conn, scope, entry_id).project_id)
        else:
            # Invariant breach: reserved at submit, yet no pointer. Lock first, then refuse.
            lock_balance(conn, scope, target_id)
            raise _mismatch(document_id, "no live reservation is recorded")
    row = read_row(repo, document_id, lock=True)
    if row["status"] != "approval_pending":
        raise conflict("invalid_transition", "This requisition is no longer pending approval.")
    assert target_id is not None
    target = projects.get(target_id)
    if not new_project:
        if target.status != "active":
            raise conflict(
                "project_not_active",
                f"{target.name} is {target.status.replace('_', ' ')}. Reactivate it, then approve.",
            )
        if assigned is not None:
            if target.generated_by_requisition_id is not None:
                raise conflict(
                    "project_generated",
                    f"{target.name} was generated by a requisition and cannot take another. "
                    "Choose a different project.",
                )
            if target.currency != currency:
                raise conflict(
                    "currency_mismatch",
                    f"{target.name} is in {target.currency}, this request in {currency}. "
                    "Choose a project in the same currency.",
                )
        if depth_of(conn, scope, target_id) >= 5:
            raise conflict(
                "project_depth_exceeded",
                f"{target.name} is at the five-level limit, so no sub-project can be created. "
                "Choose a shallower project.",
            )
        if assigned is not None:
            balance = get_balance(conn, scope, assigned)
            after = balance.available - estimate
            if (
                after < 0
                and OrgService(conn, scope)
                .effective_setting(balance.bu_id, "allow_negative_budget")
                .value
                is not True
            ):
                raise ProblemError(
                    ErrorCode.INSUFFICIENT_BUDGET,
                    detail=(
                        f"Assigning this request to {target.name} needs "
                        f"{_money(estimate, currency)} {currency}; "
                        f"{_money(balance.available, currency)} {currency} is available."
                    ),
                    checks={
                        "problem": "insufficient_budget",
                        "available": str(balance.available),
                        "requested": str(estimate),
                    },
                )
        else:
            if row["reservation_entry_id"] is None:
                raise _mismatch(document_id, "no live reservation is recorded")
            held = reservation_remaining(conn, scope, cast(int, row["reservation_entry_id"]))
            if held.remaining != estimate or held.project_id != target_id:
                raise _mismatch(document_id, f"remaining {held.remaining}, estimate {estimate}")
    if new_project:
        round_ = cast(int, row["funding_round"]) + 1
        entry = reserve(
            conn,
            scope,
            project_id=target_id,
            amount=estimate,
            currency=currency,
            source_type="requisition",
            source_id=document_id,
            effective_date=today(),
            actor_id=actor,
            department_code=str(row["department_code"]),
            ledger_account_code=str(row["ledger_account_code"]),
            idempotency_key=f"req:{document_id}:reserve:{round_}",
        )
        entry_id = entry.id
        repo.execute(
            "UPDATE requisition SET project_id=%(project)s,funding_round=%(round)s,"
            "reservation_entry_id=%(entry)s WHERE org_id=%(org_id)s AND id=%(id)s",
            {"id": document_id, "project": target_id, "round": round_, "entry": entry_id},
        )
        row.update(project_id=target_id, funding_round=round_, reservation_entry_id=entry_id)
    elif assigned is not None:
        round_ = cast(int, row["funding_round"]) + 1
        repo.execute(
            "UPDATE requisition SET project_id=%(project)s,funding_round=%(round)s"
            " WHERE org_id=%(org_id)s AND id=%(id)s",
            {"id": document_id, "project": target_id, "round": round_},
        )
        row.update(project_id=target_id, funding_round=round_)
    extra: dict[str, object] = {"project_id": str(target_id), "reservation_entry_id": entry_id}
    if not new_project:
        try:
            sub = _generate(
                conn,
                scope,
                row,
                actor,
                bu_id=target.bu_id,
                parent_id=target_id,
                currency=target.currency,
                generated_by=document_id,
                source=document_id,
                reason=f"Generated from requisition {number}",
            )
        except ProblemError as exc:
            if (exc.checks or {}).get("problem") == "project_depth_exceeded":
                raise conflict("project_depth_exceeded", exc.detail or "Depth limit.") from exc
            raise
        extra["sub_project_id"] = str(sub.id)
        entry_id = _fund_sub_project(conn, scope, repo, row, sub, context)
        extra["reservation_entry_id"] = entry_id
    else:
        extra["created_project_id"] = str(target_id)
        extra["allocation_entry_id"] = allocation_entry_id
    write_status(
        conn,
        scope,
        row,
        "sourcing_in_progress",
        actor,
        "approved",
        entry_id,
        context.decision_id,
        audit_action="requisition.approve",
        audit_extra=extra,
    )


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
