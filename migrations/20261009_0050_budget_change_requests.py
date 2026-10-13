"""Tenant-scoped requests awaiting atomic budget approval."""
from typing import Protocol

revision = "20261009_0050"
down_revision = "20261009_0049"

class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


def upgrade(connection: MigrationConnection) -> None:
    connection.execute("""
CREATE TABLE budget_change_request (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 kind text NOT NULL CHECK(kind IN ('adjustment','transfer')),
 project_id uuid NOT NULL, payload jsonb NOT NULL,
 amount numeric(18,4) NOT NULL, currency char(3) NOT NULL,
 requested_by uuid NOT NULL REFERENCES identity(id), idempotency_key text NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','approved','rejected','withdrawn')),
 created_at timestamptz NOT NULL DEFAULT now(), decided_at timestamptz, result jsonb,
 UNIQUE(org_id,idempotency_key), UNIQUE(org_id,id),
 FOREIGN KEY(org_id,project_id) REFERENCES project(org_id,id)
);
ALTER TABLE budget_change_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE budget_change_request FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON budget_change_request
 USING(org_id=current_setting('app.org_id')::uuid)
 WITH CHECK(org_id=current_setting('app.org_id')::uuid);
""")


def downgrade(connection: MigrationConnection) -> None:
    connection.execute("DROP TABLE budget_change_request")
