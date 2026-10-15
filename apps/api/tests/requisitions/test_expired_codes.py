"""Expired master codes: substitute, block downstream, no amendment change (E09-S18)."""

import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import CodeSubstitutions, DecisionWrite
from flo.modules.approvals.service import decide, get_instance
from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
from flo.modules.org.schemas import MasterCreate, MasterPatch
from flo.modules.org.service import today
from flo.modules.projects.service import ProjectService
from flo.modules.requisitions import amendments, codes
from flo.modules.requisitions.schemas import AmendmentHeaderPut, AmendmentPut
from flo.modules.requisitions.service import assert_current_upstream
from tests.requisitions.test_amendments import opened
from tests.requisitions.test_approval_effects import approve, linked, reviewer
from tests.requisitions.test_approval_effects import state as state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import client, headers, login, org_svc, project, unit
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lifecycle import sql
from tests.requisitions.test_lines import lines_db as lines_db


def expire(db, kind, code):
    org = org_svc(db)
    ref = org.assert_usable(kind, code, today())
    org.update_master(kind, ref.id, MasterPatch(active=False))


def add_code(db, kind, code):
    org_svc(db).create_master(
        kind,
        MasterCreate(
            code=code,
            name=code,
            effective_from=date(2000, 1, 1),
            attributes={"account_type": "expense"} if kind == "ledger_account" else {},
        ),
    )


def pending(db):
    p = project(db, unit(db, uuid4().hex))
    return p, linked(db, p)


def decision(db, row, **kwargs):
    current = state(db, row.id)
    task = get_instance(db[0], Scope(db[1]), current.approval.instance_id).tasks[-1]
    return decide(
        db[0],
        Scope(db[1]),
        task_id=task.id,
        actor_id=reviewer(db),
        body=DecisionWrite(document_version=current.version, **kwargs),
    )


def problem(call):
    with pytest.raises(ProblemError) as error:
        call()
    return error.value


def test_approval_over_an_expired_code_needs_a_substitute(lifecycle_db):
    db, _ = lifecycle_db
    p, row = pending(db)
    expire(db, "ledger_account", "L")
    add_code(db, "ledger_account", "L2")
    assert state(db, row.id).codes_expired is True
    assert ProjectService(db[0], Scope(db[1])).get(p.id).codes_expired is True

    refused = problem(lambda: decision(db, row, outcome="approve"))
    assert refused.code == ErrorCode.CONFLICT and refused.checks["problem"] == "code_expired"
    assert state(db, row.id).status == "approval_pending"

    decision(db, row, outcome="approve", substitutions=CodeSubstitutions(ledger_account_code="l2"))
    after = state(db, row.id)
    assert after.status == "sourcing_in_progress" and after.ledger_account_code == "L2"
    assert after.codes_expired is False
    # The reservation stays on the code it was posted under; the sub-project takes the new one.
    # The submit-time reservation and its release stay on L (the ledger is append-only); every
    # entry written after the substitution (S16's transfer legs and the sub-project's
    # reservation) carries L2.
    sub = sql(db, "SELECT id FROM project WHERE generated_by_requisition_id=%s", (row.id,))[0][0]
    entries = sql(
        db,
        "SELECT entry_type,bucket,project_id,amount,ledger_account_code FROM ledger_entry "
        "WHERE source_id=%s ORDER BY id",
        (row.id,),
    )
    assert entries == [
        ("reservation", "reserved", p.id, Decimal("60.0000"), "L"),
        ("release", "reserved", p.id, Decimal("-60.0000"), "L"),
        ("transfer", "allocated", p.id, Decimal("-60.0000"), "L2"),
        ("transfer", "allocated", sub, Decimal("60.0000"), "L2"),
        ("reservation", "reserved", sub, Decimal("60.0000"), "L2"),
    ]
    assert sql(
        db,
        "SELECT ledger_account_code FROM project WHERE generated_by_requisition_id=%s",
        (row.id,),
    ) == [("L2",)]
    audit = sql(
        db,
        "SELECT before,after FROM audit_log WHERE target_id=%s "
        "AND action='requisition.code_substitute'",
        (row.id,),
    )
    assert len(audit) == 1
    assert audit[0][0]["ledger_account_code"] == "L" and audit[0][1]["ledger_account_code"] == "L2"


def test_reject_needs_no_substitution(lifecycle_db):
    db, _ = lifecycle_db
    _, row = pending(db)
    expire(db, "department", "D")
    decision(db, row, outcome="reject", reject_mode="permanent", comment="No")
    assert state(db, row.id).status == "rejected"


def test_substitution_rules_are_422(lifecycle_db):
    db, _ = lifecycle_db
    _, row = pending(db)
    expire(db, "ledger_account", "L")
    add_code(db, "department", "D2")
    add_code(db, "ledger_account", "L2")
    expire(db, "ledger_account", "L2")
    usable = problem(
        lambda: decision(
            db, row, outcome="approve", substitutions=CodeSubstitutions(department_code="D2")
        )
    )
    assert usable.code == ErrorCode.VALIDATION_FAILED
    assert usable.checks["problem"] == "code_usable"
    unusable = problem(
        lambda: decision(
            db, row, outcome="approve", substitutions=CodeSubstitutions(ledger_account_code="L2")
        )
    )
    assert unusable.code == ErrorCode.VALIDATION_FAILED
    assert unusable.checks["problem"] == "code_unusable"
    assert state(db, row.id).status == "approval_pending"


def approved_expired(db):
    p, row = pending(db)
    from tests.requisitions.test_approval_effects import approve

    approve(db, row)
    expire(db, "department", "D")
    add_code(db, "department", "D2")
    return p, state(db, row.id)


def test_downstream_is_blocked_until_a_project_manager_substitutes(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved_expired(db)
    blocked = problem(lambda: assert_current_upstream(db[0], Scope(db[1]), row.id, row.version))
    assert blocked.checks["problem"] == "code_expired"

    codes.substitute_requisition(
        db[0], Scope(db[1]), db[3], row.id, {"department_code": "D2"}, "Department retired"
    )
    after = state(db, row.id)
    assert after.version == row.version and after.codes_expired is False
    assert_current_upstream(db[0], Scope(db[1]), row.id, after.version)  # approval stays current
    assert sql(
        db,
        "SELECT department_code FROM project WHERE generated_by_requisition_id=%s",
        (row.id,),
    ) == [("D2",)]
    assert sql(
        db,
        "SELECT count(*) FROM audit_log WHERE action IN "
        "('requisition.code_substitute','project.code_substitute') AND target_id IN "
        "(%s,(SELECT id FROM project WHERE generated_by_requisition_id=%s))",
        (row.id, row.id),
    ) == [(2,)]


def test_project_substitution_updates_the_requisition_and_refuses_usable_codes(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved_expired(db)
    sub = sql(db, "SELECT id FROM project WHERE generated_by_requisition_id=%s", (row.id,))[0][0]
    again = problem(
        lambda: codes.substitute_project(
            db[0], Scope(db[1]), db[3], sub, {"ledger_account_code": "L"}, "Nothing wrong"
        )
    )
    assert again.checks["problem"] == "code_usable"
    codes.substitute_project(
        db[0], Scope(db[1]), db[3], sub, {"department_code": "D2"}, "Department retired"
    )
    assert state(db, row.id).department_code == "D2"


def test_amendment_cannot_change_a_code(lifecycle_db):
    db, _ = lifecycle_db
    p, row = pending(db)
    approve(db, row)
    row = state(db, row.id)
    add_code(db, "department", "D2")
    draft = opened(db, row)
    for field in ("department_code", "ledger_account_code"):
        error = problem(
            lambda field=field: amendments.put(
                db[0],
                Scope(db[1]),
                row.id,
                draft.revision_no,
                AmendmentPut(header=AmendmentHeaderPut(**{field: "D2"}), lines=[]),
                str(draft.version),
                db[3],
            )
        )
        assert error.code == ErrorCode.VALIDATION_FAILED
        assert error.checks["problem"] == "codes_change_blocked"


def test_codes_routes_conceal_foreign_tenants_and_need_the_permission(lifecycle_db):
    db, _ = lifecycle_db
    from tests.requisitions.test_header import draft as create_draft

    _, row = approved_expired(db)
    foreign_project = project(db, org=db[2])
    foreign = create_draft(db, p=foreign_project, org=db[2])
    sub = sql(db, "SELECT id FROM project WHERE generated_by_requisition_id=%s", (row.id,))[0][0]
    body = {"department_code": "D2", "reason": "Department retired"}

    async def post(browser, path):
        return await browser.request("POST", path, json=body, headers=headers(browser, uuid4().hex))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for path in (
                f"/api/v1/requisitions/{foreign.id}/codes",
                f"/api/v1/projects/{foreign_project.id}/codes",
            ):
                assert (await post(browser, path)).status_code == 404, path
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                )
                identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
                role = identity.create_role("read-only", "Read only")
                identity.grant_permission(role.id, "requisition.view")
                identity.grant_permission(role.id, "project.read")
                identity.grant_role(db[3], role.id, AuthorizationTarget.organization(db[1]))
            for path in (f"/api/v1/requisitions/{row.id}/codes", f"/api/v1/projects/{sub}/codes"):
                assert (await post(browser, path)).status_code == 403, path
            for path in (f"/api/v1/requisitions/{row.id}", f"/api/v1/projects/{sub}"):
                read = await browser.get(path)
                assert read.json()["codes_expired"] is True
                assert read.json()["can_substitute_codes"] is False, path

    asyncio.run(scenario())


def test_codes_route_substitutes_for_a_granted_user(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved_expired(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            before = await browser.get(f"/api/v1/requisitions/{row.id}")
            assert before.json()["can_substitute_codes"] is True
            response = await browser.request(
                "POST",
                f"/api/v1/requisitions/{row.id}/codes",
                json={"department_code": "D2", "reason": "Department retired"},
                headers=headers(browser, uuid4().hex),
            )
            assert response.status_code == 200, response.text
            assert response.json()["department_code"] == "D2"
            assert response.json()["codes_expired"] is False

    asyncio.run(scenario())


def test_a_blank_draft_is_not_flagged(lifecycle_db):
    from tests.requisitions.test_header import draft as create_draft

    db, _ = lifecycle_db
    row = create_draft(db, bu=unit(db, uuid4().hex))
    assert row.department_code is None
    read = state(db, row.id)
    assert read.codes_expired is False
    assert read.codes.department.status == "missing"


def test_inbox_row_carries_the_expired_code_state(lifecycle_db):
    from flo.modules.approvals.service import inbox
    from flo.modules.identity.service import AuthorizationResolver

    db, _ = lifecycle_db
    _, row = pending(db)
    expire(db, "department", "D")

    def codes_expired():
        page = inbox(
            db[0],
            Scope(db[1]),
            user_id=reviewer(db),
            view="pending",
            resolver=AuthorizationResolver(db[0], Scope(db[1])),
        )
        return [r.codes_expired for r in page.rows if r.document_id == row.id]

    assert codes_expired() == [True]


def test_substitution_needs_at_least_one_code(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved_expired(db)
    sub = sql(db, "SELECT id FROM project WHERE generated_by_requisition_id=%s", (row.id,))[0][0]

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for path in (f"/api/v1/requisitions/{row.id}/codes", f"/api/v1/projects/{sub}/codes"):
                response = await browser.request(
                    "POST",
                    path,
                    json={"reason": "No code given"},
                    headers=headers(browser, uuid4().hex),
                )
                assert response.status_code == 422, (path, response.text)
                assert response.json()["checks"]["problem"] == "code_required"

    asyncio.run(scenario())


def test_two_concurrent_substitutions_of_one_code_let_one_win(lifecycle_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import psycopg

    from flo.kernel.logging import correlation_context
    from tests.requisitions.test_header import connection_url

    db, _ = lifecycle_db
    _, row = approved_expired(db)
    add_code(db, "department", "D3")
    barrier = Barrier(2)

    def substitute(code):
        with (
            psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
            correlation_context("codes-race"),
        ):
            barrier.wait(timeout=10)
            try:
                codes.substitute_requisition(
                    conn, Scope(db[1]), db[3], row.id, {"department_code": code}, "Race"
                )
                return "won"
            except ProblemError as exc:
                assert exc.checks["problem"] == "code_usable", exc
                return "refused"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(substitute, ["D2", "D3"]))
    assert sorted(results) == ["refused", "won"]
    assert sql(
        db,
        "SELECT count(*) FROM audit_log WHERE target_id=%s "
        "AND action='requisition.code_substitute'",
        (row.id,),
    ) == [(1,)]


def test_generated_project_flag_follows_the_pending_amendment(lifecycle_db):
    from tests.requisitions.test_amendments import amend

    db, _ = lifecycle_db
    _, row = pending(db)
    approve(db, row)
    row = state(db, row.id)
    add_code(db, "department", "D2")
    sub = sql(db, "SELECT id FROM project WHERE generated_by_requisition_id=%s", (row.id,))[0][0]

    async def scenario():
        async with client(app) as browser:
            await login(browser)

            async def flags():
                project = await browser.get(f"/api/v1/projects/{sub}")
                requisition = await browser.get(f"/api/v1/requisitions/{row.id}")
                assert project.json()["codes_expired"] is True
                return (
                    project.json()["can_substitute_codes"],
                    requisition.json()["can_substitute_codes"],
                )

            # An amendment is open first (its submit revalidates codes); the code expires after.
            number = amend(db, row, "100")  # material increase: stays pending
            assert state(db, row.id).amendment.status == "pending"
            expire(db, "department", "D")
            assert await flags() == (False, False)
            refused = await browser.request(
                "POST",
                f"/api/v1/projects/{sub}/codes",
                json={"department_code": "D2", "reason": "Department retired"},
                headers=headers(browser, uuid4().hex),
            )
            assert refused.status_code == 409
            assert refused.json()["checks"]["problem"] == "amendment_pending"
            amendments.withdraw_amendment(
                db[0], Scope(db[1]), row.id, number, db[3], lambda permission: True
            )
            assert await flags() == (True, True)

    asyncio.run(scenario())
