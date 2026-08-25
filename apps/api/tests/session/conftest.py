from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

import psycopg
import pytest

from flo.kernel.identity import IdentityId
from flo.kernel.session.store import SessionConnection

ROOT = Path(__file__).resolve().parents[4]
IDENTITY_MIGRATION = ROOT / "migrations" / "20260824_0002_identity.py"
SESSION_MIGRATION = ROOT / "migrations" / "20260825_0004_session.py"
MFA_MIGRATION = ROOT / "migrations" / "20260825_0011_mfa.py"


def load_migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@dataclass(frozen=True, slots=True)
class SessionDatabase:
    connection: psycopg.Connection[tuple[object, ...]]
    session_connection: SessionConnection
    identity_id: IdentityId
    migration: ModuleType


@pytest.fixture
def session_database() -> Iterator[SessionDatabase]:
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

    identity_migration = load_migration(IDENTITY_MIGRATION, "session_test_identity")
    session_migration = load_migration(SESSION_MIGRATION, "session_test_migration")
    mfa_migration = load_migration(MFA_MIGRATION, "session_test_mfa")
    connection.execute("DROP TABLE IF EXISTS mfa_security_event")
    connection.execute("DROP FUNCTION IF EXISTS reject_mfa_security_event_mutation()")
    connection.execute("DROP TABLE IF EXISTS mfa_totp_consumption")
    connection.execute("DROP TABLE IF EXISTS mfa_recovery_code")
    connection.execute("DROP TABLE IF EXISTS mfa_factor")
    connection.execute("DROP TABLE IF EXISTS session_security_event")
    connection.execute("DROP TABLE IF EXISTS auth_session")
    connection.execute("DROP FUNCTION IF EXISTS reject_session_security_event_mutation()")
    connection.execute("DROP TABLE IF EXISTS identity")
    identity_migration.upgrade(connection)
    session_migration.upgrade(connection)
    mfa_migration.upgrade(connection)
    identity_id = IdentityId(uuid4())
    connection.execute(
        "INSERT INTO identity (id, email, password_hash) VALUES (%s, %s, %s)",
        (identity_id, "session@example.test", "$argon2id$session-test-placeholder"),
    )
    try:
        yield SessionDatabase(
            connection,
            cast(SessionConnection, connection),
            identity_id,
            mfa_migration,
        )
    finally:
        connection.execute("DROP TABLE IF EXISTS mfa_security_event")
        connection.execute("DROP FUNCTION IF EXISTS reject_mfa_security_event_mutation()")
        connection.execute("DROP TABLE IF EXISTS mfa_totp_consumption")
        connection.execute("DROP TABLE IF EXISTS mfa_recovery_code")
        connection.execute("DROP TABLE IF EXISTS mfa_factor")
        connection.execute("DROP TABLE IF EXISTS session_security_event")
        connection.execute("DROP TABLE IF EXISTS auth_session")
        connection.execute("DROP FUNCTION IF EXISTS reject_session_security_event_mutation()")
        connection.execute("DROP TABLE IF EXISTS identity")
        connection.close()
