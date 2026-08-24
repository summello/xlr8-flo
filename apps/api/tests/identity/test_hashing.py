from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

import psycopg
import pytest
from argon2 import Type, extract_parameters
from psycopg import sql

from flo.kernel.config import Settings
from flo.kernel.identity import (
    IdentityConnection,
    IdentityId,
    IdentityNotFoundError,
    PasswordPolicyError,
    build_local_identity_provider,
)

from .conftest import FakeIdentityConnection, fast_settings

ROOT = Path(__file__).resolve().parents[4]
MIGRATION_PATH = ROOT / "migrations" / "20260824_0002_identity.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("identity_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_stored_hash_is_salted_argon2id_with_configured_parameters(
    identity_connection: FakeIdentityConnection,
) -> None:
    settings = Settings()
    provider = build_local_identity_provider(identity_connection, settings)
    provider.create_identity("person@example.test", "a unique lowercase passphrase")
    encoded = identity_connection.identities["person@example.test"][1]
    parameters = extract_parameters(encoded)

    assert encoded.startswith("$argon2id$v=19$")
    assert parameters.type is Type.ID
    assert parameters.time_cost == 3
    assert parameters.memory_cost == 64 * 1024
    assert parameters.parallelism == 4
    assert "a unique lowercase passphrase" not in encoded


def test_each_identity_receives_a_distinct_salt(
    identity_connection: FakeIdentityConnection,
) -> None:
    provider = build_local_identity_provider(identity_connection, fast_settings())
    provider.create_identity("one@example.test", "same safe passphrase for both")
    provider.create_identity("two@example.test", "same safe passphrase for both")

    assert identity_connection.identities["one@example.test"][1] != identity_connection.identities[
        "two@example.test"
    ][1]


def test_higher_configured_cost_rehashes_on_successful_authentication(
    identity_connection: FakeIdentityConnection,
) -> None:
    old_provider = build_local_identity_provider(identity_connection, fast_settings(time_cost=1))
    identity_id = old_provider.create_identity(
        "rehash@example.test", "rehash only after successful login"
    )
    old_hash = identity_connection.identities["rehash@example.test"][1]

    new_provider = build_local_identity_provider(identity_connection, fast_settings(time_cost=2))
    result = new_provider.authenticate(
        "rehash@example.test", "rehash only after successful login"
    )
    new_hash = identity_connection.identities["rehash@example.test"][1]

    assert result.identity_id == identity_id
    assert result.authenticated
    assert new_hash != old_hash
    assert extract_parameters(new_hash).time_cost == 2


def test_failed_authentication_does_not_rehash(
    identity_connection: FakeIdentityConnection,
) -> None:
    old_provider = build_local_identity_provider(identity_connection, fast_settings(time_cost=1))
    old_provider.create_identity("no-rehash@example.test", "the original safe passphrase")
    old_hash = identity_connection.identities["no-rehash@example.test"][1]

    new_provider = build_local_identity_provider(identity_connection, fast_settings(time_cost=2))
    assert not new_provider.authenticate(
        "no-rehash@example.test", "a different safe passphrase"
    ).authenticated

    assert identity_connection.identities["no-rehash@example.test"][1] == old_hash


def test_change_password_uses_policy_and_replaces_the_hash(
    identity_connection: FakeIdentityConnection,
) -> None:
    provider = build_local_identity_provider(identity_connection, fast_settings())
    identity_id = provider.create_identity("change@example.test", "the original safe passphrase")

    with pytest.raises(PasswordPolicyError):
        provider.change_password(identity_id, "too short")
    provider.change_password(identity_id, "the replacement safe passphrase")

    assert not provider.authenticate(
        "change@example.test", "the original safe passphrase"
    ).authenticated
    assert provider.authenticate(
        "change@example.test", "the replacement safe passphrase"
    ).authenticated


def test_change_password_rejects_an_unknown_identity(
    identity_connection: FakeIdentityConnection,
) -> None:
    provider = build_local_identity_provider(identity_connection, fast_settings())

    with pytest.raises(IdentityNotFoundError):
        provider.change_password(
            IdentityId(uuid4()),
            "a valid replacement passphrase",
        )


def test_identity_migration_is_reversible_and_does_not_touch_seeded_data() -> None:
    configured_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    database_url = configured_url or "postgresql://flo:flo-local@127.0.0.1:5432/flo_test"
    database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        connection = psycopg.connect(database_url, autocommit=True)
    except psycopg.Error as exc:
        if configured_url:
            pytest.fail(f"configured Postgres is unavailable: {type(exc).__name__}")
        pytest.skip("local Postgres is unavailable; run the repository stack")

    migration = _load_migration()
    sentinel = f"identity_migration_sentinel_{uuid4().hex[:12]}"
    try:
        connection.execute(
            sql.SQL("CREATE TABLE {} (value text NOT NULL)").format(
                sql.Identifier(sentinel)
            )
        )
        connection.execute(
            sql.SQL("INSERT INTO {} (value) VALUES ('preserved')").format(
                sql.Identifier(sentinel)
            )
        )
        migration.upgrade(connection)
        provider = build_local_identity_provider(
            cast(IdentityConnection, connection), fast_settings()
        )
        identity_id = provider.create_identity(
            "migration@example.test", "a migration integration passphrase"
        )
        assert provider.authenticate(
            "migration@example.test", "a migration integration passphrase"
        ).identity_id == identity_id

        columns = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'identity' ORDER BY ordinal_position"
        ).fetchall()
        assert [row[0] for row in columns] == [
            "id",
            "email",
            "password_hash",
            "created_at",
            "updated_at",
        ]
        stored_hash = connection.execute(
            "SELECT password_hash FROM identity WHERE id = %s", (identity_id,)
        ).fetchone()
        assert stored_hash is not None
        assert str(stored_hash[0]).startswith("$argon2id$")

        migration.downgrade(connection)
        assert connection.execute("SELECT to_regclass('public.identity')").fetchone() == (None,)
        assert connection.execute(
            sql.SQL("SELECT value FROM {}").format(sql.Identifier(sentinel))
        ).fetchone() == ("preserved",)
    finally:
        connection.execute("DROP TABLE IF EXISTS identity")
        connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(sentinel)))
        connection.close()
