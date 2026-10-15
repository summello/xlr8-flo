"""USR-007: baseline roles grant what the API checks, for new and migrated tenants."""

from __future__ import annotations

from uuid import UUID

import psycopg
import pytest

from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import (
    BASELINE_ROLE_PERMISSIONS,
    BASELINE_ROLES,
    AuthorizationTarget,
    role_code,
)
from flo.modules.identity.resolver import AuthorizationResolver
from flo.modules.identity.service import IdentityAuthorizationService
from tests.authz.conftest import ROOT, load_migration
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import admin_id, create
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db

CHECKED = (
    "budget.allocate",
    "project.update",
    "ledger.read",
    "project_request.create",
    "master_code.substitute",
)
ALLOWED = {
    "organization-administrator": set(CHECKED),
    "project-administrator": {"budget.allocate", "project.update", "ledger.read"},
    "project-manager": {
        "project.update",
        "ledger.read",
        "project_request.create",
        "master_code.substitute",
    },
    "budget-finance-user": {"budget.allocate", "ledger.read"},
    "auditor": {"ledger.read"},
    "requestor": set(),
    "approver": set(),
    "sourcing-specialist": set(),
    "purchasing-user": set(),
    "vendor-manager": set(),
    "warehouse-asset-user": set(),
    "executive-viewer": set(),
}
MIGRATION = ROOT / "migrations/20261014_0053_baseline_role_catalogue.py"


def holder(conn, org: UUID, admin: UUID, role: str) -> UUID:
    """A new identity holding one baseline role organization-wide."""

    user = conn.execute(
        "INSERT INTO identity(id,email,password_hash) "
        "VALUES (gen_random_uuid(),%s,'$argon2id$test-placeholder') RETURNING id",
        (f"{role}@example.test",),
    ).fetchone()[0]
    target = AuthorizationTarget.organization(org)
    with tenant_transaction(conn, Scope(org)):
        role_id = conn.execute("SELECT id FROM role WHERE code=%s", (role,)).fetchone()[0]
    with correlation_context("grant"):
        IdentityAuthorizationService(conn, Scope(org), admin).grant_role(user, role_id, target)
    return user


def test_baseline_roles_hold_the_checked_permissions_and_no_more(bootstrap_db):
    conn = bootstrap_db
    org = create(conn).org_id
    admin = admin_id(conn)
    assert {str(code) for code, _ in BASELINE_ROLES} == set(ALLOWED)
    for role, allowed in ALLOWED.items():
        user = holder(conn, org, admin, role)
        with tenant_transaction(conn, Scope(org)), correlation_context("check"):
            resolver = AuthorizationResolver(conn, Scope(org))
            for permission in CHECKED:
                decision = resolver.check(user, permission, AuthorizationTarget.organization(org))
                assert decision.allowed == (permission in allowed), (role, permission)


def role_grants(conn, org: UUID) -> dict[str, set[str]]:
    with tenant_transaction(conn, Scope(org)):
        rows = conn.execute(
            "SELECT r.code, rp.permission_code FROM role r "
            "LEFT JOIN role_permission rp ON rp.role_id = r.id"
        ).fetchall()
    out: dict[str, set[str]] = {}
    for code, permission in rows:
        out.setdefault(code, set())
        if permission is not None:
            out[code].add(permission)
    return out


def snapshot(conn, org: UUID) -> dict[str, object]:
    """Everything migration 0053 may touch, keyed so two snapshots compare exactly."""

    with tenant_transaction(conn, Scope(org)):
        roles = conn.execute("SELECT id,code,name,is_system FROM role ORDER BY id").fetchall()
        granted = conn.execute(
            "SELECT role_id,permission_code FROM role_permission ORDER BY 1,2"
        ).fetchall()
        members = conn.execute("SELECT role_id,user_id FROM user_role ORDER BY 1,2").fetchall()
    permissions = conn.execute("SELECT code FROM permission ORDER BY code").fetchall()
    return {"roles": roles, "grants": granted, "members": members, "permissions": permissions}


@pytest.fixture
def pre_0053(empty_database, monkeypatch):
    """A tenant at the schema before 0053, with roles seeded as they were before E09-S13."""

    from flo.kernel.migrate import apply_migrations, discover_migrations
    from flo.modules.identity import service as identity_service
    from flo.modules.identity.models import permission_code
    from tests.authz.pre_s13_baseline import OLD_BASELINE, OLD_ROLES

    revisions = [
        r for r in discover_migrations(ROOT / "migrations") if r.revision < "20261014_0053"
    ]
    apply_migrations(revisions, empty_database)
    monkeypatch.setattr(
        identity_service, "BASELINE_ROLES", tuple((role_code(c), n) for c, n in OLD_ROLES.items())
    )
    monkeypatch.setattr(
        identity_service,
        "BASELINE_ROLE_PERMISSIONS",
        {
            role_code(c): frozenset(permission_code(p) for p in perms)
            for c, perms in OLD_BASELINE.items()
        },
    )
    with psycopg.connect(empty_database, autocommit=True) as conn:
        org = create(conn).org_id
        yield conn, org, admin_id(conn)


def test_upgrade_gives_every_system_role_exactly_the_baseline(pre_0053):
    conn, org, _ = pre_0053
    assert "project-manager" not in role_grants(conn, org)
    load_migration(MIGRATION, "role_catalogue_drift").upgrade(conn)
    expected = {str(c): {str(p) for p in perms} for c, perms in BASELINE_ROLE_PERMISSIONS.items()}
    assert role_grants(conn, org) == expected


def test_downgrade_restores_exactly_what_upgrade_changed(pre_0053):
    conn, org, admin = pre_0053
    with correlation_context("custom"):
        service = IdentityAuthorizationService(conn, Scope(org), admin)
        custom = service.create_role("custom-reader", "Custom reader")
        for permission in ("ledger.read", "project.view"):
            service.grant_permission(custom.id, permission)
        with tenant_transaction(conn, Scope(org)):
            requestor = conn.execute("SELECT id FROM role WHERE code='requestor'").fetchone()[0]
        service.grant_permission(requestor, "project.read")  # a grant made before 0053
    before = snapshot(conn, org)
    migration = load_migration(MIGRATION, "role_catalogue_roundtrip")

    migration.upgrade(conn)
    after = snapshot(conn, org)
    assert after != before
    grants_after = role_grants(conn, org)
    assert grants_after["custom-reader"] == {"ledger.read"}  # project.view is retired
    assert not [p for p in after["permissions"] if p[0] in ("project.view", "project.edit")]
    assert "master_code.substitute" in grants_after["organization-administrator"]
    assert "ledger.read" in grants_after["organization-administrator"]

    migration.downgrade(conn)
    assert snapshot(conn, org) == before
    assert conn.execute("SELECT to_regclass('migration_0053_change')").fetchone() == (None,)


def test_a_tenant_role_coded_project_manager_is_renamed_and_restored(pre_0053):
    conn, org, admin = pre_0053
    user = holder(conn, org, admin, "requestor")
    with correlation_context("collision"):
        service = IdentityAuthorizationService(conn, Scope(org), admin)
        mine = service.create_role("project-manager", "Our project managers")
        # Both earlier candidate names are taken too; the rename must still be unique.
        service.create_role("project-manager-custom", "Taken one")
        service.create_role(f"project-manager-custom-{str(mine.id)[:8]}", "Taken two")
        service.grant_permission(mine.id, "budget.view")
        service.grant_role(user, mine.id, AuthorizationTarget.organization(org))
    before = snapshot(conn, org)
    migration = load_migration(MIGRATION, "role_catalogue_collision")

    migration.upgrade(conn)
    with tenant_transaction(conn, Scope(org)):
        renamed = conn.execute(
            "SELECT code,name,is_system FROM role WHERE id=%s", (mine.id,)
        ).fetchone()
        system = conn.execute("SELECT is_system FROM role WHERE code='project-manager'").fetchone()
        audited = conn.execute(
            "SELECT count(*) FROM audit_log WHERE action='role.rename' AND target_id=%s",
            (mine.id,),
        ).fetchone()
        member = conn.execute(
            "SELECT count(*) FROM user_role WHERE role_id=%s AND user_id=%s", (mine.id, user)
        ).fetchone()
    renamed_code = f"project-manager-custom-{mine.id.hex}"
    assert renamed == (renamed_code, "Our project managers", False)
    assert system == (True,)
    assert audited == (1,)
    assert member == (1,)
    assert role_grants(conn, org)[renamed_code] == {"budget.view"}

    migration.downgrade(conn)
    assert snapshot(conn, org) == before


def test_permission_table_equals_the_catalogue_once_every_migration_ran(bootstrap_db):
    from flo.modules.identity.models import PERMISSION_CODES

    stored = {row[0] for row in bootstrap_db.execute("SELECT code FROM permission").fetchall()}
    assert stored == {str(code) for code in PERMISSION_CODES}
