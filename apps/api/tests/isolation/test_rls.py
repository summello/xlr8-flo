from __future__ import annotations

import inspect
from types import ModuleType

from psycopg import sql

from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import set_local_org, tenant_transaction

from .conftest import TenantDatabase, assume_application_role


def _visible_names(database: TenantDatabase, scope: Scope) -> list[str]:
    with tenant_transaction(database.connection, scope):
        rows = database.connection.execute(
            sql.SQL("SELECT name FROM public.{} ORDER BY name").format(
                sql.Identifier(database.table)
            )
        ).fetchall()
    return [str(row["name"]) for row in rows]


def test_rls_alone_hides_foreign_rows(
    tenant_database: TenantDatabase, rls_migration: ModuleType
) -> None:
    rls_migration.upgrade(tenant_database.connection)
    assume_application_role(tenant_database)

    with tenant_transaction(tenant_database.connection, Scope(tenant_database.org_a)):
        row = tenant_database.connection.execute(
            sql.SQL("SELECT id FROM public.{} WHERE id = %s").format(
                sql.Identifier(tenant_database.table)
            ),
            (tenant_database.record_b,),
        ).fetchone()

    assert row is None
    assert _visible_names(tenant_database, Scope(tenant_database.org_a)) == ["Tenant A record"]


def test_set_local_does_not_bleed_when_one_connection_is_reused(
    tenant_database: TenantDatabase, rls_migration: ModuleType
) -> None:
    rls_migration.upgrade(tenant_database.connection)
    assume_application_role(tenant_database)

    assert _visible_names(tenant_database, Scope(tenant_database.org_a)) == ["Tenant A record"]
    with tenant_database.connection.transaction():
        previous = tenant_database.connection.execute(
            "SELECT current_setting('app.org_id', true) AS org_id"
        ).fetchone()
        assert previous is not None
        assert previous["org_id"] != str(tenant_database.org_a)
    assert _visible_names(tenant_database, Scope(tenant_database.org_b)) == ["Tenant B record"]

    source = inspect.getsource(set_local_org)
    assert "SET LOCAL app.org_id" in source
    assert 'sql.SQL("SET app.org_id' not in source


def test_migration_forces_rls_on_every_business_table_and_is_reversible(
    tenant_database: TenantDatabase, rls_migration: ModuleType
) -> None:
    rls_migration.upgrade(tenant_database.connection)
    protected = tenant_database.connection.execute(
        """
        SELECT tables.relrowsecurity, tables.relforcerowsecurity,
               policies.polname, pg_get_expr(policies.polqual, policies.polrelid) AS using
        FROM pg_catalog.pg_class AS tables
        JOIN pg_catalog.pg_namespace AS namespaces ON namespaces.oid = tables.relnamespace
        LEFT JOIN pg_catalog.pg_policy AS policies ON policies.polrelid = tables.oid
        WHERE namespaces.nspname = 'public' AND tables.relname = %s
        """,
        (tenant_database.table,),
    ).fetchone()

    assert protected is not None
    assert protected["relrowsecurity"] is True
    assert protected["relforcerowsecurity"] is True
    assert protected["polname"] == "tenant_isolation"
    assert "current_setting('app.org_id'::text)" in str(protected["using"])

    rls_migration.downgrade(tenant_database.connection)
    unprotected = tenant_database.connection.execute(
        sql.SQL(
            """
        SELECT tables.relrowsecurity, tables.relforcerowsecurity,
               (SELECT count(*) FROM pg_catalog.pg_policy WHERE polrelid = tables.oid) AS policies,
               (SELECT count(*) FROM public.{}) AS records
        FROM pg_catalog.pg_class AS tables
        JOIN pg_catalog.pg_namespace AS namespaces ON namespaces.oid = tables.relnamespace
        WHERE namespaces.nspname = 'public' AND tables.relname = %s
        """
        ).format(sql.Identifier(tenant_database.table)),
        (tenant_database.table,),
    ).fetchone()

    assert unprotected == {
        "relrowsecurity": False,
        "relforcerowsecurity": False,
        "policies": 0,
        "records": 2,
    }
