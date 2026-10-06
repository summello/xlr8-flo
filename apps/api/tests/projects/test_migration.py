from uuid import uuid4

import pytest
from psycopg import sql
from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    InsufficientPrivilege,
    UniqueViolation,
)

from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.guards import unprotected_tenant_tables
from flo.kernel.tenancy.rls import tenant_transaction
from tests.authz.conftest import ROOT, load_migration
from tests.projects.conftest import body, service, unit


def test_up_down_preserves_existing_data(org_database):
    db = org_database
    master = load_migration(ROOT / "migrations/20260826_0015_master_records.py", "master-preserve")
    migration = load_migration(ROOT / "migrations/20260826_0017_projects.py", "project-preserve")
    bu = unit(db)
    master.upgrade(db.connection)
    tables = [
        "identity",
        "organization",
        "org_unit",
        "master_record",
        "permission",
        "role",
        "role_permission",
        "user_role",
        "authorization_scope",
        "audit_log",
    ]
    before = {t: db.connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    try:
        migration.upgrade(db.connection)
        try:
            assert migration.down_revision == "20260826_0016"
            db.connection.execute(
                """INSERT INTO project
            (id, org_id, bu_id, number, name, owner_id, department_code, ledger_account_code,
             currency, created_by) VALUES (%s,%s,%s,'P','Project',%s,'D','L','USD',%s)""",
                (uuid4(), db.org_a, bu.id, db.actor_id, db.actor_id),
            )
            assert unprotected_tenant_tables(migration.UPGRADE_SQL) == []
            for table in ["numbering_format", "numbering_counter", "project"]:
                planted = migration.UPGRADE_SQL.replace(
                    f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;", ""
                )
                assert table in unprotected_tenant_tables(planted)
        finally:
            migration.downgrade(db.connection)
        for table in tables:
            assert (
                db.connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                == before[table]
            )
    finally:
        master.downgrade(db.connection)


@pytest.mark.parametrize(
    "violation",
    [
        "number",
        "status",
        "dates",
        "bu",
        "parent",
        "org",
        "format_width",
        "format_type",
        "format_unique",
        "counter_unique",
    ],
)
def test_database_constraints_reject_real_violations(project_db, violation):
    db = project_db
    bu = unit(db)
    existing = service(db).create(body(bu))
    foreign_bu = unit(db, "F", db.org_b)
    params = (uuid4(), db.org_a, bu.id, existing.number, db.actor_id, db.actor_id)
    if violation in ["format_width", "format_type", "format_unique"]:
        query = """INSERT INTO numbering_format
        (id, org_id, scope_id, doc_type, width, effective_from)
        VALUES (%s,%s,%s,%s,%s,'2026-01-01')"""
        params = (
            uuid4(),
            db.org_a,
            bu.id,
            "bad" if violation == "format_type" else "project",
            0 if violation == "format_width" else 4,
        )
        if violation == "format_unique":
            db.connection.execute(query, params)
            params = (uuid4(), *params[1:])
        expected = UniqueViolation if violation == "format_unique" else CheckViolation
    elif violation == "counter_unique":
        query = "INSERT INTO numbering_counter(org_id,scope_id,doc_type,year) VALUES (%s,%s,%s,%s)"
        from datetime import UTC, datetime

        params = (db.org_a, bu.id, "project", datetime.now(UTC).year)
        expected = UniqueViolation
    else:
        status = "invalid" if violation == "status" else "draft"
        extra = ", '2026-02-01', '2026-01-01'" if violation == "dates" else ", NULL, NULL"
        parent = "%s" if violation == "parent" else "NULL"
        query = f"""INSERT INTO project(id,org_id,bu_id,number,owner_id,created_by,
        name,department_code,ledger_account_code,currency,status,planned_start,planned_end,parent_id)
        VALUES (%s,%s,%s,%s,%s,%s,'P','D','L','USD','{status}'{extra},{parent})"""
        if violation != "number":
            params = (
                uuid4(),
                uuid4() if violation == "org" else db.org_a,
                foreign_bu.id if violation == "bu" else bu.id,
                "OTHER",
                db.actor_id,
                db.actor_id,
            )
        if violation == "parent":
            params = (*params, uuid4())
        expected = (
            UniqueViolation
            if violation == "number"
            else CheckViolation
            if violation in ["status", "dates"]
            else ForeignKeyViolation
        )
    with pytest.raises(expected):
        db.connection.execute(query, params)
    if violation == "number":
        other = service(db).create(body(unit(db, "B")))
        assert other.number == existing.number


def test_rls_rejects_unscoped_foreign_access(project_db):
    db = project_db
    a, b = unit(db, "A"), unit(db, "B", db.org_b)
    own = service(db).create(body(a))
    service(db, db.org_b).create(body(b))
    for org, bu in [(db.org_a, a), (db.org_b, b)]:
        db.connection.execute(
            """INSERT INTO numbering_format
        (id,org_id,scope_id,doc_type,effective_from) VALUES (%s,%s,%s,'project','2000-01-01')""",
            (uuid4(), org, bu.id),
        )
    role = "projects_rls_" + uuid4().hex
    db.connection.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        db.connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
        )
        db.connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON project, "
                "numbering_counter, numbering_format TO {}"
            ).format(sql.Identifier(role))
        )
        db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(db.connection, Scope(db.org_a)):
            assert db.connection.execute("SELECT id FROM project").fetchall() == [(own.id,)]
            for table in ["numbering_format", "numbering_counter"]:
                assert db.connection.execute(f"SELECT DISTINCT org_id FROM {table}").fetchall() == [
                    (db.org_a,)
                ]
        for statement, params in [
            (
                "INSERT INTO numbering_counter(org_id,scope_id,doc_type,year) VALUES (%s,%s,%s,0)",
                (db.org_b, b.id, "po"),
            ),
            (
                "INSERT INTO numbering_format(id,org_id,scope_id,doc_type,effective_from) "
                "VALUES (%s,%s,%s,'po','2000-01-01')",
                (uuid4(), db.org_b, b.id),
            ),
            (
                "INSERT INTO project(id,org_id,bu_id,number,name,owner_id,department_code,"
                "ledger_account_code,currency,created_by) "
                "VALUES (%s,%s,%s,'BAD','BAD',%s,'D','L','USD',%s)",
                (uuid4(), db.org_b, b.id, db.actor_id, db.actor_id),
            ),
        ]:
            with pytest.raises(InsufficientPrivilege):
                with tenant_transaction(db.connection, Scope(db.org_a)):
                    db.connection.execute(statement, params)
    finally:
        db.connection.execute("RESET ROLE")
        db.connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        db.connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
