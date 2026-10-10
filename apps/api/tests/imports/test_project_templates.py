"""Concrete imports exercised through production middleware and real Postgres."""

import asyncio
import csv
import io
from datetime import date
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.service import user_id_by_email
from flo.modules.imports.handlers import HANDLERS
from flo.modules.imports.templates import REGISTRY
from flo.modules.imports.templates.builtin import register_builtin
from flo.modules.org.schemas import MasterCreate, OrgUnitCreate
from flo.modules.org.service import OrgService, unit_by_code
from flo.modules.projects.schemas import ProjectCreate, ProjectPatch
from flo.modules.projects.service import ProjectService, get_by_external_ref
from tests.authz.conftest import ROOT, load_migration
from tests.imports.test_validation import send
from tests.isolation.test_real_sessions import client, headers, login
from tests.org.test_bootstrap import settings


@pytest.fixture
def concrete(probe):
    conn, org, foreign, actor, _, _ = probe
    for tenant, user in (
        (org, actor),
        (
            foreign,
            conn.execute("SELECT id FROM identity WHERE email='second@example.test'").fetchone()[0],
        ),
    ):
        service = OrgService(conn, Scope(tenant), user)
        service.create_unit(OrgUnitCreate(code="BU", name="Business unit", kind="bu"))
        service.create_master(
            "department", MasterCreate(code="D", name="Department", effective_from=date(2000, 1, 1))
        )
        service.create_master(
            "ledger_account",
            MasterCreate(
                code="L",
                name="Ledger",
                effective_from=date(2000, 1, 1),
                attributes={"account_type": "expense"},
            ),
        )
    register_builtin()
    return probe


def project(ref="ROOT", **changes):
    return {
        "external_ref": ref,
        "bu_code": "BU",
        "name": ref,
        "department_code": "D",
        "ledger_account_code": "L",
        "currency": "USD",
        **changes,
    }


def allocation(key="A", ref="ROOT", **changes):
    return {
        "row_key": key,
        "project_ref": ref,
        "amount": "100.00",
        "currency": "USD",
        "effective_date": "2026-10-10",
        "reason": "Approved initial allocation",
        **changes,
    }


async def upload_template(browser, template, rows):
    columns = [column.name for column in REGISTRY[template].columns]
    output = io.StringIO(newline="")
    csv.writer(output).writerows(
        [columns, *[[row.get(column, "") for column in columns] for row in rows]]
    )
    response = await browser.post(
        "/api/v1/imports",
        data={"template": template},
        files={"file": (template + ".csv", output.getvalue().encode(), "text/csv")},
        headers=headers(browser, uuid4().hex),
    )
    assert response.status_code == 201, response.text
    id = response.json()["id"]
    mapping = await send(
        browser, "PUT", id, "mapping", {"mapping": dict(zip(columns, columns, strict=True))}
    )
    assert mapping.status_code == 200, mapping.text
    return id


async def validate(browser, template, rows):
    id = await upload_template(browser, template, rows)
    result = await send(browser, "POST", id, "validate")
    assert result.status_code == 200, result.text
    preview = (await send(browser, "GET", id, "preview")).json()["rows"]
    return id, result.json(), preview


async def commit(browser, id):
    response = await send(browser, "POST", id, "commit", {"mode": "atomic"})
    assert response.status_code == 200, response.text
    return response.json()


async def create_tree(browser):
    rows = [
        project(),
        project("CHILD-1", parent_external_ref="ROOT"),
        project("CHILD-2", parent_external_ref="ROOT"),
    ]
    id, batch, preview = await validate(browser, "projects", rows)
    assert batch["counts"]["create"] == 3, preview
    await commit(browser, id)
    return id, rows


def test_projects_create_tree_reimport_skip_and_update(concrete):
    conn, org, _, _, _, _ = concrete

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id, rows = await create_tree(browser)
            root = get_by_external_ref(conn, Scope(org), "ROOT")
            children = [
                get_by_external_ref(conn, Scope(org), ref) for ref in ("CHILD-1", "CHILD-2")
            ]
            assert root.number and all(
                child.number and child.parent_id == root.id for child in children
            )
            with tenant_transaction(conn, Scope(org)):
                assert conn.execute(
                    "SELECT external_ref,depth FROM project ORDER BY external_ref"
                ).fetchall() == [("CHILD-1", 2), ("CHILD-2", 2), ("ROOT", 1)]
                assert {
                    row[0]
                    for row in conn.execute(
                        "SELECT record_id FROM import_result WHERE batch_id=%s", (id,)
                    ).fetchall()
                } == {str(root.id), *(str(child.id) for child in children)}
            again, batch, preview = await validate(browser, "projects", rows)
            assert batch["counts"]["skip"] == 3, preview
            assert (await commit(browser, again))["result"]["unchanged"] == 3
            rows[1]["name"] = "Renamed child"
            changed, batch, preview = await validate(browser, "projects", rows)
            assert [row["action"] for row in preview] == ["skip", "update", "skip"]
            await commit(browser, changed)
            assert get_by_external_ref(conn, Scope(org), "CHILD-1").name == "Renamed child"
            assert get_by_external_ref(conn, Scope(org), "CHILD-2").version == children[1].version
            assert conn.execute("SELECT count(*) FROM project").fetchone() == (3,)

    asyncio.run(scenario())


def test_parent_order_error_names_row_and_blocks_atomic(concrete):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id, batch, preview = await validate(
                browser, "projects", [project("CHILD", parent_external_ref="ROOT"), project()]
            )
            issue = next(
                issue for issue in preview[0]["issues"] if issue["code"] == "parent_not_defined"
            )
            assert preview[0]["row_no"] == 2
            assert (
                issue["message"]
                == "Parent `ROOT` is not in the system and not listed on an earlier row. "
                "List parents before children."
            )
            assert batch["counts"]["error"] == 1
            response = await send(browser, "POST", id, "commit", {"mode": "atomic"})
            assert (
                response.status_code == 409 and response.json()["checks"]["problem"] == "has_errors"
            )
            assert concrete[0].execute("SELECT count(*) FROM project").fetchone() == (0,)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "column,value",
    [
        ("currency", "EUR"),
        ("parent_external_ref", ""),
        ("bu_code", "OTHER"),
        ("department_code", "OTHER"),
        ("ledger_account_code", "OTHER"),
    ],
)
def test_immutable_field_changed(concrete, column, value):
    conn, org, _, actor, _, _ = concrete
    svc = OrgService(conn, Scope(org), actor)
    svc.create_unit(OrgUnitCreate(code="OTHER", name="Other", kind="bu"))
    for kind in ("department", "ledger_account"):
        svc.create_master(
            kind,
            MasterCreate(
                code="OTHER",
                name="Other",
                effective_from=date(2000, 1, 1),
                attributes={"account_type": "expense"} if kind == "ledger_account" else {},
            ),
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            _, rows = await create_tree(browser)
            rows[1][column] = value
            id, batch, preview = await validate(browser, "projects", rows)
            assert any(
                issue["code"] == "immutable_field_changed" and issue["column"] == column
                for issue in preview[1]["issues"]
            )
            assert batch["counts"]["error"] == 1
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"})
            ).status_code == 409

    asyncio.run(scenario())


def test_lookup_tenancy_casefold_and_external_ref_guards(concrete):
    conn, org, foreign, actor, _, _ = concrete
    scope = Scope(org)
    assert unit_by_code(conn, scope, "bu").id == unit_by_code(conn, scope, "BU").id
    assert unit_by_code(conn, scope, "missing") is None
    assert user_id_by_email(conn, scope, "ADMIN@EXAMPLE.TEST") == actor
    assert user_id_by_email(conn, scope, "second@example.test") is None
    assert user_id_by_email(conn, scope, "missing@example.test") is None
    service = ProjectService(conn, scope, actor, settings(conn))
    unit = unit_by_code(conn, scope, "BU")
    body = ProjectCreate(
        bu_id=unit.id,
        name="Project",
        department_code="D",
        ledger_account_code="L",
        currency="USD",
        external_ref=" REF ",
    )
    record = service.create(body)
    assert record.external_ref == "REF"
    assert get_by_external_ref(conn, Scope(foreign), "REF") is None
    for patch in (ProjectPatch(external_ref="OTHER"), ProjectPatch(external_ref=None)):
        with pytest.raises(ProblemError) as error:
            service.update(record.id, patch, str(record.version))
        assert (
            error.value.code.value == "conflict"
            and error.value.checks["problem"] == "external_ref_locked"
        )
    with pytest.raises(ProblemError) as error:
        service.create(body)
    assert error.value.checks["problem"] == "external_ref_locked"
    unset = service.create(body.model_copy(update={"external_ref": None}))
    set_ref = service.update(unset.id, ProjectPatch(external_ref="NEW"), str(unset.version))
    assert set_ref.external_ref == "NEW"
    with pytest.raises(psycopg.errors.CheckViolation, match="external_ref_locked"):
        conn.execute("UPDATE project SET external_ref='CHANGED' WHERE id=%s", (record.id,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "UPDATE project SET external_ref='REF' WHERE id=%s",
            (service.create(body.model_copy(update={"external_ref": None})).id,),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE project SET external_ref='invalid ref' WHERE id=%s",
            (service.create(body.model_copy(update={"external_ref": None})).id,),
        )


def test_external_ref_migration_preserves_projects(concrete):
    conn, org, _, actor, _, _ = concrete
    scope = Scope(org)
    service = ProjectService(conn, scope, actor, settings(conn))
    unit = unit_by_code(conn, scope, "BU")
    record = service.create(
        ProjectCreate(
            bu_id=unit.id,
            name="Preserved",
            department_code="D",
            ledger_account_code="L",
            currency="USD",
        )
    )
    before = conn.execute("SELECT id,name,number,parent_id FROM project").fetchall()
    migration = load_migration(
        ROOT / "migrations/20261009_0036_project_external_ref.py", "external_ref"
    )
    migration.downgrade(conn)
    assert conn.execute("SELECT id,name,number,parent_id FROM project").fetchall() == before
    migration.upgrade(conn)
    assert service.get(record.id).external_ref is None
    assert conn.execute("SELECT id,name,number,parent_id FROM project").fetchall() == before


def test_builtin_registry_idempotent_and_downloads_have_examples(concrete):
    before = {name: HANDLERS[name] for name in ("projects", "budget_allocations")}
    register_builtin()
    assert before == {name: HANDLERS[name] for name in before}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            templates = (await browser.get("/api/v1/imports/templates")).json()
            for name in before:
                description = next(template for template in templates if template["name"] == name)
                response = await browser.get(f"/api/v1/imports/templates/{name}/file")
                assert response.status_code == 200
                rows = list(csv.reader(io.StringIO(response.text)))
                assert rows[-1] == [column["example"] for column in description["columns"]]
                for column in description["columns"]:
                    if column["type"] == "code":
                        assert column["accepted_codes_url"]
                    if column["accepted_codes_url"]:
                        assert (await browser.get(column["accepted_codes_url"])).status_code == 200

    asyncio.run(scenario())


def test_owner_change_warning(concrete):
    conn, org, foreign, actor, _, _ = concrete
    other = conn.execute("SELECT id FROM identity WHERE email='second@example.test'").fetchone()[0]
    role = conn.execute(
        "SELECT role_id FROM user_role WHERE org_id=%s AND user_id=%s LIMIT 1", (org, actor)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO user_role(id,org_id,user_id,role_id,scope_type,scope_id) "
        "VALUES(%s,%s,%s,%s,'org',%s)",
        (uuid4(), org, other, role, org),
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await create_tree(browser)
            id, batch, preview = await validate(
                browser, "projects", [project(owner_email="SECOND@EXAMPLE.TEST")]
            )
            assert batch["counts"]["update"] == batch["counts"]["warning"] == 1
            assert any(
                issue["code"] == "owner_changed" and issue["severity"] == "warning"
                for issue in preview[0]["issues"]
            )
            await commit(browser, id)
            assert get_by_external_ref(conn, Scope(org), "ROOT").owner_id == other

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "column,value,code",
    [
        ("bu_code", "ABSENT", "unknown_bu"),
        ("owner_email", "second@example.test", "unknown_owner"),
        ("department_code", "ABSENT", "unknown_code"),
        ("ledger_account_code", "ABSENT", "unknown_code"),
        ("currency", "XXX", "unknown_code"),
        ("name", "N" * 201, "invalid_name"),
    ],
)
def test_project_row_validation(concrete, column, value, code):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id, batch, preview = await validate(browser, "projects", [project(**{column: value})])
            assert batch["counts"]["error"] == 1
            assert any(
                issue["column"] == column and issue["code"] == code
                for issue in preview[0]["issues"]
            )
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"})
            ).status_code == 409

    asyncio.run(scenario())


def test_inactive_department_row_error(concrete):
    conn, org, _, _, _, _ = concrete
    conn.execute(
        "UPDATE master_record SET active=false WHERE org_id=%s AND kind='department'", (org,)
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id, batch, preview = await validate(browser, "projects", [project()])
            assert any(
                issue["column"] == "department_code"
                and issue["code"] == "inactive_code"
                and "D" in issue["message"]
                for issue in preview[0]["issues"]
            )
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"})
            ).status_code == 409
            assert conn.execute("SELECT count(*) FROM project").fetchone() == (0,)

    asyncio.run(scenario())


def test_project_create_permission_denied_at_bu(concrete):
    conn, org, _, _, _, _ = concrete
    conn.execute(
        "DELETE FROM role_permission WHERE org_id=%s AND permission_code='project.create'", (org,)
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id, batch, preview = await validate(browser, "projects", [project()])
            assert preview[0]["action"] == "error"
            assert any(issue["code"] == "permission_denied" for issue in preview[0]["issues"])
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"})
            ).status_code == 409
            assert conn.execute("SELECT count(*) FROM project").fetchone() == (0,)

    asyncio.run(scenario())


def test_1000_projects_background_progress(concrete):
    from tests.imports.test_async_import import sliced_clock, tick

    conn, org, _, _, _, _ = concrete

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rows = [project(f"P-{number}") for number in range(1000)]
            rows[500]["parent_external_ref"] = "P-499"
            id = await upload_template(browser, "projects", rows)
            response = await send(browser, "POST", id, "validate")
            assert response.status_code == 202
            resolver = app.state.authorization_resolver_factory
            for done in (500, 1000):
                assert tick(concrete, clock=sliced_clock(), resolver=resolver).done == 1
                batch = (await browser.get(f"/api/v1/imports/{id}")).json()
                assert batch["progress"]["rows_done"] == done
            assert batch["status"] == "validated" and batch["counts"]["create"] == 1000
            response = await send(browser, "POST", id, "commit", {"mode": "partial"})
            assert response.status_code == 202
            for done in (500, 1000):
                assert tick(concrete, clock=sliced_clock(), resolver=resolver).done == 1
                batch = (await browser.get(f"/api/v1/imports/{id}")).json()
                assert batch["progress"]["rows_done"] == done, batch
            assert batch["status"] == "committed" and batch["result"]["committed"] == 1000
            with tenant_transaction(conn, Scope(org)):
                assert conn.execute("SELECT count(*) FROM project").fetchone() == (1000,)
                assert conn.execute(
                    "SELECT count(*) FROM import_result WHERE batch_id=%s", (id,)
                ).fetchone() == (1000,)

    asyncio.run(scenario())


@pytest.mark.parametrize("roll_down", [False, True])
def test_allocation_recipient_lookup_direct_and_roll_down(concrete, roll_down):
    from flo.modules.budget.schemas import AllocationCreate
    from flo.modules.budget.service import allocate, entry_by_idempotency_key

    conn, org, foreign, actor, _, _ = concrete

    async def prepare():
        async with client(app) as browser:
            await login(browser)
            await create_tree(browser)

    asyncio.run(prepare())
    scope = Scope(org)
    root = get_by_external_ref(conn, scope, "ROOT")
    payload = AllocationCreate(
        amount="100.00",
        currency="USD",
        effective_date=date(2026, 10, 10),
        reason="Approved initial allocation",
    )
    direct = allocate(conn, scope, root.id, payload, actor, "lookup-direct")
    assert entry_by_idempotency_key(conn, scope, "lookup-direct") == direct.entries[0]
    assert entry_by_idempotency_key(conn, Scope(foreign), "lookup-direct") is None
    assert entry_by_idempotency_key(conn, scope, "missing") is None
    if roll_down:
        child = get_by_external_ref(conn, scope, "CHILD-1")
        result = allocate(
            conn, scope, child.id, payload, actor, "lookup-child", permitted_at=lambda parent: True
        )
        entry = entry_by_idempotency_key(conn, scope, "lookup-child")
        assert entry.project_id == child.id and entry.amount == payload.amount
        assert entry.id == result.entries[1].id


@pytest.mark.parametrize("roll_down", [False, True])
def test_allocations_reimport_skip_reconcile_and_recipient_result(concrete, roll_down):
    from flo.modules.budget.reconcile import reconcile_org
    from flo.modules.budget.service import entry_by_idempotency_key, get_balance
    from flo.modules.imports.handlers import RowContext
    from flo.modules.imports.templates.budget_allocations import row_key

    conn, org, _, actor, _, _ = concrete
    scope = Scope(org)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await create_tree(browser)
            root_id, batch, preview = await validate(browser, "budget_allocations", [allocation()])
            assert batch["counts"]["create"] == 1, preview
            await commit(browser, root_id)
            rows = (
                [allocation("CHILD-FUNDING", "CHILD-1", amount="25.00")]
                if roll_down
                else [allocation()]
            )
            if roll_down:
                id, batch, preview = await validate(browser, "budget_allocations", rows)
                assert batch["counts"]["create"] == 1, preview
                await commit(browser, id)
            else:
                id = root_id
            context = RowContext(conn, scope, actor, lambda permission, target: True, {})
            recipient = get_by_external_ref(conn, scope, rows[0]["project_ref"])
            entry = entry_by_idempotency_key(conn, scope, row_key(context, rows[0]))
            assert entry.project_id == recipient.id and entry.amount > 0
            with tenant_transaction(conn, scope):
                recorded = conn.execute(
                    "SELECT record_id FROM import_result WHERE batch_id=%s", (id,)
                ).fetchone()[0]
                assert str(recorded) == str(entry.id)
                before = conn.execute(
                    "SELECT id,project_id,amount FROM ledger_entry ORDER BY id"
                ).fetchall()
            again, batch, preview = await validate(browser, "budget_allocations", rows)
            assert batch["counts"]["skip"] == 1 and batch["counts"]["create"] == 0, preview
            assert (await commit(browser, again))["result"]["unchanged"] == 1
            assert (
                conn.execute("SELECT id,project_id,amount FROM ledger_entry ORDER BY id").fetchall()
                == before
            )
            assert str(get_balance(conn, scope, recipient.id).allocated) == (
                "25.0000" if roll_down else "100.0000"
            )
            run = reconcile_org(conn, scope)
            with tenant_transaction(conn, scope):
                assert conn.execute(
                    "SELECT status,drift_rows FROM reconcile_run WHERE id=%s", (run,)
                ).fetchone() == ("ok", 0)
            for changed in (
                {"amount": "26.00"},
                {"project_ref": "CHILD-2"},
                {"effective_date": "2026-10-11"},
                {"reason": "Different approval reason"},
            ):
                conflict_id, batch, preview = await validate(
                    browser, "budget_allocations", [rows[0] | changed]
                )
                assert preview[0]["action"] == "error"
                assert any(issue["code"] == "row_key_conflict" for issue in preview[0]["issues"])
                assert (
                    await send(browser, "POST", conflict_id, "commit", {"mode": "atomic"})
                ).status_code == 409

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "amount,code",
    [
        ("1,000.00", "invalid_amount"),
        ("1e3", "invalid_amount"),
        ("-5", "invalid_amount"),
        ("0", "invalid_amount"),
        ("12.345678", "amount_precision"),
    ],
)
def test_allocation_invalid_amount_row_errors(concrete, amount, code):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await create_tree(browser)
            id, batch, preview = await validate(
                browser, "budget_allocations", [allocation(amount=amount)]
            )
            assert batch["counts"]["error"] == 1
            assert any(
                issue["column"] == "amount" and issue["code"] == code
                for issue in preview[0]["issues"]
            )
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"})
            ).status_code == 409
            assert concrete[0].execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)

    asyncio.run(scenario())


def test_foreign_tenant_project_reference_row_error_no_write(concrete):
    conn, org, foreign, actor, _, _ = concrete
    foreign_actor = conn.execute(
        "SELECT id FROM identity WHERE email='second@example.test'"
    ).fetchone()[0]
    foreign_unit = unit_by_code(conn, Scope(foreign), "BU")
    record = ProjectService(conn, Scope(foreign), foreign_actor, settings(conn)).create(
        ProjectCreate(
            bu_id=foreign_unit.id,
            name="Foreign",
            department_code="D",
            ledger_account_code="L",
            currency="USD",
            external_ref="FOREIGN",
        )
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id, batch, preview = await validate(
                browser, "budget_allocations", [allocation(ref="FOREIGN")]
            )
            assert batch["counts"]["error"] == 1
            assert any(issue["code"] == "unknown_project" for issue in preview[0]["issues"])
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"})
            ).status_code == 409
            assert conn.execute(
                "SELECT count(*) FROM ledger_entry WHERE project_id=%s", (record.id,)
            ).fetchone() == (0,)
            assert (
                conn.execute(
                    "SELECT allocated FROM project_balance WHERE project_id=%s", (record.id,)
                ).fetchone()[0]
                == 0
            )

    asyncio.run(scenario())


def test_allocation_permission_denied_row_blocks_atomic(concrete):
    conn, org, _, _, _, _ = concrete

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await create_tree(browser)
            conn.execute(
                "DELETE FROM role_permission WHERE org_id=%s AND permission_code='budget.allocate'",
                (org,),
            )
            id, batch, preview = await validate(browser, "budget_allocations", [allocation()])
            assert batch["counts"]["error"] == 1
            assert any(issue["code"] == "permission_denied" for issue in preview[0]["issues"])
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"})
            ).status_code == 409
            assert conn.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)

    asyncio.run(scenario())


def test_concurrent_allocation_batches_post_once(concrete):
    conn, org, _, _, _, _ = concrete

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await create_tree(browser)
            first, _, _ = await validate(browser, "budget_allocations", [allocation()])
            second, _, _ = await validate(browser, "budget_allocations", [allocation()])
            results = await asyncio.gather(
                send(browser, "POST", first, "commit", {"mode": "atomic"}),
                send(browser, "POST", second, "commit", {"mode": "atomic"}),
            )
            assert sorted(response.status_code for response in results) == [200, 409]
            stale = next(response for response in results if response.status_code == 409)
            assert stale.json()["checks"]["problem"] == "stale_validation"
            with tenant_transaction(conn, Scope(org)):
                assert conn.execute("SELECT count(*),sum(amount) FROM ledger_entry").fetchone() == (
                    1,
                    100,
                )
                assert conn.execute(
                    "SELECT allocated,available FROM project_balance p "
                    "JOIN project j ON j.id=p.project_id WHERE j.external_ref='ROOT'"
                ).fetchone() == (100, 100)

    asyncio.run(scenario())


def test_result_id_migration_preserves_uuid_and_removes_integer(concrete):
    conn, org, _, _, _, _ = concrete

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            batch_id, _ = await create_tree(browser)
            before = conn.execute(
                "SELECT record_id FROM import_result WHERE batch_id=%s ORDER BY row_no",
                (batch_id,),
            ).fetchall()
            conn.execute(
                "INSERT INTO import_result(org_id,batch_id,row_no,record_type,record_id) "
                "VALUES(%s,%s,99,'ledger_entry','123')",
                (org, batch_id),
            )
            migration = load_migration(
                ROOT / "migrations/20261009_0036_project_external_ref.py", "result_ids"
            )
            migration.downgrade(conn)
            assert [
                str(row[0])
                for row in conn.execute(
                    "SELECT record_id FROM import_result WHERE batch_id=%s ORDER BY row_no",
                    (batch_id,),
                ).fetchall()
            ] == [row[0] for row in before]
            migration.upgrade(conn)
            assert (
                conn.execute(
                    "SELECT record_id FROM import_result WHERE batch_id=%s ORDER BY row_no",
                    (batch_id,),
                ).fetchall()
                == before
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("background", [False, True])
def test_partial_allocation_failure_preserves_rule(concrete, background):
    from tests.imports.test_async_import import tick

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await create_tree(browser)
            rows = [allocation(f"FAIL-{i}", "CHILD-1") for i in range(501 if background else 1)]
            batch_id = await upload_template(browser, "budget_allocations", rows)
            response = await send(browser, "POST", batch_id, "validate")
            if background:
                assert response.status_code == 202
                assert tick(concrete, resolver=app.state.authorization_resolver_factory).done == 1
            else:
                assert response.status_code == 200
            response = await send(browser, "POST", batch_id, "commit", {"mode": "partial"})
            if background:
                assert response.status_code == 202
                assert tick(concrete, resolver=app.state.authorization_resolver_factory).done == 1
            else:
                assert response.status_code == 200
            preview = (await browser.get(f"/api/v1/imports/{batch_id}/preview")).json()
            assert preview["rows"][0]["action"] == "error", preview
            assert any(
                issue["code"] == "insufficient-budget" for issue in preview["rows"][0]["issues"]
            )
            stored = (
                concrete[0]
                .execute(
                    "SELECT issues FROM import_row WHERE batch_id=%s AND row_no=2", (batch_id,)
                )
                .fetchone()[0]
            )
            assert stored[-1]["problem"] == "insufficient_budget"
            assert preview["rows"][0]["issues"][-1]["problem"] == "insufficient_budget"

    asyncio.run(scenario())
