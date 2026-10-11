from __future__ import annotations

from uuid import uuid4

import pytest
from psycopg import sql
from psycopg.errors import InsufficientPrivilege

from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.guards import unprotected_tenant_tables
from flo.kernel.tenancy.rls import tenant_transaction
from tests.authz.conftest import ROOT, AuthorizationDatabase, load_migration
from tests.authz.test_resolver import service_for

from .test_units import create


def test_up_down_preserves_backfill_sources(authorization_database: AuthorizationDatabase) -> None:
    db = authorization_database
    with service_for(db, Scope(db.org_a), "migration-source") as identity:
        role = identity.create_role("source", "Source")
        from flo.modules.identity.models import AuthorizationTarget, ScopeType

        identity.grant_role(db.actor_id, role.id, AuthorizationTarget.organization(db.org_a))
    with service_for(db, Scope(db.org_b), "scope-only-source") as identity:
        identity.register_scope(ScopeType.BU, uuid4(), ScopeType.ORG, db.org_b)
    before = {
        table: db.connection.execute(f"SELECT * FROM {table}").fetchall()
        for table in ("identity", "role", "user_role", "authorization_scope", "audit_log")
    }
    path = ROOT / "migrations/20260826_0013_org_units.py"
    migration = load_migration(path, "org-preservation")
    migration.upgrade(db.connection)
    try:
        assert migration.down_revision == "20260825_0012"
        assert db.connection.execute(
            "SELECT id, name, base_currency FROM organization ORDER BY id"
        ).fetchall() == [(org, "Organization", None) for org in sorted((db.org_a, db.org_b))]
        assert db.connection.execute(
            "SELECT code FROM permission WHERE code LIKE 'org.%' ORDER BY code"
        ).fetchall() == [("org.setting.manage",), ("org.unit.manage",), ("org.unit.read",)]
        assert unprotected_tenant_tables(path.read_text()) == []
        for table in ("organization", "org_unit", "org_setting"):
            planted = path.read_text().replace(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;", "")
            assert table in unprotected_tenant_tables(planted)
    finally:
        migration.downgrade(db.connection)
    for table, rows in before.items():
        assert db.connection.execute(f"SELECT * FROM {table}").fetchall() == rows


def test_rls_blocks_unscoped_foreign_reads_and_writes(org_database: AuthorizationDatabase) -> None:
    db = org_database
    own = create(db, "OWN")
    foreign = create(db, "FOREIGN", org=db.org_b)
    from flo.modules.org.schemas import SettingPut

    from .conftest import service

    service(db).set_setting("funding_mode", SettingPut(unit_id=own.id, value="roll_up"))
    service(db, db.org_b).set_setting(
        "funding_mode", SettingPut(unit_id=foreign.id, value="roll_down")
    )
    role = "org_rls_" + uuid4().hex
    db.connection.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        db.connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
        )
        db.connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON organization, org_unit, org_setting TO {}"
            ).format(sql.Identifier(role))
        )
        db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(db.connection, Scope(db.org_a)):
            # Deliberately bypass the repository and omit org predicates.
            assert db.connection.execute("SELECT id FROM organization").fetchall() == [(db.org_a,)]
            assert db.connection.execute("SELECT id FROM org_unit").fetchall() == [(own.id,)]
            settings = db.connection.execute("SELECT value FROM org_setting").fetchall()
            assert settings == [("roll_up",)]
        for statement, params in [
            ("INSERT INTO organization(id, name) VALUES (%s, 'Foreign')", (uuid4(),)),
            (
                "INSERT INTO org_unit(id, org_id, code, name, kind) "
                "VALUES (%s, %s, 'BAD', 'Bad', 'bu')",
                (uuid4(), db.org_b),
            ),
            (
                "INSERT INTO org_setting(id, org_id, key, value, updated_by) "
                "VALUES (%s, %s, 'funding_mode', 'true', %s)",
                (uuid4(), db.org_b, db.actor_id),
            ),
        ]:
            with pytest.raises(InsufficientPrivilege):
                with tenant_transaction(db.connection, Scope(db.org_a)):
                    db.connection.execute(statement, params)
    finally:
        db.connection.execute("RESET ROLE")
        db.connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        db.connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.parametrize(
    "violation", ["kind", "organization_fk", "setting_unit_fk", "setting_unique"]
)
def test_database_constraints_reject_planted_violations(
    org_database: AuthorizationDatabase, violation: str
) -> None:
    from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

    db = org_database
    foreign = create(db, "F", org=db.org_b)
    if violation == "kind":
        statement = (
            "INSERT INTO org_unit(id, org_id, code, name, kind) VALUES (%s,%s,'X','X','bad')"
        )
        params = (uuid4(), db.org_a)
        expected = CheckViolation
    elif violation == "organization_fk":
        statement = "INSERT INTO org_unit(id, org_id, code, name, kind) VALUES (%s,%s,'X','X','bu')"
        params = (uuid4(), uuid4())
        expected = ForeignKeyViolation
    elif violation == "setting_unit_fk":
        statement = (
            "INSERT INTO org_setting(id, org_id, unit_id, key, value, updated_by) "
            "VALUES (%s,%s,%s,'funding_mode','true',%s)"
        )
        params = (uuid4(), db.org_a, foreign.id, db.actor_id)
        expected = ForeignKeyViolation
    else:
        statement = (
            "INSERT INTO org_setting(id, org_id, key, value, updated_by) "
            "VALUES (%s,%s,'funding_mode','true',%s)"
        )
        params = (uuid4(), db.org_a, db.actor_id)
        db.connection.execute(statement, params)
        params = (uuid4(), db.org_a, db.actor_id)
        expected = UniqueViolation
    with pytest.raises(expected):
        db.connection.execute(statement, params)
