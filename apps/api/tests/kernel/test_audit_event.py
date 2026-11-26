# ruff: noqa: F811
"""E22-S03: the shared audit_event helper writes the row each module wrapper used to write."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import cast
from uuid import UUID, uuid4

import pytest

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from tests.kernel.test_audit import AuditDatabase, audit_database  # noqa: F401

ACTOR = uuid4()
TARGET = uuid4()


@dataclass(frozen=True)
class Invitation:
    id: UUID
    role_code: str


# One case per replaced wrapper: (helper kwargs, the explicit kwargs the wrapper used to pass)
CASES = {
    "collaboration": (
        dict(
            action="note.create",
            target_type="note",
            outcome=Outcome.DENIED,
            after={"a": 1, "b": "x"},
        ),
        dict(
            action="note.create",
            target_type="note",
            outcome=Outcome.DENIED,
            after_source={"a": 1, "b": "x"},
            after_fields=("a", "b"),
        ),
    ),
    "identity": (
        dict(
            action="role.create",
            target_type="role",
            after=Invitation(TARGET, "r"),
            after_fields=("id", "role_code"),
        ),
        dict(
            action="role.create",
            target_type="role",
            after_source=Invitation(TARGET, "r"),
            after_fields=("id", "role_code"),
        ),
    ),
    "invitation": (
        dict(
            action="invitation.send",
            target_type="invitation",
            after=Invitation(TARGET, "r"),
            after_fields=("id", "role_code"),
            ip_address_value="10.1.2.0",
            user_agent="ua",
        ),
        dict(
            action="invitation.send",
            target_type="invitation",
            after_source=Invitation(TARGET, "r"),
            after_fields=("id", "role_code"),
            ip_address_value="10.1.2.0",
            user_agent="ua",
        ),
    ),
    "org": (
        dict(action="org.unit.update", target_type="org", before={"n": "a"}, after={"n": "b"}),
        dict(
            action="org.unit.update",
            target_type="org",
            before_source={"n": "a"},
            before_fields=("n",),
            after_source={"n": "b"},
            after_fields=("n",),
        ),
    ),
    "fiscal": (
        dict(
            action="fiscal.set",
            target_type="fiscal",
            before=None,
            after={"reason": "r"},
            reason="r",
        ),
        dict(
            action="fiscal.set",
            target_type="fiscal",
            before_source=None,
            after_source={"reason": "r"},
            after_fields=("reason",),
            reason="r",
        ),
    ),
    "requisitions": (
        dict(
            action="requisition.submit",
            target_type="requisition",
            before={"s": "draft"},
            after={"s": "submitted"},
        ),
        dict(
            action="requisition.submit",
            target_type="requisition",
            before_source={"s": "draft"},
            before_fields=("s",),
            after_source={"s": "submitted"},
            after_fields=("s",),
        ),
    ),
    "amendments": (
        dict(action="requisition.amend", target_type="requisition", before=None, after={"v": 2}),
        dict(
            action="requisition.amend",
            target_type="requisition",
            after_source={"v": 2},
            after_fields=("v",),
        ),
    ),
    "projects": (
        dict(
            action="project.update", target_type="project", before={"x": 1}, after={"x": 2, "y": 3}
        ),
        dict(
            action="project.update",
            target_type="project",
            before_source={"x": 1},
            before_fields=("x",),
            after_source={"x": 2, "y": 3},
            after_fields=("x", "y"),
        ),
    ),
    "imports": (
        dict(action="import.commit", target_type="import_batch", after={"rows": 3}),
        dict(
            action="import.commit",
            target_type="import_batch",
            after_source={"rows": 3},
            after_fields=("rows",),
        ),
    ),
    "approvals.service": (
        dict(action="workflow.publish", target_type="workflow", before={"v": 1}, after={"v": 2}),
        dict(
            action="workflow.publish",
            target_type="workflow",
            before_source={"v": 1},
            before_fields=("v",),
            after_source={"v": 2},
            after_fields=("v",),
        ),
    ),
    "approvals.decisions": (
        dict(
            action="approval.decision",
            target_type="approval_instance",
            before={"status": "p"},
            after={"decision_id": TARGET, "status": "approved"},
        ),
        dict(
            action="approval.decision",
            target_type="approval_instance",
            before_source={"status": "p"},
            before_fields=("status",),
            after_source={"decision_id": TARGET, "status": "approved"},
            after_fields=("decision_id", "status"),
        ),
    ),
    "approvals.delegation": (
        dict(
            action="approval.delegate",
            target_type="approval_delegation",
            before=None,
            after={"d": "z"},
        ),
        dict(
            action="approval.delegate",
            target_type="approval_delegation",
            after_source={"d": "z"},
            after_fields=("d",),
        ),
    ),
}


@pytest.mark.parametrize("module", CASES)
def test_helper_writes_the_row_the_module_wrapper_wrote(
    audit_database: AuditDatabase,
    module: str,
) -> None:
    helper_kwargs, legacy_kwargs = CASES[module]
    org_id = uuid4()
    connection = cast(AuditConnection, audit_database.connection)
    scope = Scope(org_id)
    actor = AuditActor(ActorKind.USER, ACTOR)
    with correlation_context("req-1"), tenant_transaction(connection, scope):
        via_helper = audit_event(connection, scope, actor=actor, target_id=TARGET, **helper_kwargs)
        legacy = AuditWriter(connection, scope).write(
            actor=actor,
            target_id=TARGET,
            outcome=legacy_kwargs.pop("outcome", Outcome.SUCCESS),
            **legacy_kwargs,
        )
    ignore = {"id", "occurred_at"}
    assert {k: v for k, v in asdict(via_helper).items() if k not in ignore} == {
        k: v for k, v in asdict(legacy).items() if k not in ignore
    }
