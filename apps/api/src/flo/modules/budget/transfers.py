"""Atomic tree transfers, serialized by funding rows in global lock order."""

from collections.abc import Callable
from dataclasses import asdict
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.money import quantize
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget.funding_policy import mode_for
from flo.modules.budget.ledger import InsufficientBudget, LedgerRepository, invalid, post_entry
from flo.modules.budget.models import LedgerEntry, LedgerType
from flo.modules.budget.postings import conflict
from flo.modules.budget.schemas import (
    AllocationCreate,
    LedgerEntryRead,
    TransferCreate,
    TransferRead,
)
from flo.modules.org.service import OrgService
from flo.modules.projects.schemas import PathRead, ProjectRead
from flo.modules.projects.service import ProjectService


def post_legs(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    body: AllocationCreate,
    actor_id: UUID,
    idempotency_key: str,
    legs: list[tuple[ProjectRead, Decimal, str]],
    *,
    allow_negative: bool | None = None,
    poster: Callable[..., LedgerEntry] | None = None,
) -> TransferRead:
    """Post one balanced group; the caller owns locks and the outer transaction."""
    repo = LedgerRepository(conn, scope)
    prior = repo.entry("idempotency_key = %(key)s", {"key": idempotency_key + legs[0][2]})
    group_id = (prior.transfer_group_id if prior else None) or uuid4()
    entries = []
    for project, amount, suffix in legs:
        entry = (poster or post_entry)(
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
            allow_negative=allow_negative,
        )
        evidence = repo.execute(
            "SELECT ref FROM ledger_evidence WHERE org_id=%(org_id)s AND entry_id=%(id)s",
            {"id": entry.id},
        ).fetchone()
        if prior is not None and (evidence[0] if evidence else None) != body.evidence_ref:
            raise ProblemError(
                ErrorCode.IDEMPOTENCY_KEY_REUSED,
                detail="This key carries other evidence. Use a new key.",
                checks={"problem": "idempotency_conflict"},
            )
        if evidence is None and body.evidence_ref is not None:
            repo.execute(
                "INSERT INTO ledger_evidence(entry_id,org_id,ref) "
                "VALUES (%(id)s,%(org_id)s,%(ref)s)",
                {"id": entry.id, "ref": body.evidence_ref},
            )
        entries.append(LedgerEntryRead(**asdict(entry)))
    if sum((entry.amount for entry in entries), Decimal(0)) != 0:
        raise conflict(
            "transfer_invariant_violation", "Transfer legs do not balance. Retry the transfer."
        )
    return TransferRead(transfer_group_id=group_id, entries=entries)


def plan_legs(
    nodes: dict[UUID, ProjectRead],
    path: PathRead,
    giver: UUID,
    recipient: UUID,
    siblings: bool,
    mode: str,
    amount: Decimal,
) -> list[tuple[ProjectRead, Decimal, str]]:
    if siblings or path.lca is None or mode == "roll_up":
        return [(nodes[giver], -amount, ":out"), (nodes[recipient], amount, ":in")]
    route = [*path.up, path.lca, *path.down]
    legs = []
    for hop, (source, target) in enumerate(zip(route, route[1:])):
        legs.extend(
            [
                (nodes[source], -amount, f":{hop}:out"),
                (nodes[target], amount, f":{hop}:in"),
            ]
        )
    return legs


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
        giving = projects.get(body.from_project_id)
        receiving = projects.get(body.to_project_id)
        siblings = giving.parent_id == receiving.parent_id and projects.depth_of(
            giving.id
        ) == projects.depth_of(receiving.id)
        path = projects.path_between(giving.id, receiving.id)
        mode = mode_for(conn, scope, giving)
        ids = sorted(
            set([giving.id, receiving.id, *path.up, *path.down] + ([path.lca] if path.lca else []))
        )
        locked = repo.execute(
            "SELECT project_id FROM project_balance WHERE org_id=%(org_id)s "
            "AND project_id=ANY(%(ids)s) ORDER BY project_id FOR UPDATE",
            {"ids": ids},
        ).fetchall()
        if len(locked) != len(ids):
            raise ProblemError(ErrorCode.NOT_FOUND)
        nodes = {node_id: projects.get(node_id) for node_id in ids}
        giving, receiving = nodes[giving.id], nodes[receiving.id]
        legs = plan_legs(nodes, path, giving.id, receiving.id, siblings, mode, body.amount)
        prior = repo.entry("idempotency_key = %(key)s", {"key": idempotency_key + legs[0][2]})
        if prior is None:
            detail = None
            if not siblings and path.lca is None:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=(
                        "Projects have different parents or hierarchy levels in different trees. "
                        "Cross-hierarchy transfers in E07-S08 require one tree."
                    ),
                    checks={"problem": "transfer_not_eligible", "reason": "different_trees"},
                )
            if any(
                node.currency != body.currency
                for node in ((giving, receiving) if siblings else nodes.values())
            ):
                detail = "Projects and transfer must have matching currencies."
            else:
                for node, amount, _ in legs:
                    if amount > 0 and node.status not in {"draft", "active"}:
                        detail = (
                            f"Receiving node {node.id} has status {node.status}. "
                            "Choose a Draft or Active project."
                        )
                        break
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
        if prior is None:
            running = {}
            for node_id in ids:
                balance = repo.balance(node_id)
                assert balance is not None
                running[node_id] = balance.available
            for node, amount, _ in legs:
                amount = quantize(amount, body.currency)
                projected = running[node.id] + amount
                if (
                    amount < 0
                    and projected < 0
                    and OrgService(conn, scope)
                    .effective_setting(node.bu_id, "allow_negative_budget")
                    .value
                    is not True
                ):
                    raise InsufficientBudget(running[node.id], -amount)
                running[node.id] = projected
        result = post_legs(conn, scope, body, actor_id, idempotency_key, legs)
        group_id = result.transfer_group_id
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
        return result
