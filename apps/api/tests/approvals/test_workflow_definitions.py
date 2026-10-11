"""APR-011, ACC-007 and AUD-001 through production middleware and Postgres."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import WorkflowCreate, WorkflowVersionWrite
from flo.modules.approvals.service import WorkflowService, resolve_version
from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
from tests.authz.conftest import ROOT, load_migration
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import admin_id, connection_url, create
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db

GRAPH = {
    "nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
    "edges": [{"from": "start", "to": "end"}],
}


@pytest.fixture
def workflow_db(bootstrap_db, monkeypatch):
    conn = bootstrap_db
    a = create(conn)
    b = create(conn, "SECOND", "second@example.test")
    monkeypatch.setenv("DATABASE_URL", connection_url(conn))
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", "A" * 43)
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_MEMORY_COST_KIB", "8192")
    from flo.api.origin_auth import require_origin_secret

    app.dependency_overrides[require_origin_secret] = lambda: None
    app.middleware_stack = None
    with correlation_context("workflow-tests"):
        yield conn, a.org_id, b.org_id, admin_id(conn)
    app.dependency_overrides.clear()
    app.middleware_stack = None


def svc(db, org=None, conn=None):
    return WorkflowService(conn or db[0], Scope(org or db[1]), db[3])


def definition(db, org=None, **changes):
    return svc(db, org).create(
        WorkflowCreate(document_type="requisition", name="Review", scope_type="org", **changes)
    )


def draft(db, row, graph=None):
    return svc(db).create_version(row.id, WorkflowVersionWrite(graph=graph or GRAPH))


async def send(browser, method, suffix="", data=None, **kwargs):
    if suffix.endswith("/publish") and data is None:
        data = {"acknowledge_warnings": False}
    return await browser.request(
        method,
        "/api/v1/workflows" + suffix,
        json=data,
        headers=headers(browser, kwargs.pop("key", uuid4().hex)) | kwargs.pop("extra", {}),
        **kwargs,
    )


def test_publish_preserves_graph_and_resolves_latest(workflow_db):
    db = workflow_db
    row = definition(db)
    first = draft(db, row)
    svc(db).publish(row.id, 1)
    before = (
        db[0]
        .execute("SELECT graph::text FROM workflow_version WHERE id=%s", (first.id,))
        .fetchone()
    )
    draft(db, row, GRAPH | {"layout": {"start": {"x": 10, "y": 20}}})
    svc(db).publish(row.id, 2)
    assert (
        db[0]
        .execute("SELECT graph::text FROM workflow_version WHERE id=%s", (first.id,))
        .fetchone()
        == before
    )
    assert svc(db).get_version(row.id, 1).status == "superseded"
    assert resolve_version(db[0], Scope(db[1]), "requisition", None).version == 2
    svc(db).retire(row.id)
    assert resolve_version(db[0], Scope(db[1]), "requisition", None) is None
    assert svc(db).get_version(row.id, 1).graph == first.graph


def test_application_role_immutability_and_delete_guards(workflow_db):
    db = workflow_db
    row = definition(db)
    draft(db, row)
    svc(db).publish(row.id, 1)
    draft(db, row)
    svc(db).publish(row.id, 2)
    draft(db, row)
    role = "workflow_test_" + uuid4().hex
    conn = db[0]
    conn.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        conn.execute(
            sql.SQL(
                "GRANT SELECT,UPDATE,DELETE,INSERT ON workflow_version,workflow_definition TO {}"
            ).format(sql.Identifier(role))
        )
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        for statement in [
            "UPDATE workflow_version SET graph='{}' WHERE status='published'",
            "UPDATE workflow_version SET row_version=row_version+1 WHERE status='superseded'",
            "UPDATE workflow_version SET row_version=row_version WHERE status='draft'",
            "DELETE FROM workflow_version WHERE status='published'",
            "DELETE FROM workflow_version WHERE status='superseded'",
        ]:
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                with tenant_transaction(conn, Scope(db[1])):
                    conn.execute(statement)
        with tenant_transaction(conn, Scope(db[1])):
            assert conn.execute("DELETE FROM workflow_version WHERE status='draft'").rowcount == 1
        with tenant_transaction(conn, Scope(db[2])):
            assert conn.execute("SELECT * FROM workflow_version").fetchall() == []
            assert conn.execute("SELECT * FROM workflow_definition").fetchall() == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_transaction(conn, Scope(db[2])):
                conn.execute("UPDATE workflow_definition SET name='foreign' WHERE id=%s", (row.id,))
                # Explicit foreign insert must be refused, not merely hidden by USING.
                conn.execute(
                    "INSERT INTO workflow_version(id,org_id,definition_id,version,status,"
                    "graph,created_by) "
                    "VALUES(%s,%s,%s,4,'draft','{}',%s)",
                    (uuid4(), db[1], row.id, db[3]),
                )
    finally:
        conn.execute("RESET ROLE")
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_bu_override_and_fallback(workflow_db):
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService

    db = workflow_db
    org = OrgService(db[0], Scope(db[1]), db[3])
    bu = org.create_unit(OrgUnitCreate(code="A", name="A", kind="bu"))
    other = org.create_unit(OrgUnitCreate(code="B", name="B", kind="bu"))
    row = definition(db)
    draft(db, row)
    svc(db).publish(row.id, 1)
    override = svc(db).create(
        WorkflowCreate(
            document_type="requisition", name="Override", scope_type="bu", scope_id=bu.id
        )
    )
    draft(db, override)
    published = svc(db).publish(override.id, 1)
    assert resolve_version(db[0], Scope(db[1]), "requisition", bu.id).id == published.id
    assert resolve_version(db[0], Scope(db[1]), "requisition", other.id).definition_id == row.id
    svc(db).retire(row.id)
    assert resolve_version(db[0], Scope(db[1]), "requisition", other.id) is None


def test_concurrent_publish_and_lock(workflow_db, monkeypatch):
    from flo.modules.approvals.models import WorkflowRepository

    db = workflow_db
    row = definition(db)
    draft(db, row)
    original = WorkflowRepository.version

    def checked(repo, id, version):
        assert (
            repo.connection.execute(
                "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND "
                "relation='workflow_definition'::regclass AND mode='RowShareLock'"
            ).fetchone()[0]
            > 0
        ), "publish must lock before reading version"
        return original(repo, id, version)

    monkeypatch.setattr(WorkflowRepository, "version", checked)
    barrier = Barrier(2)

    def publish(_):
        with psycopg.connect(connection_url(db[0]), autocommit=True) as conn:
            with correlation_context(uuid4().hex):
                barrier.wait(timeout=5)
                try:
                    svc(db, conn=conn).publish(row.id, 1)
                    return "ok"
                except ProblemError as exc:
                    return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(publish, range(2))) == ["ok", "version_immutable"]
    assert db[0].execute(
        "SELECT count(*) FROM workflow_version WHERE status='published'"
    ).fetchone() == (1,)


def test_writes_audit_and_idempotency(workflow_db):
    db = workflow_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            body = {"document_type": "requisition", "name": "Review", "scope_type": "org"}
            from flo.kernel.session import CSRF_HEADER_NAME

            missing = await browser.post(
                "/api/v1/workflows",
                json=body,
                headers={CSRF_HEADER_NAME: headers(browser)[CSRF_HEADER_NAME]},
            )
            assert missing.status_code == 400
            result = await send(browser, "POST", data=body, key="create-once")
            assert result.status_code == 201, result.text
            assert (
                await send(browser, "POST", data=body, key="create-once")
            ).json() == result.json()
            id = result.json()["id"]
            assert (await send(browser, "POST", data=body)).status_code == 409
            path = f"/{id}/versions"
            assert (await send(browser, "POST", path, {"graph": GRAPH})).status_code == 201
            assert (await send(browser, "POST", path, {"graph": GRAPH})).json()["checks"][
                "problem"
            ] == "draft_exists"
            assert (await send(browser, "GET", path + "/1")).headers["etag"] == "1"
            assert (await send(browser, "PUT", path + "/1", {"graph": GRAPH})).status_code == 400
            updated = await send(
                browser, "PUT", path + "/1", {"graph": GRAPH}, extra={"If-Match": "1"}
            )
            assert updated.status_code == 200, updated.text
            assert (
                await send(browser, "PUT", path + "/1", {"graph": GRAPH}, extra={"If-Match": "1"})
            ).json()["checks"]["problem"] == "stale_version"
            assert (await send(browser, "POST", path + "/1/publish")).status_code == 200
            assert (
                await send(browser, "PUT", path + "/1", {"graph": GRAPH}, extra={"If-Match": "2"})
            ).json()["checks"]["problem"] == "version_immutable"
            assert (await send(browser, "POST", f"/{id}/retire")).status_code == 200

    asyncio.run(scenario())
    rows = (
        db[0]
        .execute(
            "SELECT action,before,after FROM audit_log WHERE target_type='workflow' ORDER "
            "BY occurred_at,id"
        )
        .fetchall()
    )
    assert [r[0] for r in rows] == [
        "workflow.create",
        "workflow.version.create",
        "workflow.version.update",
        "workflow.publish",
        "workflow.retire",
    ]
    assert all(r[2] for r in rows)
    assert all(r[1] for r in rows[2:])
    assert rows[3][1]["status"] == "draft" and rows[3][2]["status"] == "published"
    assert "superseded_version_id" in rows[3][2]


GRAPH_CASES = [
    (GRAPH | {"nodes": [{"id": "end", "kind": "end"}]}, "/nodes"),
    (GRAPH | {"nodes": [{"id": "start", "kind": "start"}]}, "/nodes"),
    (GRAPH | {"nodes": GRAPH["nodes"] + [{"id": "end", "kind": "end"}]}, "/nodes/2/id"),
    (GRAPH | {"nodes": [{"id": "BAD", "kind": "start"}, GRAPH["nodes"][1]]}, "/nodes/0/id"),
    (GRAPH | {"edges": [{"from": "missing", "to": "end"}]}, "/edges/0/from"),
    (GRAPH | {"nodes": GRAPH["nodes"] * 101}, "/nodes"),
    (GRAPH | {"edges": GRAPH["edges"] * 401}, "/edges"),
    (
        GRAPH | {"nodes": [GRAPH["nodes"][0] | {"name": "x" * (256 * 1024)}, GRAPH["nodes"][1]]},
        "/",
    ),
    (GRAPH | {"edges": [{"from": "start", "to": "end", "when": []}]}, "/edges/0/when"),
]


@pytest.mark.parametrize("graph,field", GRAPH_CASES)
def test_structural_rules_have_json_pointers(graph, field):
    from flo.modules.approvals.schemas import parse_graph

    with pytest.raises(ProblemError) as exc:
        parse_graph(graph)
    assert exc.value.errors[0].field == field


def test_structural_rules_http_422(workflow_db):
    row = definition(workflow_db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for graph, field in GRAPH_CASES:
                result = await send(browser, "POST", f"/{row.id}/versions", {"graph": graph})
                assert result.status_code == 422, result.text
                assert result.json()["errors"][0]["field"] == field

    asyncio.run(scenario())


def test_foreign_tenant_routes_and_authorization(workflow_db):
    db = workflow_db
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService

    foreign_bu = OrgService(db[0], Scope(db[2]), db[3]).create_unit(
        OrgUnitCreate(code="FOREIGN", name="Foreign", kind="bu")
    )
    foreign = definition(db, db[2])
    own = definition(db)
    draft(db, own)
    paths = [
        ("GET", f"/{foreign.id}", None),
        ("POST", f"/{foreign.id}/versions", {"graph": GRAPH}),
        ("GET", f"/{foreign.id}/versions/1", None),
        ("GET", f"/{foreign.id}/versions/1/validation", None),
        ("PUT", f"/{foreign.id}/versions/1", {"graph": GRAPH}),
        ("POST", f"/{foreign.id}/versions/1/publish", None),
        ("POST", f"/{foreign.id}/retire", None),
    ]

    async def scenario():
        async with client(app) as browser:
            await browser.get("/api/v1/auth/sessions")
            for method, path, body in paths + [
                ("GET", "", None),
                ("POST", "", {"document_type": "po", "name": "P", "scope_type": "org"}),
            ]:
                assert (await send(browser, method, path, body)).status_code == 401
            await login(browser)
            assert (
                await send(
                    browser,
                    "POST",
                    data={
                        "document_type": "po",
                        "name": "Foreign",
                        "scope_type": "bu",
                        "scope_id": str(foreign_bu.id),
                    },
                )
            ).status_code == 404
            for method, path, body in paths:
                assert (await send(browser, method, path, body)).status_code == 404
            assert [r["id"] for r in (await send(browser, "GET")).json()["rows"]] == [str(own.id)]
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                )
            for method, path, body in paths:
                assert (
                    await send(browser, method, path.replace(str(foreign.id), str(own.id)), body)
                ).status_code == 404
            assert (await send(browser, "GET")).status_code == 403
            assert (
                await send(
                    browser, "POST", data={"document_type": "po", "name": "P", "scope_type": "org"}
                )
            ).status_code == 403
            with tenant_transaction(db[0], Scope(db[1])):
                identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
                role = (
                    db[0]
                    .execute("SELECT id FROM role WHERE org_id=%s AND code='requestor'", (db[1],))
                    .fetchone()[0]
                )
                identity.grant_role(db[3], role, AuthorizationTarget.organization(db[1]))
            for method, path, body in paths:
                assert (
                    await send(browser, method, path.replace(str(foreign.id), str(own.id)), body)
                ).status_code == 403

    asyncio.run(scenario())


def test_auditor_read_only_and_existing_tenant_backfill(workflow_db):
    db = workflow_db
    migration = load_migration(
        ROOT / "migrations/20261009_0038_workflow_permissions.py", "workflow_permission_roundtrip"
    )
    migration.downgrade(db[0])
    migration.upgrade(db[0])
    assert db[0].execute(
        "SELECT permission_code FROM role_permission rp JOIN role r ON r.id=rp.role_id "
        "AND r.org_id=rp.org_id WHERE r.org_id=%s AND r.code='auditor' "
        "AND permission_code LIKE 'workflow.%%'",
        (db[1],),
    ).fetchall() == [("workflow.read",)]
    assert {
        r[0]
        for r in db[0]
        .execute(
            "SELECT permission_code FROM role_permission rp JOIN role r "
            "ON r.id=rp.role_id AND r.org_id=rp.org_id "
            "WHERE r.org_id=%s AND r.code='organization-administrator' "
            "AND permission_code LIKE 'workflow.%%'",
            (db[1],),
        )
        .fetchall()
    } == {"workflow.read", "workflow.manage"}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                )
                role = (
                    db[0]
                    .execute("SELECT id FROM role WHERE org_id=%s AND code='auditor'", (db[1],))
                    .fetchone()[0]
                )
                IdentityAuthorizationService(db[0], Scope(db[1]), db[3]).grant_role(
                    db[3], role, AuthorizationTarget.organization(db[1])
                )
            assert (await send(browser, "GET")).status_code == 200
            assert (
                await send(
                    browser, "POST", data={"document_type": "po", "name": "P", "scope_type": "org"}
                )
            ).status_code == 403

    asyncio.run(scenario())


def test_database_unique_scope_and_version_guards(workflow_db):
    db = workflow_db
    row = definition(db)
    first = draft(db, row)
    for status in ("draft", "published"):
        if status == "published":
            svc(db).publish(row.id, 1)
        with pytest.raises(psycopg.errors.UniqueViolation):
            db[0].execute(
                "INSERT INTO workflow_version(id,org_id,definition_id,version,status,"
                "graph,created_by) "
                "SELECT %s,org_id,definition_id,2,status,graph,created_by FROM workflow_version "
                "WHERE id=%s",
                (uuid4(), first.id),
            )
    with pytest.raises(psycopg.errors.UniqueViolation):
        db[0].execute(
            "INSERT INTO workflow_definition(id,org_id,document_type,name,scope_type,"
            "scope_id,created_by) "
            "SELECT %s,org_id,document_type,name,scope_type,scope_id,created_by "
            "FROM workflow_definition WHERE id=%s",
            (uuid4(), row.id),
        )

    for column in ("version", "row_version"):
        with pytest.raises(psycopg.errors.CheckViolation):
            db[0].execute(
                "INSERT INTO workflow_version(id,org_id,definition_id,version,status,"
                "graph,created_by,row_version) "
                f"SELECT %s,org_id,definition_id,{0 if column == 'version' else 3},"
                f"'draft',graph,created_by,{0 if column == 'row_version' else 1} "
                "FROM workflow_version WHERE id=%s",
                (uuid4(), first.id),
            )
    with pytest.raises(psycopg.errors.CheckViolation):
        db[0].execute(
            "INSERT INTO workflow_definition(id,org_id,document_type,name,scope_type,"
            "scope_id,created_by) SELECT %s,org_id,document_type,name,'org',%s,created_by "
            "FROM workflow_definition WHERE id=%s",
            (uuid4(), uuid4(), row.id),
        )
    foreign = definition(db, db[2])
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db[0].execute(
            "INSERT INTO workflow_version(id,org_id,definition_id,version,status,graph,created_by) "
            "VALUES(%s,%s,%s,1,'draft','{}',%s)",
            (uuid4(), db[1], foreign.id, db[3]),
        )


@pytest.mark.parametrize(
    "node",
    [
        {"approvers": {"type": "users", "user_ids": []}, "mode": "any"},
        {"approvers": {"type": "users", "user_ids": [str(uuid4())] * 51}, "mode": "all"},
        {"approvers": {"type": "role", "role_code": ""}, "mode": "any"},
        {"approvers": {"type": "permission"}, "mode": {"quorum": 0}},
        {"approvers": {"type": "permission"}, "mode": "any", "due_days": 0},
        {"approvers": {"type": "permission"}, "mode": "any", "due_days": 366},
        {"mode": "any"},
    ],
)
def test_approval_node_contract_guards(node):
    from flo.modules.approvals.schemas import parse_graph

    with pytest.raises(ProblemError) as exc:
        parse_graph(
            GRAPH | {"nodes": GRAPH["nodes"] + [{"id": "approval", "kind": "approval"} | node]}
        )
    assert exc.value.errors[0].field.startswith("/nodes/2")


def test_list_keyset_paging_and_filter_binding(workflow_db):
    db = workflow_db
    ids = []
    for _ in range(51):
        row = definition(db)
        ids.append(row.id)
        svc(db).retire(row.id)
    page = svc(db).list("requisition", None)
    assert [row.id for row in page.rows] == ids[:50]
    assert page.next_cursor is not None
    last = svc(db).list("requisition", page.next_cursor)
    assert [row.id for row in last.rows] == ids[50:] and last.next_cursor is None
    assert svc(db).list("po", None).rows == []
    for cursor in ("invalid", page.next_cursor):
        with pytest.raises(ProblemError) as exc:
            svc(db).list("po", cursor)
        assert exc.value.errors[0].field == "/cursor"


def test_baseline_roles_workflow_permissions(workflow_db):
    from flo.modules.identity.models import BASELINE_ROLES

    db = workflow_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for code, _ in BASELINE_ROLES:
                with tenant_transaction(db[0], Scope(db[1])):
                    db[0].execute(
                        "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                    )
                    role = (
                        db[0]
                        .execute("SELECT id FROM role WHERE org_id=%s AND code=%s", (db[1], code))
                        .fetchone()[0]
                    )
                    IdentityAuthorizationService(db[0], Scope(db[1]), db[3]).grant_role(
                        db[3], role, AuthorizationTarget.organization(db[1])
                    )
                read = await send(browser, "GET")
                assert read.status_code == (
                    200 if code in {"organization-administrator", "auditor"} else 403
                ), (code, read.text)
                write = await send(
                    browser,
                    "POST",
                    data={"document_type": "po", "name": "Role test", "scope_type": "org"},
                )
                assert write.status_code == (
                    201 if code == "organization-administrator" else 403
                ), (code, write.text)

    asyncio.run(scenario())


def test_graph_size_guard_includes_persisted_defaults():
    import json

    from flo.modules.approvals.schemas import parse_graph

    graph = GRAPH | {"nodes": [GRAPH["nodes"][0] | {"name": ""}, GRAPH["nodes"][1]]}
    raw_size = len(json.dumps(graph, separators=(",", ":")).encode())
    graph["nodes"][0]["name"] = "x" * (256 * 1024 - raw_size)
    assert len(json.dumps(graph, separators=(",", ":")).encode()) == 256 * 1024
    with pytest.raises(ProblemError) as exc:
        parse_graph(graph)
    assert exc.value.errors[0].field == "/"


def test_approver_error_pointer_names_the_submitted_field():
    from flo.modules.approvals.schemas import parse_graph

    with pytest.raises(ProblemError) as exc:
        parse_graph(
            GRAPH
            | {
                "nodes": GRAPH["nodes"]
                + [
                    {
                        "id": "approval",
                        "kind": "approval",
                        "mode": "any",
                        "approvers": {"type": "users", "user_ids": []},
                    }
                ]
            }
        )
    assert exc.value.errors[0].field == "/nodes/2/approvers/user_ids"


@pytest.mark.parametrize(
    "body",
    [
        {"scope_type": "org", "scope_id": None},
        {"scope_type": "org", "scope_id": str(uuid4())},
        {"scope_type": "bu"},
    ],
)
def test_workflow_scope_shape_guard(body):
    with pytest.raises(ProblemError) as exc:
        WorkflowCreate(document_type="project", name="Review", **body)
    assert exc.value.errors[0].field == "/scope_id"


def test_all_start_and_edge_endpoint_guards():
    from flo.modules.approvals.schemas import parse_graph

    for graph, field in [
        (GRAPH | {"nodes": GRAPH["nodes"] + [{"id": "another_start", "kind": "start"}]}, "/nodes"),
        (GRAPH | {"edges": [{"from": "start", "to": "missing"}]}, "/edges/0/to"),
    ]:
        with pytest.raises(ProblemError) as exc:
            parse_graph(graph)
        assert exc.value.errors[0].field == field


def test_present_condition_must_be_an_object():
    from flo.modules.approvals.schemas import parse_graph

    with pytest.raises(ProblemError) as exc:
        parse_graph(GRAPH | {"edges": [{"from": "start", "to": "end", "when": None}]})
    assert exc.value.errors[0].field == "/edges/0/when"


def test_graph_json_roundtrip_preserves_absent_condition():
    from flo.modules.approvals.schemas import parse_graph

    parsed = parse_graph(GRAPH)
    returned = parsed.model_dump(mode="json", by_alias=True)
    assert "when" not in returned["edges"][0]
    assert parse_graph(returned) == parsed


def test_graph_accepts_python_uuid_approvers():
    from flo.modules.approvals.schemas import parse_graph

    user = uuid4()
    graph = parse_graph(
        GRAPH
        | {
            "nodes": GRAPH["nodes"]
            + [
                {
                    "id": "approval",
                    "kind": "approval",
                    "mode": "any",
                    "approvers": {"type": "users", "user_ids": [user]},
                }
            ]
        }
    )
    assert graph.model_dump(mode="json", by_alias=True)["nodes"][2]["approvers"]["user_ids"] == [
        str(user)
    ]


def test_publish_validation_gate_and_warning_acknowledgement(workflow_db):
    from tests.approvals.test_graph_validation import chain

    db = workflow_db
    row = definition(db)
    graph = chain(2)
    for node in graph["nodes"][1:3]:
        node["approvers"] = {"type": "permission"}
    draft(db, row, graph)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            path = f"/{row.id}/versions/1"
            result = await send(browser, "POST", path + "/publish")
            assert result.status_code == 422, result.text
            assert result.json()["checks"]["problem"] == "workflow_invalid"
            assert result.json()["errors"][0]["field"] == "/nodes/2"
            assert svc(db).get_version(row.id, 1).status == "draft"
            graph["nodes"][2]["allow_repeat"] = True
            assert (
                await send(browser, "PUT", path, {"graph": graph}, extra={"If-Match": "1"})
            ).status_code == 200
            before = svc(db).get_version(row.id, 1)
            audit = db[0].execute("SELECT count(*) FROM audit_log").fetchone()
            validation = await browser.get("/api/v1/workflows" + path + "/validation")
            assert validation.status_code == 200
            assert validation.json()["warnings"][0]["code"] == "duplicate_approval"
            assert svc(db).get_version(row.id, 1) == before
            assert db[0].execute("SELECT count(*) FROM audit_log").fetchone() == audit
            result = await send(browser, "POST", path + "/publish")
            assert result.status_code == 409
            assert result.json()["checks"]["problem"] == "warnings_unacknowledged"
            result = await send(
                browser,
                "POST",
                path + "/publish",
                {"acknowledge_warnings": True},
                key="publish-repeat",
            )
            assert result.status_code == 200, result.text
            assert (
                await send(
                    browser,
                    "POST",
                    path + "/publish",
                    {"acknowledge_warnings": True},
                    key="publish-repeat",
                )
            ).json() == result.json()
            assert (
                await browser.get("/api/v1/workflows" + path + "/validation")
            ).json() == validation.json()

    asyncio.run(scenario())


def test_org_depth_setting_and_publish_boundary(workflow_db):
    from flo.modules.org.schemas import SettingPut
    from flo.modules.org.service import OrgService
    from tests.approvals.test_graph_validation import chain

    db = workflow_db
    org = OrgService(db[0], Scope(db[1]), db[3])
    for value in [0, 51, True, "3"]:
        with pytest.raises(ProblemError):
            org.set_setting("approval.max_depth", SettingPut(value=value))
    org.set_setting("approval.max_depth", SettingPut(value=3))
    row = definition(db)
    graph = chain(4)
    for node in graph["nodes"][1:5]:
        node["approvers"] = {"type": "role", "role_code": "approver"}
        node["allow_repeat"] = True
    draft(db, row, graph)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            result = await send(
                browser, "POST", f"/{row.id}/versions/1/publish", {"acknowledge_warnings": True}
            )
            assert result.status_code == 422
            assert "depth" in result.json()["errors"][0]["message"]
            org.set_setting("approval.max_depth", SettingPut(value=4))
            assert (
                await send(
                    browser, "POST", f"/{row.id}/versions/1/publish", {"acknowledge_warnings": True}
                )
            ).status_code == 200

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,op,value", [("text", "gt", "3"), ("code", "le", "3"), ("boolean", "lt", True)]
)
def test_invalid_ordering_blocks_publish(workflow_db, monkeypatch, kind, op, value):
    from tests.approvals.test_graph_validation import condition_graph

    monkeypatch.setattr("flo.modules.approvals.validation.field_catalog", lambda _: {"field": kind})
    row = definition(workflow_db)
    draft(workflow_db, row, condition_graph({"field": "field", "op": op, "value": value}))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            result = await send(browser, "POST", f"/{row.id}/versions/1/publish")
            assert result.status_code == 422
            assert result.json()["errors"][0]["field"] == "/edges/2"

    asyncio.run(scenario())
