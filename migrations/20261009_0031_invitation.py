"""Tenant invitations and global hashed public-link resolution."""
from typing import Protocol

revision = "20261009_0031"
down_revision = "20261009_0030"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE invitation (
 id uuid PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 email text NOT NULL,
 full_name text,
 role_code text NOT NULL,
 scope_type text NOT NULL CHECK (scope_type IN ('org', 'bu', 'project')),
 scope_id uuid,
 invited_by uuid NOT NULL REFERENCES identity(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 sent_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,
 resend_counter integer NOT NULL DEFAULT 0 CHECK (resend_counter >= 0),
 used_at timestamptz,
 used_by uuid REFERENCES identity(id),
 withdrawn_at timestamptz,
 withdrawn_by uuid REFERENCES identity(id),
 CHECK (used_at IS NULL OR withdrawn_at IS NULL),
 CHECK ((used_at IS NULL) = (used_by IS NULL)),
 CHECK ((withdrawn_at IS NULL) = (withdrawn_by IS NULL))
);
CREATE UNIQUE INDEX invitation_open_email ON invitation (org_id, lower(email))
 WHERE used_at IS NULL AND withdrawn_at IS NULL;
ALTER TABLE invitation ENABLE ROW LEVEL SECURITY;
ALTER TABLE invitation FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON invitation
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TABLE invitation_token (
 token_hash char(64) PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
 invitation_id uuid NOT NULL REFERENCES invitation(id) ON DELETE CASCADE,
 org_id uuid NOT NULL REFERENCES organization(id),
 UNIQUE (invitation_id)
);
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute("DROP TABLE invitation_token; DROP TABLE invitation")
