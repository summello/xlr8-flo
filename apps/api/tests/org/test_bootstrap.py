from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.errors import CheckViolation, UniqueViolation

from flo.kernel.config import Settings
from flo.kernel.identity.hashing import build_argon2_hasher
from flo.kernel.identity.mfa import MfaAccessRequirement, MfaService, SecretCipher
from flo.kernel.logging import correlation_context
from flo.kernel.migrate import apply_migrations, discover_migrations
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.resolver import AuthorizationResolver
from flo.modules.identity.service import (
    AuthorizationTarget,
    IdentityAuthorizationService,
    role_code,
)
from flo.modules.org.bootstrap import bootstrap, main
from flo.modules.org.schemas import OrgUnitCreate
from flo.modules.org.service import OrgService
from tests.kernel.test_migrate import empty_database as empty_database

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def bootstrap_db(empty_database):
    apply_migrations(discover_migrations(ROOT / "migrations"), empty_database)
    with psycopg.connect(empty_database, autocommit=True) as conn:
        yield conn


def connection_url(conn):
    return make_conninfo(conn.info.dsn, password=conn.info.password)


def settings(conn):
    return Settings(
        database_url=connection_url(conn),
        identity_argon2_time_cost=1,
        identity_argon2_memory_cost_kib=8192,
        identity_argon2_parallelism=1,
    )


def create(conn, code="FIRST", email="admin@example.test", **kwargs):
    # Disposable test credential, never an operator secret.
    return asyncio.run(
        bootstrap(
            conn,
            org_name="First tenant",
            org_code=code,
            admin_email=email,
            password="disposable bootstrap test passphrase",
            settings=settings(conn),
            **kwargs,
        )
    )


def admin_id(conn, email="admin@example.test"):
    return conn.execute("SELECT id FROM identity WHERE email = %s", (email,)).fetchone()[0]


def mfa(conn):
    return MfaService(
        conn,
        SecretCipher(bytes(32)),
        build_argon2_hasher(settings(conn)),
        argon2_max_concurrency=2,
        max_failed_attempts=5,
        failure_window=timedelta(minutes=5),
        lock_duration=timedelta(minutes=5),
    )


def test_bootstrap_permissions_mfa_defaults_and_noop(bootstrap_db):
    conn = bootstrap_db
    result = create(conn, code="first")
    assert result.created
    identity = admin_id(conn)
    with tenant_transaction(conn, Scope(result.org_id)), correlation_context("bootstrap-test"):
        resolver = AuthorizationResolver(conn, Scope(result.org_id))
        for (permission,) in conn.execute("SELECT code FROM permission").fetchall():
            assert resolver.check(
                identity, permission, AuthorizationTarget.organization(result.org_id)
            ).allowed
        unit = OrgService(conn, Scope(result.org_id), identity).create_unit(
            OrgUnitCreate(code="BU", name="Business unit", kind="bu")
        )
        from flo.modules.org.settings import SETTING_DEFAULTS

        for key, default in SETTING_DEFAULTS.items():
            effective = OrgService(conn, Scope(result.org_id)).effective_setting(unit.id, key)
            assert effective.value == default
            assert effective.source.scope == "default"
        assert conn.execute("SELECT count(*) FROM org_setting").fetchone()[0] == 0
        audit_before = conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]
        event = conn.execute(
            "SELECT actor_kind FROM audit_log WHERE action = 'org.bootstrap'"
        ).fetchall()
        assert event == [("system",)]
    assert (
        conn.execute(
            "SELECT privileged_role_grants FROM identity WHERE id = %s", (identity,)
        ).fetchone()[0]
        > 0
    )
    assert mfa(conn).access_requirement(identity) is MfaAccessRequirement.ENROLL
    assert not create(conn, code="FIRST").created
    with tenant_transaction(conn, Scope(result.org_id)):
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == audit_before
    assert conn.execute("SELECT count(*) FROM organization_code").fetchone()[0] == 1


def test_cli_exits_and_secret_absent(bootstrap_db, monkeypatch, capsys):
    conn = bootstrap_db
    monkeypatch.setenv("DATABASE_URL", connection_url(conn))
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_MEMORY_COST_KIB", "8192")
    args = ["--org-name", "First", "--org-code", "FIRST", "--admin-email", "admin@example.test"]
    password = "disposable bootstrap test passphrase"
    monkeypatch.setenv("FLO_BOOTSTRAP_PASSWORD", password)
    assert main(args) == 0
    assert main(args) == 0
    output = capsys.readouterr()
    assert "already bootstrapped" in output.out
    assert password not in output.out + output.err
    assert main(args[:-1] + ["other@example.test"]) == 3
    monkeypatch.setenv("FLO_BOOTSTRAP_PASSWORD", "short!")
    assert (
        main(["--org-name", "Weak", "--org-code", "WEAK", "--admin-email", "weak@example.test"])
        == 2
    )
    assert (
        conn.execute("SELECT count(*) FROM organization_code WHERE code = 'WEAK'").fetchone()[0]
        == 0
    )
    assert (
        conn.execute("SELECT count(*) FROM identity WHERE email = 'weak@example.test'").fetchone()[
            0
        ]
        == 0
    )
    captured = capsys.readouterr()
    assert "short!" not in captured.out + captured.err
    with tenant_transaction(
        conn,
        Scope(
            conn.execute("SELECT org_id FROM organization_code WHERE code='FIRST'").fetchone()[0]
        ),
    ):
        assert password not in str(
            conn.execute("SELECT to_jsonb(audit_log) FROM audit_log").fetchall()
        )
    command = [sys.executable, "-m", "flo.modules.org.bootstrap", *args]
    assert password not in str(command)
    process = subprocess.run(command, capture_output=True, text=True, env=os.environ.copy())
    assert process.returncode == 0
    assert password not in process.stdout + process.stderr
    # The no-echo stdin path delegates exclusively to getpass, never input().
    monkeypatch.delenv("FLO_BOOTSTRAP_PASSWORD")
    prompts = []
    monkeypatch.setattr(
        "flo.modules.org.bootstrap.getpass.getpass",
        lambda prompt: prompts.append(prompt) or password,
    )
    assert main(args) == 0
    assert prompts


@pytest.mark.parametrize("same_email", [True, False])
def test_concurrent_bootstrap_registry_arbitrates(bootstrap_db, same_email):
    conn = bootstrap_db
    barrier = Barrier(2)

    def attempt(index):
        with psycopg.connect(connection_url(conn), autocommit=True) as other:
            barrier.wait()
            from flo.modules.org.bootstrap import BootstrapConflict

            try:
                result = create(
                    other,
                    email="admin@example.test"
                    if same_email or index == 0
                    else "other@example.test",
                )
                return result.created
            except BootstrapConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, (0, 1)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False if same_email else "conflict") == 1
    assert conn.execute("SELECT count(*) FROM organization_code").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM organization").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM identity").fetchone()[0] == 1


def test_migration_preserves_existing_identity_organization_and_guards(bootstrap_db):
    conn = bootstrap_db
    result = create(conn)
    identity = admin_id(conn)
    with pytest.raises(UniqueViolation), conn.transaction():
        conn.execute(
            "INSERT INTO identity_membership(identity_id, org_id) VALUES (%s, %s)",
            (identity, result.org_id),
        )
    with pytest.raises(CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO organization_code(code, org_id) VALUES (%s, %s)", ("bad", result.org_id)
        )
    migration = next(
        item
        for item in discover_migrations(ROOT / "migrations")
        if item.revision == "20261008_0024"
    )
    with conn.transaction():
        migration.downgrade(conn)
        assert (
            conn.execute("SELECT email FROM identity WHERE id = %s", (identity,)).fetchone()[0]
            == "admin@example.test"
        )
        with tenant_transaction(conn, Scope(result.org_id)):
            assert (
                conn.execute(
                    "SELECT name FROM organization WHERE id = %s", (result.org_id,)
                ).fetchone()[0]
                == "First tenant"
            )
        migration.upgrade(conn)
    assert conn.execute("SELECT count(*) FROM identity_membership").fetchone()[0] == 0
    # D-M1-24/26: global links must resolve BEFORE app.org_id exists.
    for table in ("identity_membership", "organization_code"):
        assert conn.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass", (table,)
        ).fetchone() == (False,)


def test_bootstrap_non_mfa_role_plant_is_detected(bootstrap_db, monkeypatch):
    original = IdentityAuthorizationService.grant_role

    def wrong_role(service, user, role, target, **kwargs):
        roles = service.seed_baseline_roles()
        return original(
            service, user, roles[role_code("project-administrator")].id, target, **kwargs
        )

    monkeypatch.setattr(IdentityAuthorizationService, "grant_role", wrong_role)
    create(bootstrap_db)
    with pytest.raises(AssertionError):
        assert (
            mfa(bootstrap_db).access_requirement(admin_id(bootstrap_db))
            is MfaAccessRequirement.ENROLL
        )


@pytest.mark.parametrize(
    "values",
    [
        {"org_code": "!invalid"},
        {"org_name": " "},
        {"base_currency": "ZZZ"},
    ],
)
def test_bootstrap_rejects_invalid_inputs_without_partial_state(bootstrap_db, values):
    arguments = {
        "org_name": "First",
        "org_code": "FIRST",
        "admin_email": "admin@example.test",
        "password": "disposable bootstrap test passphrase",
        "settings": settings(bootstrap_db),
        **values,
    }
    with pytest.raises(ValueError):
        asyncio.run(bootstrap(bootstrap_db, **arguments))
    for table in ("organization", "organization_code", "identity", "identity_membership"):
        assert bootstrap_db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_bootstrap_audit_failure_rolls_back_everything(bootstrap_db, monkeypatch):
    def fail_audit(writer, **kwargs):
        if kwargs["action"] == "org.bootstrap":
            raise RuntimeError("planted audit failure")
        return original(writer, **kwargs)

    from flo.kernel.audit import AuditWriter

    original = AuditWriter.write
    monkeypatch.setattr(AuditWriter, "write", fail_audit)
    with pytest.raises(RuntimeError, match="planted audit failure"):
        create(bootstrap_db)
    for table in (
        "organization", "organization_code", "identity", "identity_membership", "role",
        "role_permission", "user_role", "audit_log",
    ):
        assert bootstrap_db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
