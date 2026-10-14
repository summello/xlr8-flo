"""Requisition workflow and exactly-once funding against the production database."""

import asyncio
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import Outcome
from flo.modules.budget.service import reservation_remaining
from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
from flo.modules.requisitions.schemas import RequisitionTransitionCreate
from flo.modules.requisitions.service import RequisitionService, transition
from tests.approvals.test_engine import graph
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import client, login, project, send
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lines import lines_db as lines_db
from tests.requisitions.test_submission_check import change, complete


@pytest.fixture
def lifecycle_db(lines_db):
    db = lines_db
    from flo.api.document_types import register_document_types

    register_document_types()
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = auth.seed_baseline_roles()["approver"]
    user = IdentityId(uuid4())
    db[0].execute(
        "INSERT INTO identity(id,email,password_hash) VALUES(%s,%s,%s)",
        (user, "reviewer@example.test", "$argon2id$test-placeholder"),
    )
    db[0].execute(
        "INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)", (user, db[1])
    )
    auth.grant_role(user, role.id, AuthorizationTarget.organization(db[1]))
    workflow = definition(db)
    draft(db, workflow, graph())
    svc(db).publish(workflow.id, 1, True)
    request_workflow = definition(db, document_type="project_request")
    draft(db, request_workflow, graph())
    svc(db).publish(request_workflow.id, 1, True)
    return db, user


def command(db, row, to="approval_pending", **kwargs):
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    return transition(
        db[0],
        Scope(db[1]),
        row.id,
        RequisitionTransitionCreate(to=to, **kwargs),
        str(current.version),
        db[3],
        lambda p: True,
    )


def edit_justification(db, row, text):
    """A non-material draft edit, so a resubmit has something to submit (REQ-012)."""
    from flo.modules.requisitions.schemas import RequisitionPatch

    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    RequisitionService(db[0], Scope(db[1]), db[3]).update(
        row.id, RequisitionPatch(justification=text), str(current.version), lambda p: True
    )


def funded(case, price="60"):
    db = case[0]
    from tests.requisitions.test_header import unit

    row, _ = complete(db, project(db, unit(db, uuid4().hex)), price=price)
    command(db, row)
    return RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)


def sql(db, query, args=()):
    with tenant_transaction(db[0], Scope(db[1])):
        return db[0].execute(query, args).fetchall()


def outcome(db, row, value, mode=None):
    from flo.modules.approvals.schemas import DecisionWrite
    from flo.modules.approvals.service import decide, get_instance

    current = RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)
    instance = get_instance(db[0], Scope(db[1]), current.approval.instance_id)
    user = (
        db[0].execute("SELECT id FROM identity WHERE email='reviewer@example.test'").fetchone()[0]
    )
    decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[-1].id,
        actor_id=user,
        body=DecisionWrite(
            outcome="return" if value == Outcome.RETURNED else "reject",
            reject_mode=mode,
            comment="Changes required",
            document_version=current.version,
        ),
    )


def test_submit_withdraw_reservation_events_and_cards(lifecycle_db):
    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    assert row.reservation.remaining == "60.0000" and row.approval.status == "running"
    command(db, row, "withdrawn")
    after = RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: False)
    assert after.status == "withdrawn" and after.approval.status == "withdrawn"
    assert after.reservation.remaining == "0.0000" and after.reservation.released == "60.0000"
    assert after.reservation.project_available is None
    assert (
        len(sql(db, "SELECT id FROM requisition_status_event WHERE requisition_id=%s", (row.id,)))
        == 2
    )
    assert sorted(
        sql(
            db,
            "SELECT action FROM audit_log WHERE target_id=%s "
            "AND action IN ('requisition.submit','requisition.transition')",
            (row.id,),
        )
    ) == [("requisition.submit",), ("requisition.transition",)]
    for statement in [
        "UPDATE requisition_status_event SET reason='plant'",
        "DELETE FROM requisition_status_event",
        "TRUNCATE requisition_status_event",
    ]:
        with pytest.raises(Exception, match="append.only"):
            with db[0].transaction():
                db[0].execute(statement)


@pytest.mark.parametrize(
    "value,mode,status,remaining",
    [
        (Outcome.REJECTED, "permanent", "rejected", "0.0000"),
        (Outcome.REJECTED, "return_to_draft", "draft", "0.0000"),
        (Outcome.RETURNED, None, "draft", "60.0000"),
    ],
)
def test_adapter_outcomes(lifecycle_db, value, mode, status, remaining):
    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    outcome(db, row, value, mode)
    assert RequisitionService(db[0], Scope(db[1])).get(row.id).status == status
    assert (
        format(
            reservation_remaining(db[0], Scope(db[1]), row.reservation.entry_id).remaining, ".4f"
        )
        == remaining
    )


def test_return_keeps_reservation_and_resubmit_rounds(lifecycle_db):
    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    outcome(db, row, Outcome.RETURNED)
    edit_justification(db, row, "Clarified after the return")
    command(db, row)
    after = RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)
    assert (
        after.reservation.entry_id == row.reservation.entry_id
        and after.reservation.funding_round == 1
    )
    outcome(db, row, Outcome.REJECTED, "return_to_draft")
    edit_justification(db, row, "Clarified after the rejection")
    command(db, row)
    after = RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)
    assert after.reservation.funding_round == 2
    outcome(db, row, Outcome.REJECTED, "return_to_draft")
    assert reservation_remaining(db[0], Scope(db[1]), after.reservation.entry_id).remaining == 0


def test_requester_only_cancel_reason_and_notes(lifecycle_db):
    db, user = lifecycle_db
    row = funded(lifecycle_db)
    identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = identity.create_role("withdraw-holder", "Withdrawal holder")
    identity.grant_permission(role.id, "requisition.withdraw")
    identity.grant_role(user, role.id, AuthorizationTarget.organization(db[1]))
    with pytest.raises(ProblemError) as error:
        transition(
            db[0],
            Scope(db[1]),
            row.id,
            RequisitionTransitionCreate(to="withdrawn"),
            str(row.version),
            user,
            lambda p: True,
        )
    assert error.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(ProblemError) as error:
        command(db, row, "cancelled")
    assert error.value.code == ErrorCode.VALIDATION_FAILED
    command(db, row, "cancelled", reason="No longer required")
    assert RequisitionService(db[0], Scope(db[1])).get(row.id).status == "cancelled"


def test_standalone_and_routing_rollback(lifecycle_db):
    db, _ = lifecycle_db
    row, _ = complete(db)
    command(db, row)
    assert sql(db, "SELECT id FROM ledger_entry WHERE source_id=%s", (row.id,)) == []
    assert sql(
        db,
        "SELECT id FROM catalogue_event WHERE source_id IN "
        "(SELECT id FROM requisition_line WHERE requisition_id=%s)",
        (row.id,),
    ) == [(1,)]
    row, _ = complete(db)
    command(db, row, note="Please review")
    assert RequisitionService(db[0], Scope(db[1])).get(row.id).status == "approval_pending"
    assert sql(
        db,
        "SELECT revision_no,kind,status,note FROM requisition_revision WHERE requisition_id=%s",
        (row.id,),
    ) == [(1, "submission", "applied", "Please review")]


def test_foreign_lifecycle_routes(lifecycle_db):
    db, _ = lifecycle_db
    from tests.requisitions.test_header import draft as create_draft

    foreign = create_draft(db, p=project(db, org=db[2]), org=db[2])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, path, data in [
                ("POST", "transitions", {"to": "withdrawn"}),
                ("GET", "events", None),
                ("GET", "transitions/available", None),
            ]:
                response = await send(
                    browser, method, f"/{foreign.id}/{path}", data, extra={"If-Match": "1"}
                )
                assert response.status_code == 404, response.text

    asyncio.run(scenario())


def test_http_idempotency_stale_notes_and_cancel_available(lifecycle_db):
    db, _ = lifecycle_db
    row, _ = complete(db, project(db), price="60")
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            key = uuid4().hex
            kwargs = {
                "data": {"to": "approval_pending"},
                "key": key,
                "extra": {"If-Match": str(current.version)},
            }
            first = await send(browser, "POST", f"/{row.id}/transitions", **kwargs)
            assert first.status_code == 200, first.text
            replay = await send(browser, "POST", f"/{row.id}/transitions", **kwargs)
            assert replay.json() == first.json()
            again = await send(
                browser,
                "POST",
                f"/{row.id}/transitions",
                {"to": "approval_pending"},
                extra={"If-Match": first.headers["etag"]},
            )
            assert (
                again.status_code == 409
                and again.json()["checks"]["problem"] == "invalid_transition"
            )
            options = await send(browser, "GET", f"/{row.id}/transitions/available")
            assert next(x for x in options.json() if x["to"] == "cancelled")["allowed"]
            history = await send(browser, "GET", f"/{row.id}/events")
            assert (
                len(history.json()) == 1
                and history.json()[0]["actor_email"] == "admin@example.test"
            )

    asyncio.run(scenario())
    assert (
        len(
            sql(
                db,
                "SELECT id FROM ledger_entry WHERE source_id=%s AND entry_type='reservation'",
                (row.id,),
            )
        )
        == 1
    )


def test_no_workflow_submit_writes_nothing(lines_db):
    db = lines_db
    row, _ = complete(db, project(db), price="60")
    with pytest.raises(ProblemError) as error:
        command(db, row)
    assert error.value.checks["problem"] == "submission_invalid"
    assert any(x.field == "routing/workflow" for x in error.value.errors)
    assert sql(db, "SELECT id FROM ledger_entry WHERE source_id=%s", (row.id,)) == []
    assert (
        sql(
            db,
            "SELECT id FROM catalogue_event WHERE source_id IN "
            "(SELECT id FROM requisition_line WHERE requisition_id=%s)",
            (row.id,),
        )
        == []
    )
    assert (
        sql(db, "SELECT id FROM requisition_status_event WHERE requisition_id=%s", (row.id,)) == []
    )
    assert RequisitionService(db[0], Scope(db[1])).get(row.id).status == "draft"


def test_all_state_pairs_and_permission_guards(lifecycle_db):
    from typing import get_args

    from flo.modules.requisitions.lifecycle import (
        TRANSITIONS,
        _downstream,
        assert_completable,
        authorize,
        register_downstream_counter,
    )
    from flo.modules.requisitions.schemas import RequisitionStatus

    db, _ = lifecycle_db
    row, _ = complete(db)
    for source in get_args(RequisitionStatus.__value__):
        change(db, "requisition", row.id, "status=%s", (source,))
        for to in get_args(RequisitionStatus.__value__):
            if (source, to) in TRANSITIONS:
                authorize({"status": source, "requester_id": db[3]}, to, db[3], lambda p: True)
                with pytest.raises(ProblemError) as error:
                    authorize({"status": source, "requester_id": db[3]}, to, db[3], lambda p: False)
                assert error.value.code == ErrorCode.FORBIDDEN
            else:
                with pytest.raises(ProblemError) as error:
                    command(db, row, to)
                assert error.value.checks["problem"] == "invalid_transition"

    def fn(conn, scope, id):
        return 2

    register_downstream_counter(fn)
    try:
        with pytest.raises(ProblemError) as error:
            assert_completable(db[0], Scope(db[1]), row.id)
        assert (
            error.value.checks["problem"] == "completion_blocked"
            and "2 actionable" in error.value.detail
        )
    finally:
        _downstream.remove(fn)
    assert_completable(db[0], Scope(db[1]), row.id)


def test_returned_draft_larger_estimate_and_project_move(lifecycle_db):
    from flo.modules.requisitions.schemas import RequisitionPatch

    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    outcome(db, row, Outcome.RETURNED)
    from flo.modules.requisitions.schemas import LineCreate
    from tests.requisitions.test_lines import BODY, service

    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    line = service(db).list_lines(row.id).rows[0]
    service(db).write(
        row.id,
        str(current.version),
        line_id=line.id,
        body=LineCreate(**(BODY | {"quantity": "1", "unit_price": "80"})),
    )
    command(db, row)
    current = RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)
    assert current.reservation.funding_round == 2 and current.reservation.remaining == "80.0000"
    assert reservation_remaining(db[0], Scope(db[1]), row.reservation.entry_id).remaining == 0
    outcome(db, row, Outcome.RETURNED)
    from tests.requisitions.test_header import org_svc

    target = project(db, org_svc(db).get_unit(row.bu_id))
    change(db, "project", target.id, "status='active'")
    sql(
        db,
        "UPDATE project_balance SET allocated=100 WHERE project_id=%s RETURNING allocated",
        (target.id,),
    )
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    RequisitionService(db[0], Scope(db[1]), db[3]).update(
        row.id, RequisitionPatch(project_id=target.id), str(current.version), lambda p: True
    )
    command(db, row)
    current = RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)
    assert current.reservation.funding_round == 3 and current.project_id == target.id
    assert (
        sql(db, "SELECT reserved FROM project_balance WHERE project_id=%s", (row.project_id,))[0][0]
        == 0
    )
    assert (
        sql(db, "SELECT reserved FROM project_balance WHERE project_id=%s", (target.id,))[0][0]
        == 80
    )


def test_lifecycle_route_concealment_and_view_only(lifecycle_db):
    db, _ = lifecycle_db
    row = funded(lifecycle_db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                )
            response = await send(
                browser,
                "POST",
                f"/{row.id}/transitions",
                {"to": "withdrawn"},
                extra={"If-Match": str(row.version)},
            )
            assert response.status_code == 404, response.text
            with tenant_transaction(db[0], Scope(db[1])):
                identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
                role = identity.create_role("read-only", "Read only")
                identity.grant_permission(role.id, "requisition.view")
                identity.grant_role(db[3], role.id, AuthorizationTarget.organization(db[1]))
            response = await send(
                browser,
                "POST",
                f"/{row.id}/transitions",
                {"to": "withdrawn"},
                extra={"If-Match": str(row.version)},
            )
            assert response.status_code == 403, response.text
            response = await send(browser, "GET", f"/{row.id}")
            assert response.status_code == 200
            assert response.json()["reservation"]["project_available"] is None
            options = await send(browser, "GET", f"/{row.id}/transitions/available")
            assert not any(x["allowed"] for x in options.json())

    asyncio.run(scenario())


@pytest.mark.parametrize("problem", ["no_workflow", "no_eligible_approver", "route_blocked"])
def test_route_changed_after_preview_rolls_back(lifecycle_db, monkeypatch, problem):
    from flo.modules.requisitions import lifecycle

    db, _ = lifecycle_db
    row, _ = complete(db, project(db), price="60")
    original = lifecycle.routing_check

    def changed(conn, scope, id, check):
        result = original(conn, scope, id, check)
        if problem == "no_workflow":
            conn.execute(
                "UPDATE workflow_version SET status='superseded' "
                "WHERE org_id=%s AND status='published'",
                (scope.org_id,),
            )
        elif problem == "no_eligible_approver":
            conn.execute("DELETE FROM user_role WHERE org_id=%s", (scope.org_id,))
        else:
            publish_blocked_route(db)
        return result

    monkeypatch.setattr(lifecycle, "routing_check", changed)
    with pytest.raises(ProblemError) as error:
        command(db, row)
    assert error.value.checks["problem"] == problem
    assert sql(db, "SELECT id FROM ledger_entry WHERE source_id=%s", (row.id,)) == []
    assert (
        sql(
            db,
            "SELECT id FROM catalogue_event WHERE source_id IN "
            "(SELECT id FROM requisition_line WHERE requisition_id=%s)",
            (row.id,),
        )
        == []
    )
    assert (
        sql(db, "SELECT id FROM requisition_status_event WHERE requisition_id=%s", (row.id,)) == []
    )
    assert RequisitionService(db[0], Scope(db[1])).get(row.id).status == "draft"


def test_if_match_and_note_guards(lifecycle_db):
    db, _ = lifecycle_db
    row, _ = complete(db, project(db), price="60")
    for version, code, label in [
        (None, ErrorCode.BAD_REQUEST, None),
        ("bad", ErrorCode.BAD_REQUEST, None),
        ("0", ErrorCode.CONFLICT, "stale_version"),
    ]:
        with pytest.raises(ProblemError) as error:
            transition(
                db[0],
                Scope(db[1]),
                row.id,
                RequisitionTransitionCreate(to="approval_pending"),
                version,
                db[3],
                lambda p: True,
            )
        assert error.value.code == code
        if label:
            assert error.value.checks["problem"] == label
    with pytest.raises(ProblemError):
        RequisitionTransitionCreate(to="withdrawn", note="not a submission")
    with pytest.raises(ProblemError):
        RequisitionTransitionCreate(to="approval_pending", note=" ")


@pytest.mark.parametrize(
    "source",
    [
        "draft",
        "approval_pending",
        "sourcing_in_progress",
        "sourced",
        "awarded",
        "open_for_purchase",
        "completed",
        "rejected",
        "withdrawn",
        "cancelled",
        "expired",
    ],
)
@pytest.mark.parametrize(
    "target",
    [
        "draft",
        "approval_pending",
        "sourcing_in_progress",
        "sourced",
        "awarded",
        "open_for_purchase",
        "completed",
        "rejected",
        "withdrawn",
        "cancelled",
        "expired",
    ],
)
def test_parametrized_transition_table(source, target):
    from flo.modules.requisitions.lifecycle import TRANSITIONS, authorize

    actor = uuid4()
    row = {"status": source, "requester_id": actor}
    if (source, target) in TRANSITIONS:
        authorize(row, target, actor, lambda permission: True)
    else:
        with pytest.raises(ProblemError) as error:
            authorize(row, target, actor, lambda permission: True)
        assert error.value.checks["problem"] == "invalid_transition"


def test_available_matches_post_every_status_and_actor(lifecycle_db):
    from typing import get_args

    from flo.modules.requisitions.schemas import RequisitionStatus
    from flo.modules.requisitions.service import available

    db, administrator = lifecycle_db
    identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = identity.create_role("lifecycle-admin", "Lifecycle administrator")
    for permission in ["requisition.submit", "requisition.withdraw", "requisition.cancel"]:
        identity.grant_permission(role.id, permission)
    identity.grant_role(administrator, role.id, AuthorizationTarget.organization(db[1]))
    actors = [
        (db[3], {"requisition.submit", "requisition.withdraw"}),
        (administrator, set()),
        (administrator, {"requisition.submit", "requisition.withdraw", "requisition.cancel"}),
        (administrator, set()),
    ]
    for status in get_args(RequisitionStatus.__value__):
        row, _ = complete(db)
        if status == "approval_pending":
            command(db, row)
        else:
            change(db, "requisition", row.id, "status=%s", (status,))
        row = RequisitionService(db[0], Scope(db[1])).get(row.id)
        for actor, permissions in actors:
            options = available(db[0], Scope(db[1]), row.id, actor, lambda p: p in permissions)
            for option in options:
                succeeded = False
                with db[0].transaction(force_rollback=True):
                    try:
                        transition(
                            db[0],
                            Scope(db[1]),
                            row.id,
                            RequisitionTransitionCreate(
                                to=option.to,
                                reason="Authorized cancellation"
                                if option.to == "cancelled"
                                else None,
                            ),
                            str(row.version),
                            actor,
                            lambda p: p in permissions,
                        )
                        succeeded = True
                    except ProblemError:
                        pass
                assert succeeded == option.allowed, (status, actor, permissions, option)


def test_transition_rechecks_current_project_permission(lifecycle_db, monkeypatch):
    from flo.modules.identity.service import ScopeType
    from tests.requisitions.test_header import org_svc

    db, _ = lifecycle_db
    old = project(db)
    row, _ = complete(db, old, price="60")
    new = project(db, org_svc(db).get_unit(row.bu_id))
    change(db, "project", new.id, "status='active'")
    sql(
        db,
        "UPDATE project_balance SET allocated=100 WHERE project_id=%s RETURNING allocated",
        (new.id,),
    )
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
    identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = identity.create_role("old-project-submitter", "Old project submitter")
    for permission in ["requisition.view", "requisition.submit"]:
        identity.grant_permission(role.id, permission)
    identity.grant_role(db[3], role.id, AuthorizationTarget(ScopeType.PROJECT, old.id))
    original = RequisitionService.transition

    def concurrent_move(self, id, body, version, permitted, can_view_budget, can_edit=None):
        change(db, "requisition", id, "project_id=%s,version=version+1", (new.id,))
        return original(self, id, body, version, permitted, can_view_budget, can_edit)

    monkeypatch.setattr(RequisitionService, "transition", concurrent_move)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            result = await send(
                browser,
                "POST",
                f"/{row.id}/transitions",
                {"to": "approval_pending"},
                extra={"If-Match": str(current.version + 1)},
            )
            assert result.status_code == 403, result.text

    asyncio.run(scenario())
    assert RequisitionService(db[0], Scope(db[1])).get(row.id).status == "draft"
    assert sql(db, "SELECT id FROM ledger_entry WHERE source_id=%s", (row.id,)) == []


def test_reservation_change_after_discovery_requires_retry(lifecycle_db, monkeypatch):
    from decimal import Decimal

    from flo.modules.budget.service import release_reservation
    from flo.modules.requisitions import lifecycle

    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    outcome(db, row, Outcome.RETURNED)
    original = lifecycle.check_submission

    def changed(conn, scope, id, **kwargs):
        check = original(conn, scope, id, **kwargs)
        release_reservation(
            conn,
            scope,
            reservation_entry_id=row.reservation.entry_id,
            amount=Decimal(1),
            actor_id=db[3],
            reason="concurrent release",
            idempotency_key=uuid4().hex,
        )
        return check

    monkeypatch.setattr(lifecycle, "check_submission", changed)
    with pytest.raises(ProblemError) as error:
        command(db, row)
    assert error.value.checks["problem"] == "stale_version"
    assert error.value.detail == "The reservation changed. Retry."
    assert reservation_remaining(db[0], Scope(db[1]), row.reservation.entry_id).remaining == 60
    assert RequisitionService(db[0], Scope(db[1])).get(row.id).status == "draft"


@pytest.mark.parametrize("problem", ["no_eligible_approver", "route_blocked"])
def test_routing_preview_problems_write_nothing(lifecycle_db, problem):
    db, _ = lifecycle_db
    row, _ = complete(db, project(db), price="60")
    with tenant_transaction(db[0], Scope(db[1])):
        if problem == "no_eligible_approver":
            db[0].execute("DELETE FROM user_role WHERE org_id=%s", (db[1],))
        else:
            publish_blocked_route(db)
    with pytest.raises(ProblemError) as error:
        command(db, row)
    assert error.value.checks["problem"] == "submission_invalid"
    assert any(p.field == "routing/workflow" for p in error.value.errors)
    assert RequisitionService(db[0], Scope(db[1])).get(row.id).status == "draft"
    assert sql(db, "SELECT id FROM ledger_entry WHERE source_id=%s", (row.id,)) == []
    assert (
        sql(db, "SELECT id FROM requisition_status_event WHERE requisition_id=%s", (row.id,)) == []
    )
    assert sql(db, "SELECT id FROM catalogue_event") == []


def publish_blocked_route(db):
    from flo.modules.approvals.schemas import WorkflowVersionWrite

    workflow_id = sql(db, "SELECT definition_id FROM workflow_version WHERE status='published'")[0][
        0
    ]
    blocked = graph(conditional=True)
    blocked["edges"][1]["when"]["value"]["currency"] = "INR"
    version = svc(db).create_version(workflow_id, WorkflowVersionWrite(graph=blocked))
    svc(db).publish(workflow_id, version.version, True)
