"""Tenant requisition headers and generated-project provenance."""
from typing import Protocol

revision = "20261009_0039"
down_revision = "20261009_0038"

class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...

UPGRADE_SQL = """
CREATE TABLE requisition (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 number text NOT NULL, title text NOT NULL, standalone boolean NOT NULL,
 project_id uuid, bu_id uuid NOT NULL, requester_id uuid NOT NULL REFERENCES identity(id),
 justification text, needed_by date, department_code text, ledger_account_code text,
 delivery_address_id uuid, estimate NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK (estimate >= 0),
 currency char(3) NOT NULL, status text NOT NULL DEFAULT 'draft' CHECK (status IN
 ('draft','approval_pending','sourcing_in_progress','sourced','awarded','open_for_purchase',
 'completed','rejected','withdrawn','cancelled','expired')),
 version int NOT NULL DEFAULT 1, submitted_at timestamptz, decided_at timestamptz,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(org_id,id),
 UNIQUE(org_id,bu_id,number), CHECK (standalone OR project_id IS NOT NULL),
 FOREIGN KEY(org_id,project_id) REFERENCES project(org_id,id)
);
CREATE INDEX requisition_project ON requisition(org_id,project_id);
CREATE INDEX requisition_requester ON requisition(org_id,requester_id,updated_at);
CREATE INDEX requisition_status ON requisition(org_id,status,updated_at);
ALTER TABLE requisition ENABLE ROW LEVEL SECURITY;
ALTER TABLE requisition FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON requisition
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE project ADD COLUMN generated_by_requisition_id uuid;
ALTER TABLE project ADD CONSTRAINT project_generated_requisition_fk
 FOREIGN KEY(org_id,generated_by_requisition_id) REFERENCES requisition(org_id,id);
CREATE UNIQUE INDEX project_generated_requisition ON project(org_id,generated_by_requisition_id)
 WHERE generated_by_requisition_id IS NOT NULL;
"""
DOWNGRADE_SQL = """
ALTER TABLE project DROP COLUMN generated_by_requisition_id;
DROP TABLE requisition;
"""

def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)

def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
