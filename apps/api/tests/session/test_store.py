from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from psycopg import sql
from psycopg.errors import CheckViolation, RaiseException

from flo.kernel.identity import IdentityId
from flo.kernel.session.store import (
    RequestDevice,
    RotationReason,
    SessionStore,
    coarse_user_agent,
    hash_session_token,
)

from .conftest import SessionDatabase


@dataclass
class MutableClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


START = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
DEVICE = RequestDevice("203.0.113.0/24", "Chrome on macOS")


def store(database: SessionDatabase, clock: MutableClock) -> SessionStore:
    return SessionStore(
        database.session_connection,
        idle_timeout=timedelta(hours=8),
        absolute_timeout=timedelta(hours=12),
        clock=clock,
    )


def test_token_is_256_bits_and_only_its_sha256_hash_is_stored(
    session_database: SessionDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fixed_token(byte_count: int) -> str:
        calls.append(byte_count)
        return "opaque-token-that-only-the-browser-may-receive"

    monkeypatch.setattr("flo.kernel.session.store.secrets.token_urlsafe", fixed_token)
    issued = store(session_database, MutableClock(START)).issue(
        session_database.identity_id, DEVICE
    )
    row = session_database.connection.execute(
        "SELECT token_hash FROM auth_session WHERE id = %s", (issued.session.id,)
    ).fetchone()

    assert calls == [32]
    assert row == (hash_session_token(issued.cookie_value()),)
    assert issued.cookie_value() not in str(row)
    assert issued.cookie_value() not in repr(issued)


def test_idle_timeout_refreshes_on_activity_but_absolute_timeout_never_moves(
    session_database: SessionDatabase,
) -> None:
    clock = MutableClock(START)
    session_store = store(session_database, clock)
    issued = session_store.issue(session_database.identity_id, DEVICE)

    clock.now = START + timedelta(hours=7)
    active = session_store.authenticate(issued.cookie_value(), DEVICE)
    assert active is not None
    assert active.last_seen_at == clock.now
    assert active.idle_expires_at == START + timedelta(hours=12)
    assert active.absolute_expires_at == START + timedelta(hours=12)

    clock.now = START + timedelta(hours=11, minutes=59)
    assert session_store.authenticate(issued.cookie_value(), DEVICE) is not None

    clock.now = START + timedelta(hours=12)
    assert session_store.authenticate(issued.cookie_value(), DEVICE) is None


def test_idle_timeout_expires_an_inactive_session(
    session_database: SessionDatabase,
) -> None:
    clock = MutableClock(START)
    session_store = store(session_database, clock)
    issued = session_store.issue(session_database.identity_id, DEVICE)

    clock.now = START + timedelta(hours=8)

    assert session_store.authenticate(issued.cookie_value(), DEVICE) is None


@pytest.mark.parametrize("reason", list(RotationReason))
def test_every_privilege_change_rotates_id_and_token_and_revokes_the_old_row(
    session_database: SessionDatabase,
    reason: RotationReason,
) -> None:
    clock = MutableClock(START)
    session_store = store(session_database, clock)
    original = session_store.issue(session_database.identity_id, DEVICE)
    clock.now += timedelta(minutes=1)

    replacement = session_store.rotate(
        original.session,
        session_database.identity_id,
        DEVICE,
        reason,
    )

    assert replacement.session.id != original.session.id
    assert replacement.cookie_value() != original.cookie_value()
    assert session_store.authenticate(original.cookie_value(), DEVICE) is None
    assert session_store.authenticate(replacement.cookie_value(), DEVICE) is not None


def test_revoked_token_reuse_writes_append_only_security_evidence_without_token(
    session_database: SessionDatabase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = MutableClock(START)
    session_store = store(session_database, clock)
    issued = session_store.issue(session_database.identity_id, DEVICE)
    assert session_store.revoke(issued.session.id, session_database.identity_id)

    caplog.set_level(logging.DEBUG)
    clock.now += timedelta(minutes=1)
    assert session_store.authenticate(issued.cookie_value(), DEVICE) is None
    event = session_database.connection.execute(
        """
        SELECT session_id, identity_id, event_type, occurred_at, ip_prefix, user_agent
          FROM session_security_event
        """
    ).fetchone()

    assert event is not None
    assert (*event[:4], str(event[4]), event[5]) == (
        issued.session.id,
        session_database.identity_id,
        "revoked_token_reuse",
        clock.now,
        "203.0.113.0/24",
        "Chrome on macOS",
    )
    assert issued.cookie_value() not in repr(event)
    assert issued.cookie_value() not in caplog.text

    event_id = session_database.connection.execute(
        "SELECT id FROM session_security_event"
    ).fetchone()
    assert event_id is not None
    with pytest.raises(RaiseException, match="append-only"):
        session_database.connection.execute(
            "UPDATE session_security_event SET user_agent = 'changed' WHERE id = %s",
            (event_id[0],),
        )
    with pytest.raises(RaiseException, match="append-only"):
        session_database.connection.execute(
            "DELETE FROM session_security_event WHERE id = %s", (event_id[0],)
        )


def test_revoke_others_leaves_exactly_current_and_cannot_revoke_another_identity(
    session_database: SessionDatabase,
) -> None:
    clock = MutableClock(START)
    session_store = store(session_database, clock)
    current = session_store.issue(session_database.identity_id, DEVICE)
    other = session_store.issue(session_database.identity_id, DEVICE)
    foreign_identity = IdentityId(uuid4())
    session_database.connection.execute(
        "INSERT INTO identity (id, email, password_hash) VALUES (%s, %s, %s)",
        (foreign_identity, "foreign@example.test", "$argon2id$session-test-placeholder"),
    )

    assert not session_store.revoke(other.session.id, foreign_identity)
    assert session_store.revoke_others(session_database.identity_id, current.session.id) == 1
    active = session_store.list_active(session_database.identity_id, current.session.id)

    assert active == [(current.session, True)]


def test_device_metadata_is_truncated_and_contains_no_version_or_precise_address() -> None:
    from starlette.requests import Request

    from flo.kernel.session.store import request_device

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (
                b"user-agent",
                b"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                b"AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
            )
        ],
        "client": ("203.0.113.42", 443),
    }

    device = request_device(Request(scope))

    assert device == RequestDevice("203.0.113.0/24", "Chrome on macOS")
    assert "203.0.113.42" not in repr(device)
    assert "140" not in device.user_agent
    assert coarse_user_agent("unrecognized precise device details") == (
        "Other browser on unknown platform"
    )


def test_mfa_migration_extends_sessions_and_is_reversible_with_seeded_data(
    session_database: SessionDatabase,
) -> None:
    connection = session_database.connection
    columns = connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'auth_session' "
        "ORDER BY ordinal_position"
    ).fetchall()
    event_columns = connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'session_security_event' "
        "ORDER BY ordinal_position"
    ).fetchall()
    constraints = connection.execute(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'public.auth_session'::regclass ORDER BY conname"
    ).fetchall()
    trigger = connection.execute(
        "SELECT tgname FROM pg_trigger "
        "WHERE tgrelid = 'public.session_security_event'::regclass AND NOT tgisinternal"
    ).fetchall()

    assert [row[0] for row in columns] == [
        "id",
        "identity_id",
        "token_hash",
        "created_at",
        "last_seen_at",
        "idle_timeout_seconds",
        "idle_expires_at",
        "absolute_expires_at",
        "revoked_at",
        "ip_prefix",
        "user_agent",
        "last_auth_at",
        "mfa_verified_at",
    ]
    assert [row[0] for row in event_columns] == [
        "id",
        "session_id",
        "identity_id",
        "event_type",
        "occurred_at",
        "ip_prefix",
        "user_agent",
    ]
    assert [row[0] for row in constraints] == [
        "auth_session_expiry_order",
        "auth_session_identity_id_fkey",
        "auth_session_idle_timeout_seconds_check",
        "auth_session_mfa_verification_order",
        "auth_session_pkey",
        "auth_session_recent_auth_order",
        "auth_session_revocation_order",
        "auth_session_token_hash_key",
        "auth_session_token_hash_sha256",
    ]
    assert trigger == [("session_security_event_append_only",)]
    persisted_session = store(session_database, MutableClock(START)).issue(
        session_database.identity_id, DEVICE
    )

    with pytest.raises(CheckViolation):
        connection.execute(
            """
            INSERT INTO auth_session
                (id, identity_id, token_hash, created_at, last_seen_at,
                 idle_timeout_seconds, idle_expires_at, absolute_expires_at, user_agent,
                 last_auth_at)
            VALUES (%s, %s, 'plaintext', %s, %s, 60, %s, %s, 'Other browser', %s)
            """,
            (
                uuid4(),
                session_database.identity_id,
                START,
                START,
                START + timedelta(minutes=1),
                START + timedelta(minutes=2),
                START,
            ),
        )

    sentinel = f"session_migration_sentinel_{uuid4().hex[:12]}"
    connection.execute(
        sql.SQL("CREATE TABLE {} (value text NOT NULL)").format(sql.Identifier(sentinel))
    )
    connection.execute(
        sql.SQL("INSERT INTO {} (value) VALUES ('preserved')").format(
            sql.Identifier(sentinel)
        )
    )
    try:
        session_database.migration.downgrade(connection)
        assert connection.execute("SELECT to_regclass('public.auth_session')").fetchone() == (
            "auth_session",
        )
        assert connection.execute(
            "SELECT to_regclass('public.mfa_factor')"
        ).fetchone() == (None,)
        assert connection.execute(
            sql.SQL("SELECT value FROM {}").format(sql.Identifier(sentinel))
        ).fetchone() == ("preserved",)
        assert connection.execute(
            "SELECT email FROM identity WHERE id = %s", (session_database.identity_id,)
        ).fetchone() == ("session@example.test",)
        assert connection.execute(
            "SELECT id FROM auth_session WHERE identity_id = %s",
            (session_database.identity_id,),
        ).fetchall() == [(persisted_session.session.id,)]
    finally:
        connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(sentinel)))
