from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from flo.kernel.identity import IdentityId
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import (
    BASELINE_ROLE_PERMISSIONS,
    AuthorizationTarget,
    ScopeType,
    role_code,
)
from flo.modules.identity.resolver import AuthorizationResolver
from flo.modules.identity.service import IdentityAuthorizationService

from .conftest import AuthorizationDatabase


@contextmanager
def service_for(
    database: AuthorizationDatabase,
    scope: Scope,
    marker: str,
) -> Iterator[IdentityAuthorizationService]:
    with correlation_context(marker):
        yield IdentityAuthorizationService(
            database.service_connection,
            scope,
            database.actor_id,
        )


def resolver_for(database: AuthorizationDatabase, scope: Scope) -> AuthorizationResolver:
    return AuthorizationResolver(database.authorization_connection, scope)


def insert_identity(database: AuthorizationDatabase, label: str) -> IdentityId:
    identity_id = IdentityId(uuid4())
    database.connection.execute(
        "INSERT INTO identity (id, email, password_hash) VALUES (%s, %s, %s)",
        (identity_id, f"{label}-{identity_id}@example.test", "$argon2id$authz-test-placeholder"),
    )
    return identity_id


def check(
    database: AuthorizationDatabase,
    scope: Scope,
    user_id: IdentityId,
    permission: str,
    target: AuthorizationTarget,
) -> tuple[bool, bool]:
    with tenant_transaction(database.connection, scope):
        decision = resolver_for(database, scope).check(user_id, permission, target)
    return decision.scope_matched, decision.allowed


def test_new_role_and_new_permission_are_default_deny(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    user_id = insert_identity(database, "default-deny")
    with service_for(database, scope, "default-deny-role") as service:
        empty_role = service.create_role("empty-role", "Empty role")
        assignment = service.grant_role(
            user_id, empty_role.id, AuthorizationTarget.organization(database.org_a)
        )

    assert check(
        database,
        scope,
        user_id,
        "project.view",
        AuthorizationTarget.organization(database.org_a),
    ) == (True, False)

    database.connection.execute(
        "INSERT INTO permission (code) VALUES ('future_module.execute')"
    )
    with service_for(database, scope, "default-deny-admin") as service:
        roles = service.seed_baseline_roles()
        service.grant_role(
            user_id,
            roles[role_code("organization-administrator")].id,
            AuthorizationTarget.organization(database.org_a),
        )
    assert check(
        database,
        scope,
        user_id,
        "future_module.execute",
        AuthorizationTarget.organization(database.org_a),
    ) == (True, False)
    assert assignment.scope_id == database.org_a


def test_different_roles_in_different_business_units_are_an_exact_union(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    user_id = insert_identity(database, "multi-role")
    bu_a, bu_b, bu_other = uuid4(), uuid4(), uuid4()
    with service_for(database, scope, "multi-role-setup") as service:
        roles = service.seed_baseline_roles()
        for business_unit in (bu_a, bu_b, bu_other):
            service.register_scope(
                ScopeType.BU,
                business_unit,
                ScopeType.ORG,
                database.org_a,
            )
        service.grant_role(
            user_id,
            roles[role_code("requestor")].id,
            AuthorizationTarget(ScopeType.BU, bu_a),
        )
        service.grant_role(
            user_id,
            roles[role_code("approver")].id,
            AuthorizationTarget(ScopeType.BU, bu_b),
        )

    assert check(
        database, scope, user_id, "requisition.create", AuthorizationTarget(ScopeType.BU, bu_a)
    ) == (True, True)
    assert check(
        database, scope, user_id, "requisition.approve", AuthorizationTarget(ScopeType.BU, bu_a)
    ) == (True, False)
    assert check(
        database, scope, user_id, "requisition.approve", AuthorizationTarget(ScopeType.BU, bu_b)
    ) == (True, True)
    assert check(
        database, scope, user_id, "requisition.create", AuthorizationTarget(ScopeType.BU, bu_b)
    ) == (True, False)
    assert check(
        database,
        scope,
        user_id,
        "requisition.create",
        AuthorizationTarget(ScopeType.BU, bu_other, "requisition", uuid4()),
    ) == (False, False)


def test_project_grant_recurses_to_roll_down_descendants_without_sibling_leak(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    user_id = insert_identity(database, "project-tree")
    bu_id, root, child, grandchild, sibling = (uuid4() for _ in range(5))
    with service_for(database, scope, "project-tree-setup") as service:
        role = service.create_role("project-reader", "Project reader")
        service.grant_permission(role.id, "project.view")
        service.register_scope(ScopeType.BU, bu_id, ScopeType.ORG, database.org_a)
        service.register_scope(ScopeType.PROJECT, root, ScopeType.BU, bu_id)
        service.register_scope(ScopeType.PROJECT, child, ScopeType.PROJECT, root)
        service.register_scope(ScopeType.PROJECT, grandchild, ScopeType.PROJECT, child)
        service.register_scope(ScopeType.PROJECT, sibling, ScopeType.PROJECT, root)
        service.grant_role(
            user_id,
            role.id,
            AuthorizationTarget(ScopeType.PROJECT, root),
        )

    assert check(
        database, scope, user_id, "project.view", AuthorizationTarget(ScopeType.PROJECT, root)
    ) == (True, True)
    assert check(
        database,
        scope,
        user_id,
        "project.view",
        AuthorizationTarget(ScopeType.PROJECT, grandchild, "project", grandchild),
    ) == (True, True)

    sibling_user = insert_identity(database, "project-sibling")
    with service_for(database, scope, "project-sibling-setup") as service:
        service.grant_role(
            sibling_user,
            role.id,
            AuthorizationTarget(ScopeType.PROJECT, sibling),
        )
    assert check(
        database,
        scope,
        sibling_user,
        "project.view",
        AuthorizationTarget(ScopeType.PROJECT, grandchild, "project", grandchild),
    ) == (False, False)

    with service_for(database, scope, "disable-roll-down") as service:
        service.register_scope(
            ScopeType.PROJECT,
            child,
            ScopeType.PROJECT,
            root,
            roll_down=False,
        )
    assert check(
        database,
        scope,
        user_id,
        "project.view",
        AuthorizationTarget(ScopeType.PROJECT, grandchild, "project", grandchild),
    ) == (False, False)


def test_bu_grant_contains_projects_even_when_project_roll_down_is_disabled(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    user_id = insert_identity(database, "bu-project")
    bu_id, parent, child = uuid4(), uuid4(), uuid4()
    with service_for(database, scope, "bu-project-setup") as service:
        role = service.create_role("bu-reader", "BU reader")
        service.grant_permission(role.id, "project.view")
        service.register_scope(ScopeType.BU, bu_id, ScopeType.ORG, database.org_a)
        service.register_scope(ScopeType.PROJECT, parent, ScopeType.BU, bu_id)
        service.register_scope(
            ScopeType.PROJECT,
            child,
            ScopeType.PROJECT,
            parent,
            roll_down=False,
        )
        service.grant_role(user_id, role.id, AuthorizationTarget(ScopeType.BU, bu_id))
    assert check(
        database, scope, user_id, "project.view", AuthorizationTarget(ScopeType.PROJECT, child)
    ) == (True, True)


def test_org_grant_contains_every_bu_and_project(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    user_id = insert_identity(database, "org-project")
    bu_id, project_id = uuid4(), uuid4()
    with service_for(database, scope, "org-project-setup") as service:
        role = service.create_role("org-reader", "Organization reader")
        service.grant_permission(role.id, "project.view")
        service.register_scope(ScopeType.BU, bu_id, ScopeType.ORG, database.org_a)
        service.register_scope(ScopeType.PROJECT, project_id, ScopeType.BU, bu_id)
        service.grant_role(
            user_id,
            role.id,
            AuthorizationTarget.organization(database.org_a),
        )
    assert check(
        database, scope, user_id, "project.view", AuthorizationTarget(ScopeType.BU, bu_id)
    ) == (True, True)
    assert check(
        database,
        scope,
        user_id,
        "project.view",
        AuthorizationTarget(ScopeType.PROJECT, project_id),
    ) == (True, True)


ROLE_CASES = (
    ("organization-administrator", "organization.admin", "future_module.execute"),
    ("project-administrator", "project.admin", "organization.admin"),
    ("requestor", "requisition.create", "requisition.approve"),
    ("approver", "requisition.approve", "requisition.create"),
    ("sourcing-specialist", "rfq.create", "purchase_order.create"),
    ("purchasing-user", "purchase_order.issue", "rfq.create"),
    ("vendor-manager", "vendor.edit", "budget.edit"),
    ("budget-finance-user", "budget.transfer", "project.edit"),
    ("warehouse-asset-user", "asset.edit", "budget.edit"),
    ("executive-viewer", "report.view", "project.edit"),
    ("auditor", "audit.read", "project.edit"),
)


def test_every_baseline_role_allows_only_its_explicit_permissions(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    database.connection.execute(
        "INSERT INTO permission (code) VALUES ('future_module.execute')"
    )
    with service_for(database, scope, "role-matrix") as service:
        roles = service.seed_baseline_roles()
        for role_name, allowed, denied in ROLE_CASES:
            user_id = insert_identity(database, role_name)
            service.grant_role(
                user_id,
                roles[role_code(role_name)].id,
                AuthorizationTarget.organization(database.org_a),
            )
            assert check(
                database,
                scope,
                user_id,
                allowed,
                AuthorizationTarget.organization(database.org_a),
            ) == (True, True)
            assert check(
                database,
                scope,
                user_id,
                denied,
                AuthorizationTarget.organization(database.org_a),
            ) == (True, False)
            assert allowed in BASELINE_ROLE_PERMISSIONS[role_code(role_name)]


def test_revocation_changes_the_next_check_and_writes_before_after_audit(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    user_id = insert_identity(database, "revoked")
    with service_for(database, scope, "grant-audit") as service:
        role = service.create_role("immediate-revoke", "Immediate revoke")
        service.grant_permission(role.id, "project.view")
        assignment = service.grant_role(
            user_id, role.id, AuthorizationTarget.organization(database.org_a)
        )
    assert check(
        database,
        scope,
        user_id,
        "project.view",
        AuthorizationTarget.organization(database.org_a),
    ) == (True, True)

    with service_for(database, scope, "revoke-audit") as service:
        assert service.revoke_role(assignment.id)
    assert check(
        database,
        scope,
        user_id,
        "project.view",
        AuthorizationTarget.organization(database.org_a),
    ) == (False, False)

    with tenant_transaction(database.connection, scope):
        rows = database.connection.execute(
            "SELECT action, before, after FROM audit_log "
            "WHERE action IN ('user_role.grant', 'user_role.revoke') "
            "ORDER BY occurred_at, id"
        ).fetchall()
    assert [row[0] for row in rows] == ["user_role.grant", "user_role.revoke"]
    assert rows[0][1] is None
    assert rows[0][2] == {
        "effective_from": None,
        "granted_at": assignment.granted_at.isoformat(),
        "granted_by": str(database.actor_id),
        "id": str(assignment.id),
        "role_id": str(assignment.role_id),
        "scope_id": str(database.org_a),
        "scope_type": "org",
        "user_id": str(user_id),
    }
    assert rows[1][1] == rows[0][2]
    assert rows[1][2] is None


def test_permission_grant_and_revoke_each_write_before_after_audit(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    with service_for(database, scope, "permission-audit") as service:
        role = service.create_role("permission-audit", "Permission audit")
        assert service.grant_permission(role.id, "project.view")
        assert service.revoke_permission(role.id, "project.view")

    with tenant_transaction(database.connection, scope):
        rows = database.connection.execute(
            "SELECT action, before, after FROM audit_log "
            "WHERE action LIKE 'role.permission.%' ORDER BY occurred_at, id"
        ).fetchall()
    assert rows == [
        (
            "role.permission.grant",
            {"permission": None},
            {"permission": "project.view"},
        ),
        (
            "role.permission.revoke",
            {"permission": "project.view"},
            {"permission": None},
        ),
    ]


def test_rls_and_scoped_queries_hide_another_organizations_grants(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    user_id = insert_identity(database, "foreign-org")
    scope_b = Scope(database.org_b)
    with service_for(database, scope_b, "foreign-org-setup") as service:
        role = service.create_role("foreign-reader", "Foreign reader")
        service.grant_permission(role.id, "project.view")
        service.grant_role(
            user_id, role.id, AuthorizationTarget.organization(database.org_b)
        )

    assert check(
        database,
        Scope(database.org_a),
        user_id,
        "project.view",
        AuthorizationTarget.organization(database.org_a),
    ) == (False, False)


def test_privileged_role_grants_maintain_global_mfa_count_transactionally(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    user_id = insert_identity(database, "privileged-mfa")
    with service_for(database, scope, "privileged-mfa-grants") as service:
        roles = service.seed_baseline_roles()
        regular = service.grant_role(
            user_id,
            roles[role_code("requestor")].id,
            AuthorizationTarget.organization(database.org_a),
        )
        administrator = service.grant_role(
            user_id,
            roles[role_code("organization-administrator")].id,
            AuthorizationTarget.organization(database.org_a),
        )
        auditor = service.grant_role(
            user_id,
            roles[role_code("auditor")].id,
            AuthorizationTarget.organization(database.org_a),
        )

    assert database.connection.execute(
        "SELECT privileged_role_grants FROM identity WHERE id = %s", (user_id,)
    ).fetchone() == (2,)
    with service_for(database, scope, "privileged-mfa-revokes") as service:
        assert service.revoke_role(regular.id)
        assert service.revoke_role(administrator.id)
    assert database.connection.execute(
        "SELECT privileged_role_grants FROM identity WHERE id = %s", (user_id,)
    ).fetchone() == (1,)
    with service_for(database, scope, "privileged-mfa-last-revoke") as service:
        assert service.revoke_role(auditor.id)
    assert database.connection.execute(
        "SELECT privileged_role_grants FROM identity WHERE id = %s", (user_id,)
    ).fetchone() == (0,)
