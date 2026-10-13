"""The caller's approval inbox: live membership, server-side filter, sort and paging (APR-020)."""

import base64
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

import psycopg

from flo.kernel.authz import PermissionHoldersResolver
from flo.kernel.errors import ProblemError
from flo.kernel.money import Money
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals import delegation, engine
from flo.modules.approvals.models import WorkflowRepository
from flo.modules.approvals.schemas import (
    Contract,
    DocumentSnapshot,
    DocumentSummary,
    DocumentType,
    WorkflowVersionRead,
    decision_invalid,
)
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    IdentityAuthorizationConnection,
    emails_by_id,
)

Connection = psycopg.Connection[tuple[object, ...]]
View = Literal["pending", "overdue", "completed", "delegated"]
Sort = Literal["due", "amount", "decided"]
PAGE = 50


class InboxMoney(Contract):
    amount: str
    currency: str


class InboxStep(Contract):
    index: int
    of: int


class InboxRow(Contract):
    task_id: UUID
    instance_id: UUID
    document_type: str
    document_id: UUID
    number: str
    title: str
    requester_note: str | None
    requester_email: str
    document_version: int
    amount: InboxMoney | None
    step: InboxStep
    due_at: datetime | None
    decided_at: datetime | None
    status_word: str
    on_behalf_of_email: str | None
    href: str


class InboxCounts(Contract):
    pending: int
    overdue: int
    completed: int
    delegated: int


class InboxPage(Contract):
    rows: list[InboxRow]
    total: int
    total_amount: list[InboxMoney]
    counts: InboxCounts
    next_cursor: str | None


@dataclass
class Entry:
    task_id: UUID
    instance_id: UUID
    document_type: str
    document_id: UUID
    document_version: int
    node_id: str
    route: list[dict[str, object]]
    due_at: datetime | None
    decided_at: datetime | None = None
    word: str = ""
    other: UUID | None = None  # the delegator covered, or the delegate it was passed to
    covering: bool = False


def _money(value: Money) -> InboxMoney:
    return InboxMoney(amount=f"{value.amount:f}", currency=value.currency)


def _encode(offset: int, key: Mapping[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps({"offset": offset, **key}).encode()).decode()


def _decode(cursor: str, key: Mapping[str, object]) -> int:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
        offset = int(decoded.pop("offset"))
        if decoded != key or offset < 0:
            raise ValueError("filter mismatch")
    except (ValueError, KeyError, TypeError, UnicodeError, AttributeError) as exc:
        raise decision_invalid("cursor", "Invalid cursor. Restart paging.") from exc
    return offset


def _open_entries(
    conn: Connection,
    scope: Scope,
    repo: WorkflowRepository,
    user_id: UUID,
    resolver: PermissionHoldersResolver,
    now: datetime,
) -> tuple[list[Entry], list[Entry]]:
    """(pending, delegated): tasks the caller may decide now, and cover given or taken."""
    rows = repo.execute(
        "SELECT t.id,t.instance_id,t.node_id FROM approval_task t JOIN approval_instance i "
        "ON i.org_id=t.org_id AND i.id=t.instance_id WHERE t.org_id=%(org_id)s "
        "AND t.status='open' AND i.status='running' ORDER BY t.created_at,t.id"
    ).fetchall()
    tasks = {
        t["id"]: t for iid in {r[1] for r in rows} for t in engine._tasks(repo, cast(UUID, iid))
    }
    decided = {
        cast(UUID, r[0])
        for r in repo.execute(
            "SELECT task_id FROM approval_decision WHERE org_id=%(org_id)s "
            "AND task_id IS NOT NULL AND (actor_id=%(me)s OR on_behalf_of=%(me)s) "
            "AND outcome IN ('approve','reject','return')",
            {"me": user_id},
        ).fetchall()
    }
    pending: list[Entry] = []
    delegated: list[Entry] = []
    instances: dict[UUID, dict[str, object]] = {}
    versions: dict[UUID, WorkflowVersionRead] = {}
    # ponytail: evaluates live membership for every open task of the org; cache holders per
    # (type, target) or prefilter in SQL if an organization holds thousands of open tasks.
    for row in rows:
        task = tasks[cast(UUID, row[0])]
        instance = instances.get(cast(UUID, row[1])) or engine._instance(repo, cast(UUID, row[1]))
        instances[cast(UUID, row[1])] = instance
        wv = cast(UUID, instance["workflow_version_id"])
        version = versions.get(wv) or engine._version(repo, wv)
        versions[wv] = version
        node = next(n for n in version.graph.nodes if n.id == task["node_id"])
        try:
            snapshot: DocumentSnapshot = engine.adapter_for(
                str(instance["document_type"])
            ).snapshot(conn, scope, cast(UUID, instance["document_id"]))
        except ProblemError:
            continue
        members = delegation.effective_members(
            conn, scope, instance, task, node, snapshot, resolver, now=now
        )
        entry = Entry(
            task_id=cast(UUID, task["id"]),
            instance_id=cast(UUID, instance["id"]),
            document_type=str(instance["document_type"]),
            document_id=cast(UUID, instance["document_id"]),
            document_version=snapshot.version,  # what a decision must name (APR-016)
            node_id=str(task["node_id"]),
            route=cast(list[dict[str, object]], instance["route"]),
            due_at=cast(datetime | None, task["due_at"]),
        )
        if entry.task_id not in decided and (
            user_id in members.base or user_id in members.delegates
        ):
            pending.append(replace(entry, other=members.delegates.get(user_id)))
        if user_id in members.delegates:
            delegated.append(replace(entry, other=members.delegates[user_id], covering=True))
        elif user_id in members.base:
            passed = next((d for d, owner in members.delegates.items() if owner == user_id), None)
            if passed is not None:
                delegated.append(replace(entry, other=passed))
    return pending, delegated


def _completed_entries(repo: WorkflowRepository, user_id: UUID) -> list[Entry]:
    rows = repo.execute(
        "SELECT d.task_id,d.instance_id,d.on_behalf_of,d.outcome,d.decided_at,"
        "i.document_type,i.document_id,d.document_version,i.route,t.node_id,t.due_at "
        "FROM approval_decision d JOIN approval_task t ON t.org_id=d.org_id AND t.id=d.task_id "
        "JOIN approval_instance i ON i.org_id=d.org_id AND i.id=d.instance_id "
        "WHERE d.org_id=%(org_id)s AND d.actor_id=%(me)s "
        "AND d.outcome IN ('approve','reject','return') ORDER BY d.decided_at DESC,d.id",
        {"me": user_id},
    ).fetchall()
    words = {"approve": "Approved", "return": "Returned for Changes", "reject": "Rejected"}
    return [
        Entry(
            task_id=cast(UUID, r[0]),
            instance_id=cast(UUID, r[1]),
            document_type=str(r[5]),
            document_id=cast(UUID, r[6]),
            document_version=cast(int, r[7]),
            node_id=str(r[9]),
            route=cast(list[dict[str, object]], r[8]),
            due_at=cast(datetime | None, r[10]),
            decided_at=cast(datetime, r[4]),
            word=words[str(r[3])],
            other=cast(UUID | None, r[2]),
        )
        for r in rows
    ]


def _step(entry: Entry) -> InboxStep:
    nodes = [str(e["node_id"]) for e in entry.route if "node_id" in e]
    index = nodes.index(entry.node_id) + 1 if entry.node_id in nodes else 1
    return InboxStep(index=index, of=max(len(nodes), index))


def inbox(
    conn: Connection,
    scope: Scope,
    *,
    user_id: UUID,
    view: View,
    document_type: DocumentType | None = None,
    q: str = "",
    sort: Sort | None = None,
    direction: Literal["asc", "desc"] | None = None,
    cursor: str | None = None,
    resolver: PermissionHoldersResolver | None = None,
    now: datetime | None = None,
) -> InboxPage:
    now = now or datetime.now(UTC)
    sort = sort or ("decided" if view == "completed" else "due")
    direction = direction or ("desc" if sort == "decided" else "asc")
    key = {
        "view": view,
        "type": document_type,
        "q": q.strip().casefold(),
        "sort": sort,
        "direction": direction,
    }
    offset = _decode(cursor, key) if cursor else 0
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        resolver = resolver or AuthorizationResolver(cast(AuthorizationConnection, conn), scope)
        pending, delegated = _open_entries(conn, scope, repo, user_id, resolver, now)
        completed = _completed_entries(repo, user_id)
        overdue = [e for e in pending if e.due_at is not None and e.due_at < now]
        counts = InboxCounts(
            pending=len(pending),
            overdue=len(overdue),
            completed=len(completed),
            delegated=len(delegated),
        )
        selected = {"pending": pending, "overdue": overdue, "completed": completed}.get(
            view, delegated
        )
        selected = [e for e in selected if document_type in (None, e.document_type)]
        # One batch call per document type.
        by_type: dict[str, list[UUID]] = defaultdict(list)
        for e in selected:
            by_type[e.document_type].append(e.document_id)
        summaries: dict[tuple[str, UUID], DocumentSummary] = {}
        for kind, ids in by_type.items():
            for doc_id, summary in engine.adapter_for(kind).summaries(conn, scope, ids).items():
                summaries[(kind, doc_id)] = summary
        selected = [e for e in selected if (e.document_type, e.document_id) in summaries]
        emails = emails_by_id(
            cast(IdentityAuthorizationConnection, conn),
            [
                *(s.requester_id for s in summaries.values()),
                *(e.other for e in selected if e.other is not None),
            ],
        )

        def doc(e: Entry) -> DocumentSummary:
            return summaries[(e.document_type, e.document_id)]

        def requester(e: Entry) -> str:
            return emails.get(doc(e).requester_id, "(removed user)")

        if key["q"]:
            needle = str(key["q"])
            selected = [
                e
                for e in selected
                if any(
                    v.casefold().startswith(needle)
                    for v in (doc(e).number, doc(e).title, requester(e))
                )
            ]
        selected.sort(key=lambda e: str(e.task_id))

        def sort_key(e: Entry) -> object:
            if sort == "amount":
                money = doc(e).amount
                return None if money is None else money.amount
            return e.decided_at if sort == "decided" else e.due_at

        # Entries without a value always sort last, whichever the direction.
        valued = sorted(
            (e for e in selected if sort_key(e) is not None),
            key=sort_key,  # type: ignore[arg-type]
            reverse=direction == "desc",
        )
        selected = valued + [e for e in selected if sort_key(e) is None]
        sums: dict[str, Decimal] = {}
        for e in selected:
            money = doc(e).amount
            if money is not None:
                total = Money(sums.get(money.currency, Decimal(0)), money.currency) + money
                sums[money.currency] = total.amount
        page = selected[offset : offset + PAGE]
        rows = []
        for e in page:
            s = doc(e)
            word = e.word
            behalf = None
            if view in {"pending", "overdue"}:
                word = "Overdue" if e.due_at is not None and e.due_at < now else "Your Turn"
                behalf = emails.get(e.other) if e.other else None
            if view == "completed":
                behalf = emails.get(e.other) if e.other else None
            if view == "delegated":
                who = emails.get(e.other, "(removed user)") if e.other else ""
                word = f"Covering {who}" if e.covering else f"With {who}"
                behalf = who if e.covering else None
            rows.append(
                InboxRow(
                    task_id=e.task_id,
                    instance_id=e.instance_id,
                    document_type=e.document_type,
                    document_id=e.document_id,
                    number=s.number,
                    title=s.title,
                    requester_note=s.note,
                    requester_email=requester(e),
                    document_version=e.document_version,
                    amount=None if s.amount is None else _money(s.amount),
                    step=_step(e),
                    due_at=e.due_at,
                    decided_at=e.decided_at,
                    status_word=word,
                    on_behalf_of_email=behalf,
                    href=s.href,
                )
            )
        return InboxPage(
            rows=rows,
            total=len(selected),
            total_amount=[InboxMoney(amount=f"{v:f}", currency=c) for c, v in sorted(sums.items())],
            counts=counts,
            next_cursor=_encode(offset + PAGE, key) if offset + PAGE < len(selected) else None,
        )


__all__ = ["InboxPage", "inbox"]
