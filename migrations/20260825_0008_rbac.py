"""Create tenant-scoped roles, permissions, assignments, and scope ancestry.

Revision ID: 20260825_0008
Revises: 20260825_0007
"""

from __future__ import annotations

from typing import Protocol

revision = "20260825_0008"
down_revision = "20260825_0007"


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


PERMISSION_CODES = (
    "asset.create",
    "asset.edit",
    "asset.view",
    "audit.read",
    "budget.approve",
    "budget.create",
    "budget.edit",
    "budget.transfer",
    "budget.view",
    "organization.admin",
    "organization.view",
    "project.admin",
    "project.approve",
    "project.create",
    "project.edit",
    "project.view",
    "purchase_order.amend",
    "purchase_order.approve",
    "purchase_order.close",
    "purchase_order.create",
    "purchase_order.issue",
    "purchase_order.view",
    "record.read",
    "report.view",
    "requisition.approve",
    "requisition.create",
    "requisition.edit",
    "requisition.view",
    "rfq.approve",
    "rfq.create",
    "rfq.edit",
    "rfq.view",
    "vendor.approve",
    "vendor.create",
    "vendor.edit",
    "vendor.view",
    "view.archive",
)

_PERMISSION_VALUES = ",\n".join(f"    ('{code}')" for code in PERMISSION_CODES)

UPGRADE_SQL = f"""
CREATE TABLE permission (
    code text PRIMARY KEY,
    CONSTRAINT permission_code_valid
        CHECK (code ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$')
);
INSERT INTO permission (code) VALUES
{_PERMISSION_VALUES};

CREATE TABLE role (
    id uuid PRIMARY KEY,
    org_id uuid NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    is_system boolean NOT NULL DEFAULT false,
    CONSTRAINT role_code_valid
        CHECK (code ~ '^[a-z][a-z0-9_]*(-[a-z0-9_]+)*$'),
    CONSTRAINT role_name_nonempty CHECK (btrim(name) <> ''),
    CONSTRAINT role_org_id_id_key UNIQUE (org_id, id),
    CONSTRAINT role_org_id_code_key UNIQUE (org_id, code)
);
ALTER TABLE role ENABLE ROW LEVEL SECURITY;
ALTER TABLE role FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON role
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TABLE role_permission (
    org_id uuid NOT NULL,
    role_id uuid NOT NULL,
    permission_code text NOT NULL REFERENCES permission(code) ON DELETE RESTRICT,
    PRIMARY KEY (org_id, role_id, permission_code),
    CONSTRAINT role_permission_role_fk
        FOREIGN KEY (org_id, role_id) REFERENCES role(org_id, id) ON DELETE CASCADE
);
ALTER TABLE role_permission ENABLE ROW LEVEL SECURITY;
ALTER TABLE role_permission FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON role_permission
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TABLE authorization_scope (
    org_id uuid NOT NULL,
    scope_type text NOT NULL,
    scope_id uuid NOT NULL,
    parent_scope_type text,
    parent_scope_id uuid,
    roll_down boolean NOT NULL DEFAULT true,
    PRIMARY KEY (org_id, scope_type, scope_id),
    CONSTRAINT authorization_scope_type_valid
        CHECK (scope_type IN ('bu', 'project')),
    CONSTRAINT authorization_scope_parent_complete
        CHECK ((parent_scope_type IS NULL) = (parent_scope_id IS NULL)),
    CONSTRAINT authorization_scope_parent_type_valid
        CHECK (parent_scope_type IS NULL OR parent_scope_type IN ('org', 'bu', 'project')),
    CONSTRAINT authorization_scope_not_self_parent
        CHECK (parent_scope_type IS DISTINCT FROM scope_type OR parent_scope_id <> scope_id),
    CONSTRAINT authorization_scope_org_parent_valid
        CHECK (parent_scope_type IS DISTINCT FROM 'org' OR parent_scope_id = org_id),
    CONSTRAINT authorization_scope_bu_parent_valid
        CHECK (scope_type <> 'bu' OR parent_scope_type = 'org'),
    CONSTRAINT authorization_scope_project_parent_valid
        CHECK (scope_type <> 'project' OR parent_scope_type IN ('bu', 'project'))
);
CREATE INDEX authorization_scope_parent_idx
    ON authorization_scope (org_id, parent_scope_type, parent_scope_id);
ALTER TABLE authorization_scope ENABLE ROW LEVEL SECURITY;
ALTER TABLE authorization_scope FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON authorization_scope
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TABLE user_role (
    id uuid PRIMARY KEY,
    org_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES identity(id) ON DELETE RESTRICT,
    role_id uuid NOT NULL,
    scope_type text NOT NULL,
    scope_id uuid NOT NULL,
    CONSTRAINT user_role_scope_type_valid
        CHECK (scope_type IN ('org', 'bu', 'project')),
    CONSTRAINT user_role_org_scope_valid
        CHECK (scope_type <> 'org' OR scope_id = org_id),
    CONSTRAINT user_role_role_fk
        FOREIGN KEY (org_id, role_id) REFERENCES role(org_id, id) ON DELETE CASCADE,
    CONSTRAINT user_role_grant_key UNIQUE (org_id, user_id, role_id, scope_type, scope_id)
);
CREATE INDEX user_role_effective_access_idx
    ON user_role (org_id, user_id, scope_type, scope_id, role_id);
ALTER TABLE user_role ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_role FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON user_role
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = """
DROP TABLE user_role;
DROP TABLE authorization_scope;
DROP TABLE role_permission;
DROP TABLE role;
DROP TABLE permission;
"""


def upgrade(connection: MigrationConnection) -> None:
    """Create the default-deny RBAC schema and explicit permission catalog."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove only the RBAC schema, preserving identities and audit history."""

    connection.execute(DOWNGRADE_SQL)
