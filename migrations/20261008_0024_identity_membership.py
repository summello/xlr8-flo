"""Global identity membership and bootstrap code registry (D-M1-24/26).

These tables resolve the tenant before RLS context exists; they deliberately
contain only identity/code-to-organization links, not tenant business records.
"""

from typing import Protocol

revision = "20261008_0024"
down_revision = "20260826_0023"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE identity_membership (
 identity_id uuid PRIMARY KEY REFERENCES identity(id),
 org_id uuid NOT NULL REFERENCES organization(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX identity_membership_org_id_idx ON identity_membership(org_id);
CREATE TABLE organization_code (
 code text PRIMARY KEY CHECK (code ~ '^[A-Z][A-Z0-9_-]{1,31}$'),
 org_id uuid NOT NULL UNIQUE REFERENCES organization(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
"""
DOWNGRADE_SQL = """
DROP TABLE organization_code;
DROP TABLE identity_membership;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
