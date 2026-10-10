"""PROJ-012/013/018 and WF-002/006, including balance-first races."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from itertools import product
from threading import Barrier
from typing import get_args
from uuid import uuid4

import psycopg
import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.budget.models import LedgerBucket, LedgerType
from flo.modules.budget.schemas import TransferCreate
from flo.modules.budget.service import post_entry, transfer
from flo.modules.projects.schemas import ProjectStatus, TransitionCreate
from flo.modules.projects.service import ProjectService
from tests.authz.conftest import ROOT, load_migration
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity
from tests.budget.test_allocation import allocation_db as allocation_db
from tests.budget.test_post_entry import payload, post
from tests.budget.test_post_entry import posting_db as posting_db
from tests.projects.conftest import body, service, settings, unit
from tests.projects.test_projects import request

VALID = {
    ("draft", "approval_pending"),
    ("approval_pending", "active"),
    ("approval_pending", "draft"),
    ("draft", "abandoned"),
    ("active", "deferred"),
    ("deferred", "active"),
    ("active", "completed"),
    ("active", "abandoned"),
    ("deferred", "abandoned"),
}


def project(db, status="draft"):
    row = service(db).create(body(unit(db)))
    db.connection.execute("UPDATE project SET status=%s WHERE id=%s", (status, row.id))
    return row


def transition(db, row, to, **kwargs):
    return service(db).transition(row.id, TransitionCreate(to=to, **kwargs), "1", lambda p: True)


def send(db, row, to, viewer=None, **changes):
    return request(
        db,
        "POST",
        f"/{row.id}/transitions",
        json={"to": to} | changes,
        headers={"If-Match": "1", "Idempotency-Key": uuid4().hex},
        viewer=viewer,
    )


@pytest.mark.parametrize("start,target", list(product(get_args(ProjectStatus.__value__), repeat=2)))
def test_all_36_state_pairs(project_db, start, target):
    row = project(project_db, start)
    if (start, target) in VALID:
        result = transition(project_db, row, target, reason="Explained transition")
        assert result.status == target and result.version == 2
    else:
        with pytest.raises(ProblemError) as error:
            transition(project_db, row, target, reason="Explained transition")
        assert error.value.code == ErrorCode.CONFLICT
        assert error.value.checks == {"problem": "invalid_transition"}
    count = project_db.connection.execute("SELECT count(*) FROM project_status_event").fetchone()[0]
    assert count == int((start, target) in VALID)


def test_submitter_cannot_approve_and_other_actor_can(project_db):
    db = project_db
    grant_permissions(db, db.actor_id, "project.submit", "project.approve")
    row = project(db)
    assert send(db, row, "approval_pending").status_code == 200
    with pytest.raises(ProblemError) as exc:
        service(db).transition(row.id, TransitionCreate(to="active"), "2", lambda p: True)
    assert exc.value.code == ErrorCode.FORBIDDEN
    other = insert_identity(db, "approver")
    grant_permissions(db, other, "project.read", "project.approve")
    result = request(
        db,
        "POST",
        f"/{row.id}/transitions",
        json={"to": "active"},
        headers={"If-Match": "2", "Idempotency-Key": uuid4().hex},
        viewer=other,
    )
    assert result.status_code == 200, result.text


@pytest.mark.parametrize(
    "start,target,permission",
    [
        ("draft", "approval_pending", "project.submit"),
        ("approval_pending", "active", "project.approve"),
        ("active", "completed", "project.complete"),
        ("active", "deferred", "project.update"),
    ],
)
def test_dynamic_permissions(project_db, start, target, permission):
    db = project_db
    row = project(db, start)
    viewer = insert_identity(db, "reader")
    grant_permissions(db, viewer, "project.read")
    assert send(db, row, target, viewer, reason="A reason").status_code == 403
    grant_permissions(db, viewer, permission)
    assert send(db, row, target, viewer, reason="A reason").status_code == 200


@pytest.mark.parametrize("target", ["completed", "abandoned"])
def test_closing_block_and_override(posting_db, target):
    db = posting_db
    row = project(db, "active")
    post(db, row)
    post(db, row, entry_type=LedgerType.RESERVATION, amount=Decimal("40"))
    grant_permissions(db, db.actor_id, "project.complete")
    blocked = send(db, row, target, reason="Closure reason")
    assert blocked.status_code == 409
    assert blocked.json()["checks"]["problem"] == "closing_blocked"
    assert "40.0000 USD" in blocked.json()["detail"]
    assert (
        send(db, row, target, override=True, reason="Authorized override explanation").status_code
        == 403
    )
    grant_permissions(db, db.actor_id, "project.close.override")
    assert send(db, row, target, override=True, reason="short").status_code == 422
    assert (
        send(db, row, target, override=True, reason="Authorized override explanation").status_code
        == 200
    )
    event = db.connection.execute(
        "SELECT override, blocked_by FROM project_status_event"
    ).fetchone()
    assert event[0] and event[1]["reserved"] == "40.0000"
    audit = db.connection.execute(
        "SELECT after FROM audit_log WHERE action='project.transition'"
    ).fetchone()[0]
    assert audit["override"] and audit["blocked_by"] == event[1]


def test_draft_abandon_uses_closing_rule(posting_db):
    db = posting_db
    row = project(db, "active")
    post(db, row)
    post(db, row, entry_type=LedgerType.RESERVATION, amount=Decimal("40"))
    db.connection.execute("UPDATE project SET status='draft' WHERE id=%s", (row.id,))
    with pytest.raises(ProblemError) as exc:
        transition(db, row, "abandoned")
    assert exc.value.checks == {"problem": "closing_blocked"}
    assert (
        transition(db, row, "abandoned", override=True, reason="Authorized closure of draft").status
        == "abandoned"
    )


@pytest.mark.parametrize(
    "start,target",
    [
        ("active", "deferred"),
        ("active", "abandoned"),
        ("deferred", "abandoned"),
        ("approval_pending", "draft"),
    ],
)
def test_required_reason(project_db, start, target):
    row = project(project_db, start)
    with pytest.raises(ProblemError) as exc:
        transition(project_db, row, target, reason="   ")
    assert exc.value.code == ErrorCode.VALIDATION_FAILED


@pytest.mark.parametrize("field", ["name", "department_code", "ledger_account_code", "currency"])
def test_submit_required_fields(project_db, field):
    row = project(project_db)
    project_db.connection.execute(f"UPDATE project SET {field}='' WHERE id=%s", (row.id,))
    with pytest.raises(ProblemError) as exc:
        transition(project_db, row, "approval_pending")
    assert exc.value.code == ErrorCode.VALIDATION_FAILED


def test_open_document_hook_blocks(project_db, monkeypatch):
    row = project(project_db, "active")
    monkeypatch.setattr("flo.modules.projects.lifecycle.open_documents", lambda id: 2)
    with pytest.raises(ProblemError) as exc:
        transition(project_db, row, "completed")
    assert "2 open documents" in exc.value.detail


def test_available_matches_post(project_db):
    db = project_db
    row = project(db, "active")
    grant_permissions(db, db.actor_id, "project.complete")
    options = request(db, "GET", f"/{row.id}/transitions/available")
    assert options.status_code == 200
    assert len(options.json()) == 6
    for option in options.json():
        with db.connection.transaction(force_rollback=True):
            result = send(
                db, row, option["to"], **({"reason": "x" * 20} if option["reason_required"] else {})
            )
            assert option["allowed"] == (result.status_code == 200)
            if not option["allowed"]:
                assert result.json()["detail"] in option["blocked_reasons"]


def test_transition_foreign_project(project_db):
    db = project_db
    foreign = service(db, db.org_b).create(body(unit(db, "F", db.org_b)))
    assert send(db, foreign, "abandoned").status_code == 404


def test_available_foreign_project(project_db):
    db = project_db
    foreign = service(db, db.org_b).create(body(unit(db, "F", db.org_b)))
    assert request(db, "GET", f"/{foreign.id}/transitions/available").status_code == 404


def test_no_scope_conceals_transition(project_db):
    db = project_db
    row = project(db)
    viewer = insert_identity(db, "ungranted")
    assert send(db, row, "abandoned", viewer).status_code == 404
    assert request(db, "GET", f"/{row.id}/transitions/available", viewer=viewer).status_code == 404


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE project_status_event SET reason='changed'",
        "DELETE FROM project_status_event",
        "TRUNCATE project_status_event",
    ],
)
def test_events_append_only(project_db, statement):
    row = project(project_db)
    transition(project_db, row, "abandoned")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        project_db.connection.execute(statement)


def test_migration_preserves_existing_rows(project_db):
    db = project_db
    row = project(db)
    migration = load_migration(
        ROOT / "migrations/20261008_0025_project_lifecycle.py", "lifecycle-roundtrip"
    )
    migration.downgrade(db.connection)
    tables = ["project", "project_balance", "audit_log", "permission", "role_permission"]
    before = {t: db.connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    migration.upgrade(db.connection)
    transition(db, row, "abandoned")
    # The migration only removes its history and catalog entries, preserving project data.
    after = {
        t: db.connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
        for t in tables
        if t not in {"permission", "role_permission"}
    }
    migration.downgrade(db.connection)
    for t in after:
        assert db.connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() == after[t]
    for t in ["permission", "role_permission"]:
        assert db.connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() == before[t]
    migration.upgrade(db.connection)


@pytest.mark.parametrize("status", get_args(ProjectStatus.__value__))
@pytest.mark.parametrize(
    "kind,amount",
    [
        (LedgerType.ALLOCATION, "1"),
        (LedgerType.ADJUSTMENT, "-1"),
        (LedgerType.RESERVATION, "1"),
        (LedgerType.COMMITMENT, "1"),
        (LedgerType.ACTUAL, "1"),
    ],
)
def test_posting_status_matrix(posting_db, status, kind, amount):
    db = posting_db
    row = project(db, "active")
    post(db, row)
    db.connection.execute("UPDATE project SET status=%s WHERE id=%s", (status, row.id))
    allowed = (
        (
            kind in {LedgerType.ALLOCATION, LedgerType.ADJUSTMENT}
            and (Decimal(amount) < 0 or status in {"draft", "active"})
        )
        or (kind in {LedgerType.RESERVATION, LedgerType.COMMITMENT} and status == "active")
        or (kind == LedgerType.ACTUAL and status in {"active", "deferred"})
    )
    if allowed:
        post(db, row, entry_type=kind, amount=Decimal(amount), reason="Adjustment")
    else:
        with pytest.raises(ProblemError) as exc:
            post(db, row, entry_type=kind, amount=Decimal(amount), reason="Adjustment")
        assert exc.value.checks == {"problem": "posting_not_allowed"}


def test_direct_transfer_signed_guard(posting_db):
    db = posting_db
    row = project(db, "active")
    post(db, row)
    transition(db, row, "completed")
    with pytest.raises(ProblemError) as exc:
        post(
            db, row, entry_type=LedgerType.TRANSFER, amount=Decimal("1"), transfer_group_id=uuid4()
        )
    assert exc.value.checks == {"problem": "posting_not_allowed"}
    post(db, row, entry_type=LedgerType.TRANSFER, amount=Decimal("-1"), transfer_group_id=uuid4())


def racing(db, first, second):
    barrier = Barrier(2)

    def run(call):
        with psycopg.connect(settings(db).database_url.get_secret_value(), autocommit=True) as conn:
            with correlation_context(uuid4().hex):
                barrier.wait(timeout=5)
                try:
                    call(conn)
                    return "ok"
                except ProblemError as exc:
                    return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        return list(pool.map(run, [first, second]))


def test_concurrent_same_version(project_db):
    db = project_db
    row = project(db, "active")

    def close(conn):
        ProjectService(conn, Scope(db.org_a), db.actor_id).transition(
            row.id, TransitionCreate(to="completed"), "1", lambda p: True
        )

    assert sorted(racing(db, close, close)) == ["ok", "stale_version"]


def test_reservation_races_completion(posting_db):
    db = posting_db
    row = project(db, "active")
    post(db, row)

    def close(conn):
        ProjectService(conn, Scope(db.org_a), db.actor_id).transition(
            row.id, TransitionCreate(to="completed"), "1", lambda p: True
        )

    def reserve(conn):
        post_entry(
            conn,
            Scope(db.org_a),
            **payload(db, row, entry_type=LedgerType.RESERVATION, amount=Decimal("40")),
        )

    outcomes = racing(db, close, reserve)
    assert sorted(outcomes) in [["closing_blocked", "ok"], ["ok", "posting_not_allowed"]]
    status = service(db).get(row.id).status
    reserved = db.connection.execute(
        "SELECT reserved FROM project_balance WHERE project_id=%s", (row.id,)
    ).fetchone()[0]
    assert not (status == "completed" and reserved > 0)


@pytest.mark.parametrize("iteration", range(20))
def test_transfer_races_completion(allocation_db, iteration):
    db = allocation_db
    bu = unit(db)
    a, b = service(db).create(body(bu)), service(db).create(body(bu))
    db.connection.execute("UPDATE project SET status='active'")
    post(db, a)

    def close(conn):
        ProjectService(conn, Scope(db.org_a), db.actor_id).transition(
            b.id, TransitionCreate(to="completed"), "1", lambda p: True
        )

    def move(conn):
        transfer(
            conn,
            Scope(db.org_a),
            TransferCreate(
                from_project_id=a.id,
                to_project_id=b.id,
                amount="25",
                currency="USD",
                effective_date="2026-08-26",
                reason="Residual transfer",
            ),
            db.actor_id,
            uuid4().hex,
        )

    outcomes = racing(db, close, move)
    assert outcomes in [["ok", "ok"], ["ok", "transfer_not_eligible"]]
    assert service(db).get(b.id).status == "completed"
    allocated = db.connection.execute(
        "SELECT allocated FROM project_balance WHERE project_id=%s", (b.id,)
    ).fetchone()[0]
    assert allocated == (Decimal("25") if outcomes[1] == "ok" else Decimal(0))


def test_balance_lock_precedes_status_read(project_db, monkeypatch):
    """Planting a status read before the funding lock must fail this real-DB check."""
    from flo.modules.projects.models import ProjectRepository

    db = project_db
    row = project(db, "active")
    original = ProjectRepository.get

    def checked(repo, id, *, lock=False):
        if lock:
            held = repo.connection.execute(
                "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND relation='project_balance'::regclass AND mode='RowShareLock'"
            ).fetchone()[0]
            assert held > 0, "status read occurred before balance FOR UPDATE"
        return original(repo, id, lock=lock)

    monkeypatch.setattr(ProjectRepository, "get", checked)
    assert transition(db, row, "completed").status == "completed"


@pytest.mark.parametrize("status", get_args(ProjectStatus.__value__))
def test_release_and_reversal_remain_allowed(posting_db, status):
    from flo.modules.budget.models import LedgerBucket

    db = posting_db
    row = project(db, "active")
    post(db, row)
    held = post(db, row, entry_type=LedgerType.RESERVATION, amount=Decimal("40"))
    db.connection.execute("UPDATE project SET status=%s WHERE id=%s", (status, row.id))
    release = post(
        db,
        row,
        entry_type=LedgerType.RELEASE,
        bucket=LedgerBucket.RESERVED,
        amount=Decimal("-10"),
        releases_entry_id=held.id,
    )
    reversal = post(
        db,
        row,
        entry_type=LedgerType.REVERSAL,
        amount=Decimal("1"),
        reverses_entry_id=release.id,
        reason="Undo release",
    )
    assert reversal.amount == Decimal("10")
    assert db.connection.execute(
        "SELECT reserved FROM project_balance WHERE project_id=%s", (row.id,)
    ).fetchone() == (Decimal("40"),)


def test_transition_idempotency_key(allocation_db):
    import asyncio

    import httpx

    from tests.budget.test_allocation import app

    db = allocation_db
    row = project(db)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app(db)), base_url="https://testserver"
        ) as client:
            path = f"/api/v1/projects/{row.id}/transitions"
            assert (
                await client.post(path, json={"to": "abandoned"}, headers={"If-Match": "1"})
            ).status_code == 400
            headers = {"If-Match": "1", "Idempotency-Key": "close-once"}
            first = await client.post(path, json={"to": "abandoned"}, headers=headers)
            second = await client.post(path, json={"to": "abandoned"}, headers=headers)
            assert first.status_code == second.status_code == 200
            assert first.json() == second.json()
            changed = await client.post(path, json={"to": "approval_pending"}, headers=headers)
            assert changed.status_code == 422

    asyncio.run(run())
    assert db.connection.execute("SELECT count(*) FROM project_status_event").fetchone() == (1,)


@pytest.mark.parametrize("version", [None, "invalid", "2"])
def test_transition_if_match(project_db, version):
    row = project(project_db)
    with pytest.raises(ProblemError) as exc:
        service(project_db).transition(
            row.id, TransitionCreate(to="abandoned"), version, lambda permission: True
        )
    assert exc.value.code == (ErrorCode.CONFLICT if version == "2" else ErrorCode.BAD_REQUEST)


def test_events_rls_rejects_foreign_write(project_db):
    from psycopg import sql

    from flo.kernel.tenancy.rls import tenant_transaction

    db = project_db
    own = project(db)
    foreign = service(db, db.org_b).create(body(unit(db, "F", db.org_b)))
    transition(db, own, "abandoned")
    service(db, db.org_b).transition(
        foreign.id, TransitionCreate(to="abandoned"), "1", lambda p: True
    )
    role = "lifecycle_rls_" + uuid4().hex
    db.connection.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        db.connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
        )
        db.connection.execute(
            sql.SQL("GRANT SELECT,INSERT ON project_status_event TO {}").format(
                sql.Identifier(role)
            )
        )
        db.connection.execute(
            sql.SQL("GRANT USAGE ON SEQUENCE project_status_event_id_seq TO {}").format(
                sql.Identifier(role)
            )
        )
        db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(db.connection, Scope(db.org_a)):
            assert db.connection.execute(
                "SELECT project_id FROM project_status_event"
            ).fetchall() == [(own.id,)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_transaction(db.connection, Scope(db.org_a)):
                db.connection.execute(
                    "INSERT INTO project_status_event(org_id,project_id,from_status,"
                    "to_status,actor_id) "
                    "VALUES (%s,%s,'draft','abandoned',%s)",
                    (db.org_b, foreign.id, db.actor_id),
                )
    finally:
        db.connection.execute("RESET ROLE")
        db.connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        db.connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_committed_balance_blocks_closure(posting_db):
    db = posting_db
    row = project(db, "active")
    post(db, row)
    post(db, row, entry_type=LedgerType.COMMITMENT, amount=Decimal("40"))
    with pytest.raises(ProblemError) as exc:
        transition(db, row, "completed")
    assert exc.value.checks == {"problem": "closing_blocked"}
    assert "committed 40.0000 USD" in exc.value.detail


def test_missing_balance_initialized_before_transition(project_db):
    db = project_db
    row = project(db)
    db.connection.execute("DELETE FROM project_balance WHERE project_id=%s", (row.id,))
    assert transition(db, row, "abandoned").status == "abandoned"
    assert db.connection.execute(
        "SELECT reserved,committed FROM project_balance WHERE project_id=%s", (row.id,)
    ).fetchone() == (Decimal(0), Decimal(0))


def test_available_does_not_wait_for_balance_lock(project_db):
    db = project_db
    row = project(db, "active")
    db.connection.execute("SET lock_timeout = '250ms'")
    try:
        with psycopg.connect(settings(db).database_url.get_secret_value()) as blocker:
            blocker.execute(
                "SELECT project_id FROM project_balance WHERE project_id=%s FOR UPDATE", (row.id,)
            )
            response = request(db, "GET", f"/{row.id}/transitions/available")
            assert response.status_code == 200, response.text
    finally:
        db.connection.execute("RESET lock_timeout")


def test_available_missing_balance_is_read_only(project_db):
    db = project_db
    row = project(db, "active")
    grant_permissions(db, db.actor_id, "project.complete")
    db.connection.execute("DELETE FROM project_balance WHERE project_id=%s", (row.id,))
    with db.connection.transaction():
        db.connection.execute("SET TRANSACTION READ ONLY")
        response = request(db, "GET", f"/{row.id}/transitions/available")
        assert response.status_code == 200, response.text
        completed = next(option for option in response.json() if option["to"] == "completed")
        assert completed["allowed"] and completed["blocked_reasons"] == []
    assert db.connection.execute(
        "SELECT count(*) FROM project_balance WHERE project_id=%s", (row.id,)
    ).fetchone() == (0,)


@pytest.mark.parametrize("status", [*get_args(ProjectStatus.__value__), "unknown"])
@pytest.mark.parametrize("bucket", list(LedgerBucket))
@pytest.mark.parametrize("sign", [-1, 1])
def test_direct_posting_allow_list(posting_db, monkeypatch, status, bucket, sign):
    db = posting_db
    row = project(db, "active")
    post(db, row)
    # The seventh status proves future/unknown states cannot pass a deny-list.
    if status == "unknown":
        monkeypatch.setattr("flo.modules.projects.service.get_status", lambda *args: status)
    else:
        db.connection.execute("UPDATE project SET status=%s WHERE id=%s", (status, row.id))
    kind = {
        LedgerBucket.ALLOCATED: LedgerType.ADJUSTMENT,
        LedgerBucket.RESERVED: LedgerType.RESERVATION,
        LedgerBucket.COMMITTED: LedgerType.COMMITMENT,
        LedgerBucket.ACTUAL: LedgerType.ACTUAL,
    }[bucket]
    allowed = (
        (bucket == LedgerBucket.ALLOCATED and (sign < 0 or status in {"draft", "active"}))
        or (bucket in {LedgerBucket.RESERVED, LedgerBucket.COMMITTED} and status == "active")
        or (bucket == LedgerBucket.ACTUAL and status in {"active", "deferred"})
    )
    # Non-allocated negative entries are rejected by the existing ledger sign contract.
    invalid_sign = sign < 0 and bucket != LedgerBucket.ALLOCATED
    args = payload(db, row, entry_type=kind, amount=Decimal(sign), reason="Status matrix")
    if allowed and not invalid_sign:
        assert post_entry(db.connection, Scope(db.org_a), **args).amount == Decimal(sign)
    else:
        with pytest.raises(ProblemError) as exc:
            post_entry(db.connection, Scope(db.org_a), **args)
        assert exc.value.checks == {
            "problem": "invalid_ledger_entry" if invalid_sign else "posting_not_allowed"
        }


def test_available_reason_input_does_not_bypass_post_guard(project_db):
    db = project_db
    row = project(db, "active")
    options = request(db, "GET", f"/{row.id}/transitions/available").json()
    deferred = next(option for option in options if option["to"] == "deferred")
    assert deferred["reachable"] and deferred["reason_required"] and deferred["allowed"]
    assert not deferred["override_available"]
    assert db.connection.execute("SELECT count(*) FROM project_status_event").fetchone() == (0,)
    assert send(db, row, "deferred").status_code == 422
    assert send(db, row, "deferred", reason="Explained deferral").status_code == 200


def test_available_override_only_when_closing_is_sole_blocker(posting_db):
    db = posting_db
    row = project(db, "active")
    post(db, row)
    post(db, row, entry_type=LedgerType.RESERVATION, amount=Decimal("40"))
    grant_permissions(db, db.actor_id, "project.complete")
    options = request(db, "GET", f"/{row.id}/transitions/available").json()
    completed = next(option for option in options if option["to"] == "completed")
    assert completed["reachable"] and completed["override_available"]
    assert completed["reason_required"] and not completed["allowed"]
    viewer = insert_identity(db, "read-only")
    grant_permissions(db, viewer, "project.read")
    options = request(db, "GET", f"/{row.id}/transitions/available", viewer=viewer).json()
    assert not next(o for o in options if o["to"] == "completed")["override_available"]
    assert not next(o for o in options if o["to"] == "draft")["reachable"]
