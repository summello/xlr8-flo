"""The linked-record graph of a requisition, computed from the owning records (REQ-009, WF-001)."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.errors import conflict
from flo.kernel.money import currency_exponent, quantize
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.service import assert_current_approval, instances_for
from flo.modules.budget.service import entries_for_source
from flo.modules.collaboration.service import CollaborationService
from flo.modules.org.service import codes_read
from flo.modules.projects.service import ProjectService, generated_for
from flo.modules.requisitions import revisions
from flo.modules.requisitions.approval import RequisitionAdapter
from flo.modules.requisitions.lifecycle import read_row
from flo.modules.requisitions.models import RequisitionRepository, doc_type
from flo.modules.requisitions.schemas import (
    LinkAccess,
    LinkEdge,
    LinkedNode,
    LinkedRecord,
    LinksRead,
    Tone,
)

logger = logging.getLogger(__name__)
type Conn = psycopg.Connection[tuple[object, ...]]
# (permission, "project" | "bu", target id): built by the router from kernel.authz.permits.
type ReadCheck = Callable[[str, str, UUID], bool]


# A provider returns `LinkedNode`s: each carries the `access` (permission and target) a caller
# needs to see it. `links` authorizes every node itself through the `read_check` and fails
# closed: a node that is not a `LinkedNode`, or whose check is denied, is omitted. A provider
# never decides who sees its nodes, so a new one cannot forget to.
type Provider = Callable[[Conn, Scope, UUID, ReadCheck], list[LinkedNode]]
_PROVIDERS: dict[str, Provider] = {}
KIND_ORDER = (
    "project",
    "generated_project",
    "revision",
    "approval",
    "ledger_entry",
    "attachment",
    "note",
)
SOURCING_OR_LATER = ("sourcing_in_progress", "sourced", "awarded", "open_for_purchase")


def register_link_provider(kind: str, provider: Provider) -> None:
    _PROVIDERS[kind] = provider


def _date(value: datetime) -> str:
    return f"{value.day} {value:%b %Y}"


def _access(conn: Conn, scope: Scope, requisition_id: UUID) -> LinkAccess:
    row = read_row(RequisitionRepository(conn, scope), requisition_id)
    standalone = bool(row["standalone"])
    return LinkAccess(
        permission=RequisitionAdapter.read_permission,
        scope_type="bu" if standalone else "project",
        target_id=cast(UUID, row["bu_id"] if standalone else row["project_id"]),
    )


def _project_node(
    conn: Conn, scope: Scope, project_id: UUID, kind: str, relation: str
) -> LinkedNode:
    project = ProjectService(conn, scope).get(project_id)
    tones: dict[str, Tone] = {
        "draft": "neutral",
        "approval_pending": "info",
        "active": "success",
        "deferred": "warning",
        "completed": "neutral",
        "abandoned": "danger",
    }
    return LinkedNode(
        key=f"{kind}:{project.id}",
        kind=kind,
        id=str(project.id),
        label=f"Project {project.number} {project.name}",
        state=project.status.replace("_", " ").capitalize(),
        state_tone=tones[project.status],
        href=f"/projects/{project.id}",
        at=project.created_at,
        relation=relation,
        access=LinkAccess(permission="project.read", scope_type="project", target_id=project.id),
    )


def _project(
    conn: Conn, scope: Scope, requisition_id: UUID, read_check: ReadCheck
) -> list[LinkedNode]:
    project_id = read_row(RequisitionRepository(conn, scope), requisition_id)["project_id"]
    if not project_id:
        return []
    return [_project_node(conn, scope, cast(UUID, project_id), "project", "funds")]


def _generated(
    conn: Conn, scope: Scope, requisition_id: UUID, read_check: ReadCheck
) -> list[LinkedNode]:
    ref = generated_for(conn, scope, requisition_id)
    if ref is None:
        return []
    return [_project_node(conn, scope, ref.id, "generated_project", "generated")]


def _revisions(
    conn: Conn, scope: Scope, requisition_id: UUID, read_check: ReadCheck
) -> list[LinkedNode]:
    access = _access(conn, scope, requisition_id)
    tones: dict[str, Tone] = {
        "draft": "neutral",
        "pending": "info",
        "applied": "success",
        "rejected": "danger",
        "returned": "warning",
        "withdrawn": "neutral",
    }
    return [
        LinkedNode(
            key=f"revision:{requisition_id}:{r.revision_no}",
            kind="revision",
            id=str(r.revision_no),
            label=f"Revision {r.revision_no} · {r.kind.capitalize()}",
            state=r.status.capitalize(),
            state_tone=tones[r.status],
            href=f"/requisitions/{requisition_id}",
            at=r.created_at,
            relation="revision_of",
            access=access,
        )
        for r in revisions.summaries(conn, scope, requisition_id)
    ]


def _approvals(
    conn: Conn, scope: Scope, requisition_id: UUID, read_check: ReadCheck
) -> list[LinkedNode]:
    access = _access(conn, scope, requisition_id)
    words: dict[str, tuple[str, Tone]] = {
        "approved": ("Approved", "success"),
        "rejected": ("Rejected", "danger"),
        "returned": ("Returned", "warning"),
        "withdrawn": ("Withdrawn", "neutral"),
        "running": ("In review", "info"),
        "superseded": ("Superseded", "neutral"),
    }
    nodes = []
    for instance in instances_for(conn, scope, RequisitionAdapter.document_type, requisition_id):
        word, tone = words[instance.status]
        nodes.append(
            LinkedNode(
                key=f"approval:{instance.id}",
                kind="approval",
                id=str(instance.id),
                label=f"Approval · {word} · {_date(instance.started_at)}",
                state=word,
                state_tone=tone,
                href="/approvals",
                at=instance.started_at,
                relation="approved_by",
                access=access,
            )
        )
    return nodes


def _money(amount: object, currency: str) -> str:
    value = quantize(amount, currency)  # type: ignore[arg-type]
    text = f"{abs(value):,.{currency_exponent(currency)}f}"
    return f"({text})" if value < 0 else text


def _ledger(
    conn: Conn, scope: Scope, requisition_id: UUID, read_check: ReadCheck
) -> list[LinkedNode]:
    relations = {"reservation": "reserves", "release": "releases", "allocation": "allocates"}
    tones: dict[str, Tone] = {"reservation": "info", "release": "neutral", "allocation": "success"}
    nodes = []
    for entry in entries_for_source(conn, scope, "requisition", requisition_id):
        kind = entry.entry_type.value
        word = kind.capitalize()
        nodes.append(
            LinkedNode(
                key=f"ledger_entry:{entry.id}",
                kind="ledger_entry",
                id=str(entry.id),
                label=(
                    f"Ledger entry LE-{entry.id:04d} · {word} "
                    f"{_money(entry.amount, entry.currency)} {entry.currency}"
                ),
                state=word,
                state_tone=tones.get(kind, "neutral"),
                href=f"/projects/{entry.project_id}/dashboard?bucket=all&entry={entry.id}#ledger",
                at=entry.posted_at,
                relation=relations.get(kind, kind),
                amount=format(entry.amount, ".4f"),
                currency=entry.currency,
                access=LinkAccess(
                    permission="ledger.read", scope_type="project", target_id=entry.project_id
                ),
            )
        )
    return nodes


def _attachments(
    conn: Conn, scope: Scope, requisition_id: UUID, read_check: ReadCheck
) -> list[LinkedNode]:
    access = _access(conn, scope, requisition_id)
    tones: dict[str, Tone] = {
        "clean": "success",
        "basic_checks_passed": "success",
        "pending": "info",
    }
    return [
        LinkedNode(
            key=f"attachment:{a.id}",
            kind="attachment",
            id=str(a.id),
            label=f"Attachment · {a.display_name}",
            state=a.scan_status.replace("_", " ").capitalize(),
            state_tone=tones.get(a.scan_status, "danger"),
            href=f"/requisitions/{requisition_id}",
            at=a.uploaded_at,
            relation="attached",
            access=access,
        )
        for a in CollaborationService(conn, scope).attachments_for("requisition", requisition_id)
        if a.deleted_at is None
    ]


def _notes(
    conn: Conn, scope: Scope, requisition_id: UUID, read_check: ReadCheck
) -> list[LinkedNode]:
    access = _access(conn, scope, requisition_id)
    return [
        LinkedNode(
            key=f"note:{n.id}",
            kind="note",
            id=str(n.id),
            label=f"Note · {n.author_email} · {_date(n.created_at)}",
            state="Locked" if n.locked else n.visibility.capitalize(),
            state_tone="neutral",
            href=f"/requisitions/{requisition_id}",
            at=n.created_at,
            relation="noted",
            access=access,
        )
        for n in CollaborationService(conn, scope).notes_for("requisition", requisition_id)
    ]


for _kind, _provider in {
    "project": _project,
    "generated_project": _generated,
    "revision": _revisions,
    "approval": _approvals,
    "ledger_entry": _ledger,
    "attachment": _attachments,
    "note": _notes,
}.items():
    register_link_provider(_kind, _provider)


def _authorized(node: object, read_check: ReadCheck) -> bool:
    if not isinstance(node, LinkedNode):
        logger.error(
            "link provider returned a node without access; omitted",
            extra={"link_kind": getattr(node, "kind", None)},
        )
        return False
    access = node.access
    return read_check(access.permission, access.scope_type, access.target_id)


def links(conn: Conn, scope: Scope, requisition_id: UUID, read_check: ReadCheck) -> LinksRead:
    """Every provider's nodes the caller may read; an unreadable node leaves no trace."""
    nodes: list[LinkedNode] = []
    partial = False
    with tenant_transaction(cast(RlsSession, conn), scope):
        read_row(RequisitionRepository(conn, scope), requisition_id)
        for kind, provider in list(_PROVIDERS.items()):
            try:
                with conn.transaction():  # a failed provider statement must not poison the rest
                    nodes.extend(provider(conn, scope, requisition_id, read_check))
            except Exception:
                logger.exception("link provider failed", extra={"link_kind": kind})
                partial = True
    visible = [
        LinkedRecord.model_validate(n.model_dump(exclude={"access"}))
        for n in nodes
        if _authorized(n, read_check)
    ]
    visible.sort(
        key=lambda n: (
            n.at,
            KIND_ORDER.index(n.kind) if n.kind in KIND_ORDER else len(KIND_ORDER),
            n.key,
        )
    )
    return LinksRead(
        nodes=visible,
        edges=[
            LinkEdge.model_validate(
                {"from": f"requisition:{requisition_id}", "to": n.key, "relation": n.relation}
            )
            for n in visible
            if n.relation is not None
        ],
        partial=partial,
    )


def assert_current_upstream(
    conn: Conn, scope: Scope, requisition_id: UUID, document_version: int
) -> None:
    """WF-001: refuse a downstream record built on a stale, unapproved or amended requisition."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = read_row(RequisitionRepository(conn, scope), requisition_id)
        if (
            row["status"] not in SOURCING_OR_LATER
            or row["pending_amendment_id"] is not None
            or row["version"] != document_version
        ):
            raise conflict(
                "stale_version",
                f"{row['number']} is not ready for this step: it must be approved, unchanged "
                "since version "
                f"{document_version} and have no amendment pending. Reload it and retry.",
            )
        assert_current_approval(
            conn, scope, doc_type(conn, scope, requisition_id), requisition_id, document_version
        )
        # D-M2-25: no sourcing or purchasing document is raised on an expired code.
        expired = codes_read(
            conn,
            scope,
            cast(str | None, row["department_code"]),
            cast(str | None, row["ledger_account_code"]),
        ).expired
        sub = generated_for(conn, scope, requisition_id)
        if not expired and sub is not None:
            expired = ProjectService(conn, scope).get(sub.id).codes_expired
        if expired:
            raise conflict(
                "code_expired",
                f"{row['number']} or its project carries an expired department or ledger "
                "account code. A Project Manager must substitute it before this step.",
            )
