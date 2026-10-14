"""Amendments to an approved requisition: the approved version stays in force (E09-S08)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import assert_current_approval, decide, get_instance
from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
from flo.modules.requisitions import amendments
from flo.modules.requisitions.schemas import (
    AmendmentHeaderPut,
    AmendmentPut,
    AmendmentSubmit,
    ReplacementLine,
    RequisitionTransitionCreate,
)
from flo.modules.requisitions.service import RequisitionService, transition
from tests.requisitions.test_approval_effects import approve, drift, linked, reviewer
from tests.requisitions.test_approval_effects import state as state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import client, connection_url, login, project, send, unit
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lifecycle import sql
from tests.requisitions.test_lines import BODY, service
from tests.requisitions.test_lines import lines_db as lines_db


def approved(db, price="60", available="110"):
    p = project(db, unit(db, uuid4().hex))
    row = linked(db, p, price=price, available=available)
    approve(db, row)
    return p, state(db, row.id)


def opened(db, row):
    return amendments.create(db[0], Scope(db[1]), row.id, str(row.version), db[3])


def propose(db, row, revision_no, version, quantity="1", price="60", justification=None):
    line = service(db).list_lines(row.id).rows[0]
    header = {"justification": justification} if justification else {}
    return amendments.put(
        db[0],
        Scope(db[1]),
        row.id,
        revision_no,
        AmendmentPut(
            header=AmendmentHeaderPut(**header),
            lines=[
                ReplacementLine(
                    line_id=line.id, **(BODY | {"quantity": quantity, "unit_price": price})
                )
            ],
        ),
        str(version),
        db[3],
    )


def submit(db, row, revision_no, version, note=None):
    amendments.submit(
        db[0], Scope(db[1]), row.id, revision_no, AmendmentSubmit(note=note), str(version), db[3]
    )


def amend(db, row, price, justification=None, note=None):
    draft = opened(db, row)
    draft = propose(
        db, row, draft.revision_no, draft.version, price=price, justification=justification
    )
    submit(db, row, draft.revision_no, draft.version, note)
    return draft.revision_no


def revision(db, id, no):
    return sql(
        db,
        "SELECT status,note FROM requisition_revision WHERE requisition_id=%s AND revision_no=%s",
        (id, no),
    )[0]


def current_check(db, id):
    now = state(db, id)
    assert_current_approval(db[0], Scope(db[1]), "requisition", id, now.version)


def test_non_material_amendment_is_applied_immediately(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)
    instances = sql(db, "SELECT count(*) FROM approval_instance WHERE document_id=%s", (row.id,))
    no = amend(db, row, "60", justification="Reworded for the auditors", note="Wording only")
    after = state(db, row.id)
    assert after.version == row.version + 1 and after.amendment is None
    assert sql(db, "SELECT justification FROM requisition WHERE id=%s", (row.id,)) == [
        ("Reworded for the auditors",)
    ]
    assert revision(db, row.id, no) == ("applied", "Wording only")
    assert (
        sql(db, "SELECT count(*) FROM approval_instance WHERE document_id=%s", (row.id,))
        == instances
    )
    current_check(db, row.id)
    assert sql(
        db,
        "SELECT reason FROM requisition_status_event WHERE requisition_id=%s "
        "ORDER BY id DESC LIMIT 1",
        (row.id,),
    ) == [("amendment applied",)]


def test_material_increase_stays_pending_until_approved(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db)  # 60 reserved, 50 available
    no = amend(db, row, "100", note="Supplier raised the price")
    pending = state(db, row.id)
    assert pending.status == "sourcing_in_progress" and pending.amendment.status == "pending"
    assert pending.reservation.remaining == "60.0000" and pending.reservation.funding_round == 1
    with pytest.raises(ProblemError) as error:
        current_check(db, row.id)
    assert error.value.checks["problem"] == "stale_version"
    instance = get_instance(db[0], Scope(db[1]), pending.approval.instance_id)
    assert instance.status == "running" and instance.document_id == row.id
    assert sql(db, "SELECT estimate FROM requisition WHERE id=%s", (row.id,)) == [(Decimal(60),)]
    approve(db, row)
    done = state(db, row.id)
    assert done.estimate == "100.0000" and done.amendment is None
    assert done.reservation.remaining == "100.0000" and done.reservation.funding_round == 2
    assert revision(db, row.id, no) == ("applied", "Supplier raised the price")
    current_check(db, row.id)
    # The reservation lives on the sub-project (E09-S16); the main project holds none.
    assert sql(db, "SELECT sum(reserved) FROM project_balance") == [(Decimal(100),)]
    assert [
        e[0]
        for e in sql(
            db,
            "SELECT reason FROM requisition_status_event WHERE requisition_id=%s AND "
            "from_status=to_status ORDER BY id",
            (row.id,),
        )
    ] == ["amendment approved"]
    assert drift(db) == []


def test_increase_beyond_available_funds_fails_the_decision_and_changes_nothing(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db)
    amend(db, row, "100")
    sql(db, "UPDATE project_balance SET allocated=20 WHERE project_id=%s RETURNING 1", (p.id,))
    with pytest.raises(ProblemError) as error:
        approve(db, row)
    assert error.value.code == ErrorCode.INSUFFICIENT_BUDGET
    after = state(db, row.id)
    assert after.estimate == "60.0000" and after.amendment.status == "pending"
    assert after.reservation.remaining == "60.0000" and after.reservation.funding_round == 1
    assert get_instance(db[0], Scope(db[1]), after.approval.instance_id).status == "running"


def test_increase_is_prechecked_at_submit(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)  # 50 available
    draft = opened(db, row)
    draft = propose(db, row, draft.revision_no, draft.version, price="200")
    with pytest.raises(ProblemError) as error:
        submit(db, row, draft.revision_no, draft.version)
    assert error.value.checks["problem"] == "submission_invalid"
    assert [p.category for p in error.value.problems] == ["funds"]
    assert revision(db, row.id, draft.revision_no)[0] == "draft"
    assert state(db, row.id).amendment.can_continue is False  # no permission callback supplied


def test_decrease_releases_the_round_once_and_reserves_the_new_total(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db, price="100", available="150")
    amend(db, row, "70")
    assert state(db, row.id).reservation.remaining == "100.0000"
    approve(db, row)
    done = state(db, row.id)
    assert done.reservation.remaining == "70.0000" and done.reservation.funding_round == 2
    keys = sql(
        db,
        "SELECT entry_type,idempotency_key,amount FROM ledger_entry WHERE source_id=%s "
        "AND entry_type<>'transfer' ORDER BY id",
        (row.id,),
    )
    assert [(k[0], k[1]) for k in keys] == [
        ("reservation", f"req:{row.id}:reserve:1"),
        ("release", f"req:{row.id}:release:1:approved"),
        ("reservation", f"req:{row.id}:reserve:1:sub"),
        ("release", f"req:{row.id}:release:1:resubmit"),
        ("reservation", f"req:{row.id}:reserve:2"),
    ]
    assert sql(db, "SELECT sum(reserved) FROM project_balance") == [(Decimal(70),)]
    assert drift(db) == []


@pytest.mark.parametrize("decision", ["reject", "return"])
def test_rejected_or_returned_amendment_changes_nothing_and_allows_another(lifecycle_db, decision):
    db, _ = lifecycle_db
    _, row = approved(db)
    no = amend(db, row, "100")
    current = state(db, row.id)
    instance = get_instance(db[0], Scope(db[1]), current.approval.instance_id)
    decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[-1].id,
        actor_id=reviewer(db),
        body=DecisionWrite(
            outcome=decision,
            reject_mode="permanent" if decision == "reject" else None,
            comment="No",
            document_version=current.version,
        ),
    )
    after = state(db, row.id)
    assert after.status == "sourcing_in_progress" and after.amendment is None
    assert after.estimate == "60.0000" and after.reservation.remaining == "60.0000"
    assert revision(db, row.id, no)[0] == ("rejected" if decision == "reject" else "returned")
    current_check(db, row.id)  # the approved version stays in force
    assert opened(db, after).revision_no == no + 1


def test_cancel_with_an_amendment_running_withdraws_it_first(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db)
    no = amend(db, row, "100")
    running = state(db, row.id).approval.instance_id
    transition(
        db[0],
        Scope(db[1]),
        row.id,
        RequisitionTransitionCreate(to="cancelled", reason="Project was cancelled today"),
        str(state(db, row.id).version),
        db[3],
        lambda permission: True,
    )
    assert get_instance(db[0], Scope(db[1]), running).status == "withdrawn"
    assert revision(db, row.id, no)[0] == "withdrawn"
    after = state(db, row.id)
    assert after.status == "cancelled" and after.amendment is None
    assert sql(
        db,
        "SELECT amount,reason FROM ledger_entry WHERE source_id=%s AND entry_type='release'",
        (row.id,),
    ) == [(Decimal(-60), "approved"), (Decimal(-60), "cancelled")]
    assert sql(db, "SELECT sum(reserved) FROM project_balance") == [(Decimal(0),)]


def test_requester_withdraws_a_running_amendment_and_others_cannot(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)
    no = amend(db, row, "100")
    with pytest.raises(ProblemError) as error:
        amendments.withdraw_amendment(
            db[0], Scope(db[1]), row.id, no, reviewer(db), lambda permission: True
        )
    assert error.value.code == ErrorCode.FORBIDDEN
    amendments.withdraw_amendment(db[0], Scope(db[1]), row.id, no, db[3], lambda permission: True)
    after = state(db, row.id)
    assert after.amendment is None and after.estimate == "60.0000"
    assert revision(db, row.id, no)[0] == "withdrawn"
    with pytest.raises(ProblemError) as error:
        amendments.withdraw_amendment(
            db[0], Scope(db[1]), row.id, no, db[3], lambda permission: True
        )
    assert error.value.checks["problem"] == "invalid_transition"


def test_guards_project_change_one_open_nothing_changed_delete_and_status(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db)
    draft = opened(db, row)
    with pytest.raises(ProblemError) as error:
        opened(db, row)
    assert error.value.checks["problem"] == "amendment_open"
    with pytest.raises(ProblemError) as error:
        amendments.put(
            db[0],
            Scope(db[1]),
            row.id,
            draft.revision_no,
            AmendmentPut(header=AmendmentHeaderPut(project_id=uuid4()), lines=[]),
            str(draft.version),
            db[3],
        )
    assert error.value.code == ErrorCode.VALIDATION_FAILED
    assert "withdraw it and raise a new requisition" in error.value.detail
    with pytest.raises(ProblemError) as error:
        amendments.put(
            db[0], Scope(db[1]), row.id, draft.revision_no, AmendmentPut(lines=[]), "99", db[3]
        )
    assert error.value.checks["problem"] == "stale_version"
    line = service(db).list_lines(row.id).rows[0]
    same = amendments.put(
        db[0],
        Scope(db[1]),
        row.id,
        draft.revision_no,
        AmendmentPut(
            lines=[
                ReplacementLine(line_id=line.id, **(BODY | {"quantity": "1", "unit_price": "60"}))
            ]
        ),
        str(draft.version),
        db[3],
    )
    with pytest.raises(ProblemError) as error:
        submit(db, row, same.revision_no, same.version)
    assert error.value.checks["problem"] == "nothing_changed"
    amendments.delete(db[0], Scope(db[1]), row.id, same.revision_no, db[3])
    assert sql(db, "SELECT count(*) FROM requisition_revision WHERE kind='amendment'", ()) == [(0,)]
    with pytest.raises(ProblemError) as error:
        amendments.current(db[0], Scope(db[1]), row.id)
    assert error.value.code == ErrorCode.NOT_FOUND
    # A requisition that is not approved cannot be amended.
    from tests.requisitions.test_lifecycle import funded

    drafted = funded(lifecycle_db)
    with pytest.raises(ProblemError) as error:
        amendments.create(db[0], Scope(db[1]), drafted.id, str(drafted.version), db[3])
    assert error.value.checks["problem"] == "invalid_transition"


def test_submitted_amendment_cannot_be_edited_or_deleted(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)
    no = amend(db, row, "100")
    pending = amendments.current(db[0], Scope(db[1]), row.id)
    for action in (
        lambda: propose(db, row, no, pending.version, price="90"),
        lambda: amendments.delete(db[0], Scope(db[1]), row.id, no, db[3]),
    ):
        with pytest.raises(ProblemError) as error:
            action()
        assert error.value.checks["problem"] == "not_editable"


def on_own_connection(db, work, barrier):
    with (
        psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
        correlation_context("amendment-race"),
    ):
        barrier.wait(timeout=10)
        try:
            work(conn)
            return "success"
        except ProblemError as exc:
            assert exc.code == ErrorCode.CONFLICT, exc
            return exc.checks["problem"]


def test_concurrent_opens_and_puts_have_one_winner(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)
    version = str(state(db, row.id).version)
    barrier = Barrier(2)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda _: on_own_connection(
                    db,
                    lambda conn: amendments.create(conn, Scope(db[1]), row.id, version, db[3]),
                    barrier,
                ),
                range(2),
            )
        )
    assert sorted(results) == ["amendment_open", "success"]
    draft = amendments.current(db[0], Scope(db[1]), row.id)
    line = service(db).list_lines(row.id).rows[0]
    body = AmendmentPut(
        lines=[ReplacementLine(line_id=line.id, **(BODY | {"quantity": "1", "unit_price": "75"}))]
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda _: on_own_connection(
                    db,
                    lambda conn: amendments.put(
                        conn,
                        Scope(db[1]),
                        row.id,
                        draft.revision_no,
                        body,
                        str(draft.version),
                        db[3],
                    ),
                    barrier,
                ),
                range(2),
            )
        )
    assert sorted(results) == ["stale_version", "success"]


def test_stop_racing_a_decision_on_the_amendment_never_deadlocks(lifecycle_db):
    db, _ = lifecycle_db
    for _ in range(5):
        _, row = approved(db)
        amend(db, row, "100")
        current = state(db, row.id)
        instance = get_instance(db[0], Scope(db[1]), current.approval.instance_id)
        barrier = Barrier(2)

        def stop(conn):
            transition(
                conn,
                Scope(db[1]),
                row.id,
                RequisitionTransitionCreate(to="cancelled", reason="Project was cancelled today"),
                str(current.version),
                db[3],
                lambda permission: True,
            )

        def decision(conn):
            decide(
                conn,
                Scope(db[1]),
                task_id=instance.tasks[-1].id,
                actor_id=reviewer(db),
                body=DecisionWrite(outcome="approve", document_version=current.version),
            )

        def other_outcomes(job):
            try:
                return on_own_connection(db, job, barrier)
            except ProblemError as exc:  # the decision may lose with a task_closed conflict
                return exc.code.value

        with ThreadPoolExecutor(2) as pool:
            results = [
                f.result(timeout=60)
                for f in [pool.submit(other_outcomes, stop), pool.submit(other_outcomes, decision)]
            ]
        assert not any("40P01" in str(r) or "deadlock" in str(r) for r in results), results
        assert drift(db) == []


def test_routes_conceal_foreign_tenants_and_refuse_view_only_users(lifecycle_db):
    db, _ = lifecycle_db
    from tests.requisitions.test_header import draft as create_draft

    _, row = approved(db)
    foreign = create_draft(db, p=project(db, org=db[2]), org=db[2])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, suffix, data in [
                ("GET", "revisions", None),
                ("GET", "changes", None),
                ("POST", "amendments", None),
                ("GET", "amendments/current", None),
                ("PUT", "amendments/1", {"lines": []}),
                ("DELETE", "amendments/1", None),
                ("POST", "amendments/1/submit", {}),
                ("POST", "amendments/1/withdraw", None),
            ]:
                response = await send(
                    browser, method, f"/{foreign.id}/{suffix}", data, extra={"If-Match": "1"}
                )
                assert response.status_code == 404, (suffix, response.text)
            created = await send(
                browser, "POST", f"/{row.id}/amendments", None, extra={"If-Match": str(row.version)}
            )
            assert created.status_code == 201 and created.headers["etag"] == '"1"'
            assert created.json()["revision_no"] == 2
            on_record = await send(browser, "GET", f"/{row.id}")
            assert on_record.json()["amendment"] == {
                "revision_no": 2,
                "status": "draft",
                "can_continue": True,
            }
            listed = await send(browser, "GET", f"/{row.id}/revisions")
            assert [r["revision_no"] for r in listed.json()] == [2, 1]
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                )
                identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
                role = identity.create_role("read-only", "Read only")
                identity.grant_permission(role.id, "requisition.view")
                identity.grant_role(db[3], role.id, AuthorizationTarget.organization(db[1]))
            for method, suffix, data in [
                ("POST", "amendments", None),
                ("PUT", "amendments/2", {"lines": []}),
                ("DELETE", "amendments/2", None),
                ("POST", "amendments/2/submit", {}),
                ("POST", "amendments/2/withdraw", None),
            ]:
                response = await send(
                    browser, method, f"/{row.id}/{suffix}", data, extra={"If-Match": "1"}
                )
                assert response.status_code == 403, (suffix, response.text)
            assert (await send(browser, "GET", f"/{row.id}/amendments/current")).status_code == 200
            assert (await send(browser, "GET", f"/{row.id}")).json()["amendment"][
                "can_continue"
            ] is False

    asyncio.run(scenario())


def test_summaries_carry_the_requesters_note(lifecycle_db):
    db, _ = lifecycle_db
    from flo.modules.requisitions.service import RequisitionAdapter

    _, row = approved(db)
    adapter = RequisitionAdapter()
    summary = lambda: adapter.summaries(db[0], Scope(db[1]), [row.id])[row.id]  # noqa: E731
    with tenant_transaction(db[0], Scope(db[1])):
        assert summary().note is None
    amend(db, row, "100", note="Supplier raised the price")
    with tenant_transaction(db[0], Scope(db[1])):
        assert summary().note == "Supplier raised the price"
    assert RequisitionService(db[0], Scope(db[1])).get(row.id).status == "sourcing_in_progress"


def test_submit_revalidates_the_proposals_master_data(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)
    # D-M2-23: a code cannot be changed by amendment, so the code itself expires meanwhile.
    draft = opened(db, row)
    line = service(db).list_lines(row.id).rows[0]
    draft = amendments.put(
        db[0],
        Scope(db[1]),
        row.id,
        draft.revision_no,
        AmendmentPut(
            header=AmendmentHeaderPut(),
            lines=[
                ReplacementLine(line_id=line.id, **(BODY | {"quantity": "1", "unit_price": "60"}))
            ],
        ),
        str(draft.version),
        db[3],
    )
    sql(
        db,
        "UPDATE master_record SET active=false WHERE kind='ledger_account' AND code='L' "
        "RETURNING 1",
    )
    with pytest.raises(ProblemError) as error:
        submit(db, row, draft.revision_no, draft.version)
    assert error.value.checks["problem"] == "submission_invalid"
    assert [(p.category, p.field) for p in error.value.problems] == [
        ("master_data", "master_data/ledger_account_code")
    ]
    assert revision(db, row.id, draft.revision_no)[0] == "draft"
    assert state(db, row.id).amendment.status == "draft"


@pytest.mark.parametrize("stop", ["cancelled", "withdrawn", "expired"])
def test_a_draft_amendment_does_not_outlive_its_requisition(lifecycle_db, stop):
    from flo.modules.requisitions import lifecycle

    db, _ = lifecycle_db
    _, row = approved(db)
    draft = opened(db, row)
    if stop == "expired":
        lifecycle.stop_approved(
            db[0],
            Scope(db[1]),
            row.id,
            "expired",
            "expired",
            lifecycle.SYSTEM_ACTOR_ID,
            system_actor=lifecycle.EXPIRY_ACTOR,
        )
    else:
        transition(
            db[0],
            Scope(db[1]),
            row.id,
            RequisitionTransitionCreate(to=stop, reason="Project was cancelled today"),
            str(state(db, row.id).version),
            db[3],
            lambda permission: True,
        )
    after = state(db, row.id)
    assert after.status == stop and after.amendment is None
    with pytest.raises(ProblemError) as error:
        propose(db, row, draft.revision_no, draft.version, price="90")
    assert error.value.checks["problem"] == "invalid_transition"
    with pytest.raises(ProblemError) as error:
        amendments.current(db[0], Scope(db[1]), row.id)
    assert error.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(ProblemError) as error:
        submit(db, row, draft.revision_no, draft.version)
    assert error.value.checks["problem"] == "invalid_transition"
