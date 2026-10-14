"""Project Requests: own approval document type, sponsors on requisitions and projects (E09-S15)."""

from typing import Protocol, cast

revision = "20261014_0054"
down_revision = "20261014_0053"

TYPES = "'project','requisition','budget_change','vendor','rfq','award','po'"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


class _Cursor(Protocol):
    def fetchone(self) -> object: ...


def _sponsor_table(name: str, parent: str, column: str) -> str:
    return f"""
CREATE TABLE {name} (
 org_id uuid NOT NULL REFERENCES organization(id), {column} uuid NOT NULL,
 user_id uuid NOT NULL REFERENCES identity(id),
 PRIMARY KEY (org_id, {column}, user_id),
 FOREIGN KEY (org_id, {column}) REFERENCES {parent}(org_id, id)
);
ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {name} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {name}
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
"""


UPGRADE_SQL = f"""
ALTER TABLE workflow_definition DROP CONSTRAINT workflow_definition_document_type_check;
ALTER TABLE workflow_definition ADD CONSTRAINT workflow_definition_document_type_check
 CHECK (document_type IN ({TYPES.replace("'requisition',", "'requisition','project_request',")}));
ALTER TABLE approval_instance DISABLE TRIGGER approval_instance_pin_guard;
UPDATE approval_instance i SET document_type='project_request'
 FROM requisition r WHERE i.document_type='requisition' AND r.org_id=i.org_id
 AND r.id=i.document_id AND r.standalone;
ALTER TABLE approval_instance ENABLE TRIGGER approval_instance_pin_guard;
{_sponsor_table("requisition_sponsor", "requisition", "requisition_id")}
{_sponsor_table("project_sponsor", "project", "project_id")}
ALTER TABLE project ADD COLUMN source_requisition_id uuid;
UPDATE project SET source_requisition_id = generated_by_requisition_id
 WHERE generated_by_requisition_id IS NOT NULL;
UPDATE project p SET source_requisition_id = (a.after->>'requisition_id')::uuid
 FROM audit_log a WHERE p.source_requisition_id IS NULL AND a.action = 'project.create'
 AND a.target_id = p.id AND a.after->>'source' = 'requisition';
CREATE INDEX project_source_requisition ON project(org_id, source_requisition_id)
 WHERE source_requisition_id IS NOT NULL;
INSERT INTO project_sponsor(org_id, project_id, user_id)
 SELECT org_id, id, sponsor_id FROM project WHERE sponsor_id IS NOT NULL;
"""

DOWNGRADE_SQL = f"""
ALTER TABLE approval_instance DISABLE TRIGGER approval_instance_pin_guard;
UPDATE approval_instance SET document_type='requisition' WHERE document_type='project_request';
ALTER TABLE approval_instance ENABLE TRIGGER approval_instance_pin_guard;
DROP TABLE project_sponsor, requisition_sponsor;
DROP INDEX project_source_requisition;
ALTER TABLE project DROP COLUMN source_requisition_id;
ALTER TABLE workflow_definition DROP CONSTRAINT workflow_definition_document_type_check;
ALTER TABLE workflow_definition ADD CONSTRAINT workflow_definition_document_type_check
 CHECK (document_type IN ({TYPES}));
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: dropping requisition_sponsor and project_sponsor loses the sponsor rows
    # written since upgrade (requisition sponsors have no other home); project.sponsor_id keeps
    # the first project sponsor, and upgrade backfills project_sponsor from it.
    row = cast(
        "tuple[int] | None",
        cast(
            "_Cursor",
            connection.execute(
                "SELECT count(*) FROM workflow_definition WHERE document_type='project_request'"
            ),
        ).fetchone(),
    )
    if row and row[0]:
        raise RuntimeError("cannot downgrade: project_request workflows exist; retire and delete them")
    connection.execute(DOWNGRADE_SQL)
