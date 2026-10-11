from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
import pytest

from flo.kernel.errors import ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.projects.models import ProjectRepository
from tests.authz.conftest import ROOT, load_migration
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity, service_for

from .conftest import body, service, unit
from .test_projects import foreign_project, request


def planted_tree(db):
    bu = unit(db)
    svc = service(db)
    root = svc.create(body(bu))
    left, right = [], []
    for branch in (left, right):
        parent = root.id
        for _ in range(4):
            child = svc.create(body(bu, parent_id=parent))
            branch.append(child.id)
            parent = child.id
    return bu, root.id, left, right


def test_golden_traversals_are_one_statement(project_db):
    db = project_db
    bu, root, left, right = planted_tree(db)
    svc = service(db)
    for call, expected in [
        (lambda: [n.id for n in svc.ancestors(left[-1])], left[-2::-1] + [root]),
        (lambda: {n.id for n in svc.descendants(root)}, {root, *left, *right}),
        (lambda: [n.id for n in svc.descendants(left[0], False)], left[1:]),
        (lambda: svc.lowest_common_ancestor(left[-1], right[-1]).id, root),
        (
            lambda: svc.path_between(left[-1], right[-1]).model_dump(),
            {"up": left[::-1], "lca": root, "down": right},
        ),
        (lambda: svc.depth_of(left[-1]), 5),
    ]:
        statements = []
        original = ProjectRepository.execute

        def count(repo, query, params=None):
            statements.append(query)
            return original(repo, query, params)

        with patch.object(ProjectRepository, "execute", count):
            assert call() == expected
        assert len(statements) == 1
    assert svc.path_between(root, root).model_dump() == {"up": [], "lca": root, "down": []}
    other = svc.create(body(bu))
    assert svc.lowest_common_ancestor(root, other.id) is None
    assert svc.path_between(root, other.id).lca is None
    with pytest.raises(ProblemError):
        svc.ancestors(uuid4())


def direct_child(db, bu, parent, number="direct"):
    return db.connection.execute(
        """INSERT INTO project(id, org_id, bu_id, parent_id, number, name, owner_id,
        department_code, ledger_account_code, currency, created_by)
        VALUES (%s,%s,%s,%s,%s,'Direct',%s,'D','L','USD',%s) RETURNING id,depth,root_id""",
        (uuid4(), db.org_a, bu.id, parent, number, db.actor_id, db.actor_id),
    ).fetchone()


def test_depth_trigger_and_service_reject_sixth_level(project_db):
    db = project_db
    bu, root, left, _ = planted_tree(db)
    result = request(db, "POST", json=body(bu, parent_id=left[-1]).model_dump(mode="json"))
    assert result.status_code == 422
    assert result.json()["type"].endswith("/validation-failed")
    assert result.json()["checks"]["problem"] == "project_depth_exceeded"
    with pytest.raises(psycopg.errors.CheckViolation):
        direct_child(db, bu, left[-1])
    inserted = direct_child(db, bu, root)
    assert inserted[1:] == (2, root)


@pytest.mark.parametrize("column", ["parent_id", "org_id"])
def test_structural_columns_are_database_immutable(project_db, column):
    db = project_db
    _, root, left, _ = planted_tree(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.connection.execute(
            f"UPDATE project SET {column} = %s WHERE id = %s",
            (db.org_b if column == "org_id" else root, left[-1]),
        )


def test_cycle_terminates_without_duplicates(project_db):
    db = project_db
    bu = unit(db)
    a, b = uuid4(), uuid4()
    with db.connection.transaction():
        db.connection.execute("SET LOCAL session_replication_role = replica")
        db.connection.execute(
            """INSERT INTO project(id, org_id, bu_id, parent_id, number, name, owner_id,
            department_code, ledger_account_code, currency, created_by, depth, root_id)
            VALUES (%s,%s,%s,%s,'cycle-a','A',%s,'D','L','USD',%s,2,%s),
            (%s,%s,%s,%s,'cycle-b','B',%s,'D','L','USD',%s,2,%s)""",
            (
                a,
                db.org_a,
                bu.id,
                b,
                db.actor_id,
                db.actor_id,
                a,
                b,
                db.org_a,
                bu.id,
                a,
                db.actor_id,
                db.actor_id,
                a,
            ),
        )
        db.connection.execute("SET LOCAL statement_timeout = '2s'")
        assert [n.id for n in service(db).ancestors(a)] == [b]
        assert {n.id for n in service(db).descendants(a)} == {a, b}


def test_children_foreign_project(project_db):
    row = foreign_project(project_db)
    assert request(project_db, "GET", f"/{row.id}/children").status_code == 404


def test_tree_foreign_project(project_db):
    row = foreign_project(project_db)
    assert request(project_db, "GET", f"/{row.id}/tree").status_code == 404


def test_foreign_parent_and_direct_database_parent_guard(project_db):
    db = project_db
    foreign = foreign_project(db)
    bu = unit(db)
    assert (
        request(db, "POST", json=body(bu, parent_id=foreign.id).model_dump(mode="json")).status_code
        == 404
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        direct_child(db, bu, foreign.id)


def test_children_pagination_and_tree_depth_and_cap(project_db):
    db = project_db
    bu, root, left, right = planted_tree(db)
    svc = service(db)
    page = svc.children(root, page_size=1)
    assert len(page.rows) == 1 and page.next_cursor
    next_page = svc.children(root, cursor=page.next_cursor, page_size=1)
    assert {page.rows[0].id, next_page.rows[0].id} == {left[0], right[0]}
    assert next_page.next_cursor is None
    tree = request(db, "GET", f"/{root}/tree?max_depth=2")
    assert tree.status_code == 200
    assert len(tree.json()["tree"]["children"]) == 2
    assert all(not n["children"] for n in tree.json()["tree"]["children"])
    for i in range(501):
        direct_child(db, bu, root, str(i))
    capped = svc.tree(root)

    def count(node):
        return 1 + sum(count(n) for n in node.children)

    assert capped.truncated and count(capped.tree) == 500
    assert request(db, "GET", f"/{root}/children?page_size=51").status_code == 422
    assert request(db, "GET", f"/{root}/tree?max_depth=6").status_code == 422
    for cursor in ("bad", "W10=", page.next_cursor):
        with pytest.raises(ProblemError):
            svc.children(left[0], cursor)
    for call in (lambda: svc.children(root, page_size=0), lambda: svc.tree(root, 0)):
        with pytest.raises(ProblemError):
            call()


@pytest.mark.parametrize("route", ["children", "tree"])
@pytest.mark.parametrize("grant", [False, True])
def test_read_route_permission_guard(project_db, route, grant):
    db = project_db
    row = service(db).create(body(unit(db)))
    viewer = insert_identity(db, "denied")
    if grant:
        grant_permissions(db, viewer, "master.read")
    assert request(db, "GET", f"/{row.id}/{route}", viewer=viewer).status_code == (
        403 if grant else 404
    )


def test_parent_read_and_cross_bu_create_guard_and_scope(project_db):
    db = project_db
    parent_bu, child_bu = unit(db, "P"), unit(db, "C")
    parent = service(db).create(body(parent_bu))
    viewer = insert_identity(db, "child-author")
    with service_for(db, Scope(db.org_a), "grants") as identity:
        create = identity.create_role("child-create", "Create")
        identity.grant_permission(create.id, "project.create")
        identity.grant_role(viewer, create.id, AuthorizationTarget(ScopeType.BU, child_bu.id))
        payload = body(child_bu, parent_id=parent.id).model_dump(mode="json")
        assert request(db, "POST", json=payload, viewer=viewer).status_code == 404
        read = identity.create_role("parent-read", "Read")
        identity.grant_permission(read.id, "project.read")
        identity.grant_role(viewer, read.id, AuthorizationTarget(ScopeType.PROJECT, parent.id))
        response = request(db, "POST", json=payload, viewer=viewer)
        assert response.status_code == 201, response.text
        child = UUID(response.json()["id"])
        assert db.connection.execute(
            "SELECT parent_scope_type,parent_scope_id FROM authorization_scope WHERE scope_id=%s",
            (child,),
        ).fetchone() == ("project", parent.id)
        assert request(db, "GET", f"/{child}", viewer=viewer).status_code == 200
        # Parent-read permission does not replace child-BU create permission.
        assert (
            request(
                db,
                "POST",
                json=body(parent_bu, parent_id=parent.id).model_dump(mode="json"),
                viewer=viewer,
            ).status_code
            == 404
        )
        identity.revoke_permission(read.id, "project.read")
        assert request(db, "POST", json=payload, viewer=viewer).status_code == 403


def test_migration_preserves_existing_three_level_tree_up_down(project_db):
    db = project_db
    migration = load_migration(
        ROOT / "migrations/20260826_0018_project_hierarchy.py", "hierarchy-roundtrip"
    )
    migration.downgrade(db.connection)
    bu = unit(db)
    root = uuid4()
    ids = [root, uuid4(), uuid4()]
    for i, id in enumerate(ids):
        db.connection.execute(
            """INSERT INTO project(id,org_id,bu_id,parent_id,number,name,owner_id,
            department_code,ledger_account_code,currency,created_by)
            VALUES (%s,%s,%s,%s,%s,'Preserved',%s,'D','L','USD',%s)""",
            (id, db.org_a, bu.id, ids[i - 1] if i else None, str(i), db.actor_id, db.actor_id),
        )
    before = db.connection.execute("SELECT * FROM project ORDER BY number").fetchall()
    migration.upgrade(db.connection)
    assert db.connection.execute(
        "SELECT depth,root_id FROM project ORDER BY number"
    ).fetchall() == [(1, root), (2, root), (3, root)]
    migration.downgrade(db.connection)
    assert db.connection.execute("SELECT * FROM project ORDER BY number").fetchall() == before
    migration.upgrade(db.connection)


def test_parent_read_is_required_in_same_bu(project_db):
    db = project_db
    bu = unit(db)
    parent = service(db).create(body(bu))
    viewer = insert_identity(db, "create-only")
    grant_permissions(db, viewer, "project.create")
    assert (
        request(
            db, "POST", json=body(bu, parent_id=parent.id).model_dump(mode="json"), viewer=viewer
        ).status_code
        == 403
    )


def test_trigger_depth_violation_is_mapped_to_problem(project_db):
    db = project_db
    bu, _, left, _ = planted_tree(db)
    svc = service(db)
    with patch.object(svc, "depth_of", return_value=4):
        with pytest.raises(ProblemError) as error:
            svc.create(body(bu, parent_id=left[-1]))
    assert error.value.code.value == "validation-failed"
    assert error.value.checks["problem"] == "project_depth_exceeded"
