from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest

from flo.kernel.identity import IdentityId
from flo.modules.identity.resolver import AuthorizationConnection
from flo.modules.identity.service import IdentityAuthorizationConnection

ROOT = Path(__file__).resolve().parents[4]
IDENTITY_MIGRATION = ROOT / "migrations" / "20260824_0002_identity.py"
AUDIT_MIGRATION = ROOT / "migrations" / "20260825_0005_audit_log.py"
RBAC_MIGRATION = ROOT / "migrations" / "20260825_0008_rbac.py"
SESSION_MIGRATION = ROOT / "migrations" / "20260825_0004_session.py"
MFA_MIGRATION = ROOT / "migrations" / "20260825_0011_mfa.py"
ACCESS_MIGRATION = ROOT / "migrations" / "20260825_0012_effective_access.py"


def load_migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@dataclass(frozen=True, slots=True)
class AuthorizationDatabase:
    connection: psycopg.Connection[tuple[object, ...]]
    authorization_connection: AuthorizationConnection
    service_connection: IdentityAuthorizationConnection
    migration: ModuleType
    access_migration: ModuleType
    org_a: UUID
    org_b: UUID
    actor_id: IdentityId


def drop_objects(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    connection.execute("RESET ROLE")
    connection.execute("DROP TABLE IF EXISTS mfa_security_event")
    connection.execute("DROP FUNCTION IF EXISTS reject_mfa_security_event_mutation()")
    connection.execute("DROP TABLE IF EXISTS mfa_totp_consumption")
    connection.execute("DROP TABLE IF EXISTS mfa_recovery_code")
    connection.execute("DROP TABLE IF EXISTS mfa_factor")
    partitions = connection.execute(
        "SELECT tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname = 'public' AND tablename LIKE 'audit_log_%'"
    ).fetchall()
    for partition in partitions:
        connection.execute(f'DROP TABLE IF EXISTS "{partition[0]}"')
    connection.execute("DROP TABLE IF EXISTS user_role")
    connection.execute("DROP TABLE IF EXISTS authorization_scope")
    connection.execute("DROP TABLE IF EXISTS role_permission")
    connection.execute("DROP TABLE IF EXISTS role")
    connection.execute("DROP TABLE IF EXISTS permission")
    connection.execute("DROP TABLE IF EXISTS audit_log")
    connection.execute("DROP FUNCTION IF EXISTS raise_append_only()")
    connection.execute("DROP TABLE IF EXISTS session_security_event")
    connection.execute("DROP TABLE IF EXISTS auth_session")
    connection.execute("DROP FUNCTION IF EXISTS reject_session_security_event_mutation()")
    # A test builds this to prove attribution survives deactivation. It lives in the test body,
    # so a failing assertion used to leak it — and because it carries a foreign key to identity,
    # the leak broke the NEXT test's setup rather than its own. Teardown owns it now.
    connection.execute("DROP TABLE IF EXISTS historical_record")
    connection.execute("DROP TABLE IF EXISTS identity")


@pytest.fixture
def authorization_database() -> Iterator[AuthorizationDatabase]:
    configured_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    database_url = configured_url or "postgresql://flo:flo-local@127.0.0.1:5432/flo_test"
    database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        connection = psycopg.connect(database_url, autocommit=True)
    except psycopg.Error as exc:
        if configured_url:
            pytest.fail(f"configured Postgres is unavailable: {type(exc).__name__}")
        pytest.skip("local Postgres is unavailable; run the repository stack")
        raise

    identity_migration = load_migration(IDENTITY_MIGRATION, "authz_test_identity")
    audit_migration = load_migration(AUDIT_MIGRATION, "authz_test_audit")
    rbac_migration = load_migration(RBAC_MIGRATION, "authz_test_rbac")
    session_migration = load_migration(SESSION_MIGRATION, "authz_test_session")
    mfa_migration = load_migration(MFA_MIGRATION, "authz_test_mfa")
    access_migration = load_migration(ACCESS_MIGRATION, "authz_test_access")
    drop_objects(connection)
    identity_migration.upgrade(connection)
    session_migration.upgrade(connection)
    audit_migration.upgrade(connection)
    rbac_migration.upgrade(connection)
    mfa_migration.upgrade(connection)
    access_migration.upgrade(connection)
    actor_id = IdentityId(uuid4())
    connection.execute(
        "INSERT INTO identity (id, email, password_hash) VALUES (%s, %s, %s)",
        (actor_id, "authz-actor@example.test", "$argon2id$authz-test-placeholder"),
    )
    database = AuthorizationDatabase(
        connection=connection,
        authorization_connection=cast(AuthorizationConnection, connection),
        service_connection=cast(IdentityAuthorizationConnection, connection),
        migration=rbac_migration,
        access_migration=access_migration,
        org_a=uuid4(),
        org_b=uuid4(),
        actor_id=actor_id,
    )
    try:
        yield database
    finally:
        drop_objects(connection)
        connection.close()
