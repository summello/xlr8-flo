"""E22-S01: a project asks for its budget, names sponsors, and is funded on approval."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import decide, start
from flo.modules.budget.schemas import AllocationCreate
from flo.modules.budget.service import allocate
from flo.modules.identity.service import AuthorizationResolver
from flo.modules.projects.schemas import ProjectCreate, ProjectPatch, TransitionCreate
from flo.modules.projects.service import ProjectService
from tests.approvals.test_project_wiring import publish
from tests.approvals.test_project_wiring import wiring as wiring
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db
from tests.org.test_bootstrap import connection_url


def create(wiring, name, budget=None, parent_id=None, sponsors=True, conn=None):
    db, row, _ = wiring
    return ProjectService(conn or db[0], Scope(db[1]), db[3]).create(
        ProjectCreate(
            bu_id=row.bu_id,
            parent_id=parent_id,
            name=name,
            department_code="D",
            ledger_account_code="L",
            currency="USD",
            budget={"amount": budget, "currency": "USD"} if budget else None,
            sponsor_ids=[db[3]] if sponsors else [],
        )
    )


def submit(wiring, project, conn=None):
    db, _, _ = wiring
    conn = conn or db[0]
    scope = Scope(db[1])
    with tenant_transaction(conn, scope):
        ProjectService(conn, scope, db[3]).transition(
            project.id, TransitionCreate(to="approval_pending"), "1", lambda p: True
        )
        return start(
            conn,
            scope,
            document_type="project",
            document_id=project.id,
            actor_id=db[3],
            resolver=AuthorizationResolver(conn, scope),
        )


def decision(wiring, instance, outcome="approve", conn=None, version=2):
    db, _, approver = wiring
    body = DecisionWrite(
        outcome=outcome,
        document_version=version,
        **({"reject_mode": "return_to_draft", "comment": "Revise"} if outcome == "reject" else {}),
    )
    return decide(
        conn or db[0], Scope(db[1]), task_id=instance.tasks[0].id, actor_id=approver, body=body
    )


def fund(wiring, project_id, amount):
    db, _, _ = wiring
    allocate(
        db[0],
        Scope(db[1]),
        project_id,
        AllocationCreate.model_validate(
            {
                "amount": amount,
                "currency": "USD",
                "effective_date": date.today(),
                "reason": "Seed the main project",
            }
        ),
        db[3],
        uuid4().hex,
    )


def entries(wiring, project_id):
    return (
        wiring[0][0]
        .execute(
            "SELECT entry_type, amount FROM ledger_entry WHERE project_id=%s ORDER BY id",
            (project_id,),
        )
        .fetchall()
    )


def balance(wiring, project_id):
    return (
        wiring[0][0]
        .execute(
            "SELECT allocated, available FROM project_balance WHERE project_id=%s", (project_id,)
        )
        .fetchone()
    )


def test_standalone_approval_allocates_once_and_rejection_posts_nothing(wiring):
    publish(wiring)
    rejected = create(wiring, "Rejected", "500")
    decision(wiring, submit(wiring, rejected), "reject")
    assert entries(wiring, rejected.id) == []
    approved = create(wiring, "Approved", "1250.50")
    decision(wiring, submit(wiring, approved))
    assert entries(wiring, approved.id) == [("allocation", Decimal("1250.5000"))]
    assert balance(wiring, approved.id) == (Decimal("1250.5000"), Decimal("1250.5000"))
    read = ProjectService(wiring[0][0], Scope(wiring[0][1])).get(approved.id)
    assert read.status == "active" and read.requested_budget == "1250.5000"
    assert read.sponsor_id == wiring[0][3] and read.sponsor_ids == [wiring[0][3]]


def test_child_approval_transfers_from_main_with_both_legs(wiring):
    publish(wiring)
    main = create(wiring, "Main", "1000")
    decision(wiring, submit(wiring, main))
    child = create(wiring, "Child", "400", parent_id=main.id)
    decision(wiring, submit(wiring, child))
    assert entries(wiring, child.id) == [("transfer", Decimal("400.0000"))]
    assert entries(wiring, main.id)[-1] == ("transfer", Decimal("-400.0000"))
    assert balance(wiring, main.id)[1] == Decimal("600.0000")
    assert balance(wiring, child.id) == (Decimal("400.0000"), Decimal("400.0000"))
    total = (
        wiring[0][0]
        .execute("SELECT sum(amount) FROM ledger_entry WHERE entry_type='transfer'")
        .fetchone()[0]
    )
    assert total == 0


def test_child_creation_refused_when_main_lacks_funds_and_nothing_is_written(wiring):
    publish(wiring)
    main = create(wiring, "Main", "100")
    decision(wiring, submit(wiring, main))
    with pytest.raises(ProblemError) as error:
        create(wiring, "Too big", "100.01", parent_id=main.id)
    assert error.value.code == ErrorCode.INSUFFICIENT_BUDGET
    assert wiring[0][0].execute("SELECT count(*) FROM project WHERE name='Too big'").fetchone() == (
        0,
    )
    assert balance(wiring, main.id)[1] == Decimal("100.0000")


def test_two_children_against_one_main_exactly_one_is_funded(wiring):
    publish(wiring)
    db, _, _ = wiring
    main = create(wiring, "Main", "1000")
    decision(wiring, submit(wiring, main))
    # Each passes the unlocked check alone; only the lock at approval decides.
    children = [create(wiring, f"Child {n}", "700", parent_id=main.id) for n in (1, 2)]
    instances = [submit(wiring, child) for child in children]
    barrier = Barrier(2)

    def approve(instance):
        with psycopg.connect(connection_url(db[0]), autocommit=True) as conn:
            with correlation_context("project-funding-race"):
                conn.execute("SET lock_timeout='10s'")
                barrier.wait(10)
                try:
                    decision(wiring, instance, conn=conn)
                    return "funded"
                except ProblemError as exc:
                    return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        results = sorted(pool.map(approve, instances))
    assert results == ["funded", "insufficient_budget"]
    assert balance(wiring, main.id)[1] == Decimal("300.0000")
    assert sorted(balance(wiring, c.id)[0] for c in children) == [0, Decimal("700.0000")]


@pytest.mark.parametrize(
    "budget,sponsors,missing",
    [(None, True, "budget"), ("100", False, "sponsor_ids"), (None, False, "budget")],
)
def test_submit_requires_budget_and_sponsor_and_writes_nothing(wiring, budget, sponsors, missing):
    publish(wiring)
    project = create(wiring, "Incomplete", budget, sponsors=sponsors)
    with pytest.raises(ProblemError) as error:
        submit(wiring, project)
    assert error.value.checks == {"problem": "submission_invalid"}
    assert missing in {e.field for e in error.value.errors}
    assert ProjectService(wiring[0][0], Scope(wiring[0][1])).get(project.id).status == "draft"


def test_budget_currency_must_match_the_project(wiring):
    db, row, _ = wiring
    with pytest.raises(ProblemError) as error:
        ProjectService(db[0], Scope(db[1]), db[3]).create(
            ProjectCreate(
                bu_id=row.bu_id,
                name="Mixed",
                department_code="D",
                ledger_account_code="L",
                currency="USD",
                budget={"amount": "10", "currency": "EUR"},
            )
        )
    assert error.value.checks == {"problem": "currency_mismatch"}


def test_sponsor_list_is_bounded_and_distinct():
    user = uuid4()
    base = {
        "bu_id": uuid4(),
        "name": "n",
        "department_code": "D",
        "ledger_account_code": "L",
        "currency": "USD",
    }
    with pytest.raises(ValueError, match="once"):
        ProjectCreate(**base, sponsor_ids=[user, user])
    with pytest.raises(ValueError):
        ProjectCreate(**base, sponsor_ids=[uuid4() for _ in range(11)])
    with pytest.raises(ValueError):
        ProjectCreate(**base, budget={"amount": "0", "currency": "USD"})


def test_create_route_carries_budget_and_sponsors_and_keeps_the_gates(project_db):
    from tests.authz.test_effective_access import grant_permissions
    from tests.authz.test_resolver import insert_identity
    from tests.projects.conftest import body, unit
    from tests.projects.test_projects import request

    db = project_db
    bu = unit(db)
    payload = body(bu, budget={"amount": "75.5", "currency": "USD"}, sponsor_ids=[db.actor_id])
    created = request(db, "POST", json=payload.model_dump(mode="json"))
    assert created.status_code == 201, created.text
    assert created.json()["requested_budget"] == "75.5000"
    assert created.json()["sponsor_ids"] == [str(db.actor_id)]
    # A sponsor outside the organization is a field error; nothing is created.
    stranger = payload.model_copy(update={"sponsor_ids": [uuid4()]})
    refused = request(db, "POST", json=stranger.model_dump(mode="json"))
    assert refused.status_code == 422 and refused.json()["errors"][0]["field"] == "sponsor_ids"
    # project.create is still the gate for a draft: a reader may not create one.
    reader = insert_identity(db, "reader")
    grant_permissions(db, reader, "project.read")
    forbidden = request(db, "POST", json=payload.model_dump(mode="json"), viewer=reader)
    assert forbidden.status_code == 403


@pytest.mark.parametrize("currency,amount", [("USD", "10.123"), ("JPY", "10.5")])
def test_budget_precision_follows_the_currency_exponent(wiring, currency, amount):
    db, row, _ = wiring
    with pytest.raises(ProblemError) as error:
        ProjectService(db[0], Scope(db[1]), db[3]).create(
            ProjectCreate(
                bu_id=row.bu_id,
                name="Precise",
                department_code="D",
                ledger_account_code="L",
                currency=currency,
                budget={"amount": amount, "currency": currency},
            )
        )
    assert error.value.code == ErrorCode.VALIDATION_FAILED
    assert error.value.checks == {"problem": "amount_precision"}
    assert [e.field for e in error.value.errors] == ["budget"]
    assert db[0].execute("SELECT count(*) FROM project WHERE name='Precise'").fetchone() == (0,)
    ok = ProjectService(db[0], Scope(db[1]), db[3]).create(
        ProjectCreate(
            bu_id=row.bu_id,
            name="Exact",
            department_code="D",
            ledger_account_code="L",
            currency="JPY",
            budget={"amount": "10", "currency": "JPY"},
        )
    )
    assert ok.requested_budget == "10.0000"


def test_reads_and_lists_return_every_sponsor(wiring):
    db, _, approver = wiring
    scope = Scope(db[1])
    two = ProjectService(db[0], scope, db[3]).create(
        ProjectCreate(
            bu_id=wiring[1].bu_id,
            name="Two sponsors",
            department_code="D",
            ledger_account_code="L",
            currency="USD",
            sponsor_ids=[db[3], approver],
        )
    )
    expected = sorted([db[3], approver])
    service = ProjectService(db[0], scope)
    assert sorted(service.get(two.id).sponsor_ids) == expected
    page = service.list(q="Two sponsors")
    assert [sorted(r.sponsor_ids) for r in page.rows] == [expected]
    # A project with no sponsor list keeps the legacy single-column fallback.
    legacy = create(wiring, "Legacy sponsor", sponsors=False)
    db[0].execute("UPDATE project SET sponsor_id=%s WHERE id=%s", (approver, legacy.id))
    assert service.get(legacy.id).sponsor_ids == [approver]
    assert [r.sponsor_ids for r in service.list(q="Legacy sponsor").rows] == [[approver]]


def test_a_creator_without_ledger_read_gets_no_balance_figures(project_db):
    from tests.authz.test_effective_access import grant_permissions
    from tests.authz.test_resolver import insert_identity
    from tests.projects.conftest import body, service, unit
    from tests.projects.test_projects import request

    db = project_db
    bu = unit(db)
    main = service(db).create(body(bu))  # nothing allocated: available 0
    creator = insert_identity(db, "creator")
    grant_permissions(db, creator, "project.create", "project.read")
    child = body(
        bu, parent_id=main.id, budget={"amount": "100", "currency": "USD"}, sponsor_ids=[creator]
    )
    refused = request(db, "POST", json=child.model_dump(mode="json"), viewer=creator)
    assert refused.status_code == 409, refused.text
    assert refused.json()["checks"] == {"problem": "insufficient_budget"}
    assert "0.0000" not in refused.text and "100" not in refused.text


def test_submit_refusal_for_a_short_main_project_carries_no_figures(wiring):
    publish(wiring)
    main = create(wiring, "Main", "100")
    decision(wiring, submit(wiring, main))
    child = create(wiring, "Child", "50", parent_id=main.id)
    wiring[0][0].execute("UPDATE project SET requested_budget=500 WHERE id=%s", (child.id,))
    with pytest.raises(ProblemError) as error:
        submit(wiring, child)
    assert error.value.code == ErrorCode.INSUFFICIENT_BUDGET
    assert error.value.checks == {"problem": "insufficient_budget"}


def test_a_draft_without_budget_gets_one_by_patch_and_then_submits(wiring):
    publish(wiring)
    db, _, approver = wiring
    scope = Scope(db[1])
    draft = create(wiring, "Blank", sponsors=False)
    with pytest.raises(ProblemError):
        submit(wiring, draft)
    service = ProjectService(db[0], scope, db[3])
    patched = service.update(
        draft.id,
        ProjectPatch(budget={"amount": "250.25", "currency": "USD"}, sponsor_ids=[db[3], approver]),
        "1",
    )
    assert patched.requested_budget == "250.2500" and patched.version == 2
    assert sorted(patched.sponsor_ids) == sorted([db[3], approver])
    # Replacing the list replaces it; clearing the budget clears the request.
    again = service.update(draft.id, ProjectPatch(sponsor_ids=[approver]), "2")
    assert again.sponsor_ids == [approver]
    cleared = service.update(draft.id, ProjectPatch(budget=None), "3")
    assert cleared.requested_budget is None
    service.update(draft.id, ProjectPatch(budget={"amount": "10", "currency": "USD"}), "4")
    decision(wiring, submit_at(wiring, draft, "5"), version=6)
    assert entries(wiring, draft.id) == [("allocation", Decimal("10.0000"))]


def submit_at(wiring, project, version):
    db, _, _ = wiring
    scope = Scope(db[1])
    with tenant_transaction(db[0], scope):
        ProjectService(db[0], scope, db[3]).transition(
            project.id, TransitionCreate(to="approval_pending"), version, lambda p: True
        )
        return start(
            db[0],
            scope,
            document_type="project",
            document_id=project.id,
            actor_id=db[3],
            resolver=AuthorizationResolver(db[0], scope),
        )


def test_patch_budget_applies_the_create_checks_and_only_while_draft(wiring):
    publish(wiring)
    db, _, _ = wiring
    service = ProjectService(db[0], Scope(db[1]), db[3])
    main = create(wiring, "Main", "100")
    decision(wiring, submit(wiring, main))
    child = create(wiring, "Child", sponsors=False, parent_id=main.id)
    for patch, problem in [
        ({"amount": "1.234", "currency": "USD"}, "amount_precision"),
        ({"amount": "1", "currency": "EUR"}, "currency_mismatch"),
        ({"amount": "100.01", "currency": "USD"}, "insufficient_budget"),
    ]:
        with pytest.raises(ProblemError) as error:
            service.update(child.id, ProjectPatch(budget=patch), "1")
        assert error.value.checks == {"problem": problem}
    with pytest.raises(ProblemError) as error:
        service.update(child.id, ProjectPatch(sponsor_ids=[uuid4()]), "1")
    assert error.value.errors[0].field == "sponsor_ids"
    assert service.get(child.id).version == 1
    # Once submitted, the request is frozen.
    ready = create(wiring, "Ready", "5")
    submit(wiring, ready)
    with pytest.raises(ProblemError) as error:
        service.update(ready.id, ProjectPatch(budget={"amount": "6", "currency": "USD"}), "2")
    assert error.value.checks == {"problem": "invalid_transition"}
    assert service.get(ready.id).requested_budget == "5.0000"


def test_patch_route_sets_budget_and_sponsors_with_if_match(project_db):
    from tests.projects.conftest import body, service, unit
    from tests.projects.test_projects import request

    db = project_db
    draft = service(db).create(body(unit(db)))
    payload = {"budget": {"amount": "75", "currency": "USD"}, "sponsor_ids": [str(db.actor_id)]}
    patched = request(db, "PATCH", f"/{draft.id}", json=payload, headers={"If-Match": "1"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["requested_budget"] == "75.0000"
    stale = request(db, "PATCH", f"/{draft.id}", json=payload, headers={"If-Match": "1"})
    assert stale.status_code == 409


def test_approval_refusal_for_a_short_main_project_carries_no_figures(wiring):
    publish(wiring)
    main = create(wiring, "Main", "1000")
    decision(wiring, submit(wiring, main))
    kids = [create(wiring, f"Kid {n}", "700", parent_id=main.id) for n in (1, 2)]
    instances = [submit(wiring, kid) for kid in kids]
    decision(wiring, instances[0])
    with pytest.raises(ProblemError) as error:
        decision(wiring, instances[1])
    assert error.value.code == ErrorCode.INSUFFICIENT_BUDGET
    assert error.value.checks == {"problem": "insufficient_budget"}


def foreign_user(db):
    """A real member of tenant B."""
    from tests.authz.test_resolver import insert_identity, service_for

    user = insert_identity(db, "tenant-b")
    with service_for(db, Scope(db.org_b), "tenant-b-grant") as identity:
        role = identity.create_role("b-reader", "B reader")
        identity.grant_permission(role.id, "project.read")
        from flo.modules.identity.models import AuthorizationTarget

        identity.grant_role(user, role.id, AuthorizationTarget.organization(db.org_b))
    return user


def test_ten_010_foreign_sponsor_and_foreign_main_project_never_reach_funds(project_db):
    from tests.projects.conftest import body, service, unit
    from tests.projects.test_projects import foreign_project, request

    db = project_db
    bu = unit(db)
    stranger = foreign_user(db)
    other = foreign_project(db)
    budget = {"amount": "100", "currency": "USD"}
    # Tenant B's user cannot be named a sponsor: a field error, on create and on PATCH.
    create_body = body(bu, budget=budget, sponsor_ids=[stranger]).model_dump(mode="json")
    refused = request(db, "POST", json=create_body)
    assert refused.status_code == 422
    assert refused.json()["errors"][0]["field"] == "sponsor_ids"
    draft = service(db).create(body(bu))
    patched = request(
        db,
        "PATCH",
        f"/{draft.id}",
        json={"sponsor_ids": [str(stranger)]},
        headers={"If-Match": "1"},
    )
    assert patched.status_code == 422 and patched.json()["errors"][0]["field"] == "sponsor_ids"
    # Tenant B's project as the main project: 404, never a funds answer.
    child = body(bu, parent_id=other.id, budget=budget).model_dump(mode="json")
    missing = request(db, "POST", json=child)
    assert missing.status_code == 404
    assert "insufficient" not in missing.text.lower() and "checks" not in missing.json()
    # Tenant B's project id with a budget on PATCH: 404 as well.
    foreign_patch = request(
        db, "PATCH", f"/{other.id}", json={"budget": budget}, headers={"If-Match": "1"}
    )
    assert foreign_patch.status_code == 404


def test_lookup_gated_scoped_searchable(project_db):
    from tests.authz.test_effective_access import grant_permissions
    from tests.authz.test_resolver import insert_identity
    from tests.projects.conftest import body, service, unit
    from tests.projects.test_projects import foreign_project, request

    db = project_db
    bu = unit(db)
    foreign_unit = unit(db, "FB", org=db.org_b)
    main = service(db).create(body(bu).model_copy(update={"name": "Plant expansion"}))
    db.connection.execute("UPDATE project SET status='active' WHERE id=%s", (main.id,))
    foreign_project(db)
    stranger = foreign_user(db)
    creator = insert_identity(db, "creator")
    grant_permissions(db, creator, "project.create", "project.read")
    url = f"/lookups/people?bu_id={bu.id}&q=creator"
    people = request(db, "GET", url, viewer=creator)
    assert people.status_code == 200, people.text
    assert [r["label"] for r in people.json()["rows"]] == [
        r["label"] for r in people.json()["rows"] if "creator" in r["label"]
    ] and people.json()["rows"]
    assert (
        str(stranger)
        not in request(db, "GET", f"/lookups/people?bu_id={bu.id}", viewer=creator).text
    )
    projects = request(db, "GET", f"/lookups/main_projects?bu_id={bu.id}&q=Plant", viewer=creator)
    assert [r["value"] for r in projects.json()["rows"]] == [str(main.id)]
    assert request(
        db, "GET", f"/lookups/main_projects?bu_id={bu.id}&q=zzz", viewer=creator
    ).json() == {"rows": []}
    # No project.create: 403. A foreign unit: 404.
    reader = insert_identity(db, "reader")
    grant_permissions(db, reader, "project.read")
    assert request(db, "GET", url, viewer=reader).status_code == 403
    assert (
        request(db, "GET", f"/lookups/people?bu_id={foreign_unit.id}", viewer=creator).status_code
        == 404
    )
