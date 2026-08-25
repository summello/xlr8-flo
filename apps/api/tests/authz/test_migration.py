from __future__ import annotations

from typing import cast

from flo.modules.identity.models import PERMISSION_CODES

from .conftest import AuthorizationDatabase


def test_migration_uses_assigned_chain_and_seeds_every_permission_by_name(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    assert database.migration.revision == "20260825_0008"
    assert database.migration.down_revision == "20260825_0007"
    stored = {
        cast(str, row[0])
        for row in database.connection.execute("SELECT code FROM permission").fetchall()
    }
    assert stored == {str(permission) for permission in PERMISSION_CODES}


def test_migration_has_rls_on_every_tenant_table(
    authorization_database: AuthorizationDatabase,
) -> None:
    rows = authorization_database.connection.execute(
        """
        SELECT class.relname, class.relrowsecurity, class.relforcerowsecurity
          FROM pg_class AS class
          JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
         WHERE namespace.nspname = 'public'
           AND class.relname IN (
               'role', 'role_permission', 'authorization_scope', 'user_role'
           )
         ORDER BY class.relname
        """
    ).fetchall()
    assert rows == [
        ("authorization_scope", True, True),
        ("role", True, True),
        ("role_permission", True, True),
        ("user_role", True, True),
    ]


def test_migration_downgrade_preserves_identity_and_audit_data(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    identity_before = database.connection.execute("SELECT count(*) FROM identity").fetchone()
    database.migration.downgrade(database.connection)
    assert (
        database.connection.execute("SELECT count(*) FROM identity").fetchone()
        == identity_before
    )
    assert database.connection.execute("SELECT to_regclass('public.audit_log')").fetchone() == (
        "audit_log",
    )
    for table in ("permission", "role", "role_permission", "authorization_scope", "user_role"):
        assert database.connection.execute(
            "SELECT to_regclass(%s)", (f"public.{table}",)
        ).fetchone() == (None,)
