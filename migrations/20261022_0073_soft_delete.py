"""Soft delete of drafts: a `deleted` status on requisitions, projects, workflow versions and budget change requests (E22-S02)."""

from typing import Protocol

revision = "20261022_0073"
down_revision = "20261022_0072"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE requisition DROP CONSTRAINT requisition_status_check;
ALTER TABLE requisition ADD CONSTRAINT requisition_status_check CHECK (status IN
 ('draft','approval_pending','sourcing_in_progress','sourced','awarded','open_for_purchase',
 'completed','rejected','withdrawn','cancelled','expired','deleted'));
ALTER TABLE project DROP CONSTRAINT project_status_check;
ALTER TABLE project ADD CONSTRAINT project_status_check CHECK
 (status IN ('draft','approval_pending','active','deferred','completed','abandoned','deleted'));
ALTER TABLE workflow_version DROP CONSTRAINT workflow_version_status_check;
ALTER TABLE workflow_version ADD CONSTRAINT workflow_version_status_check
 CHECK (status IN ('draft','published','superseded','deleted'));
ALTER TABLE budget_change_request DROP CONSTRAINT budget_change_request_status_check;
ALTER TABLE budget_change_request ADD CONSTRAINT budget_change_request_status_check
 CHECK (status IN ('pending','approved','rejected','withdrawn','deleted'));
"""
# Rows are kept on the way down, mapped to the closest status the old constraints accept.
DOWNGRADE_SQL = """
UPDATE requisition SET status = 'withdrawn' WHERE status = 'deleted';
UPDATE project SET status = 'abandoned' WHERE status = 'deleted';
UPDATE budget_change_request SET status = 'withdrawn' WHERE status = 'deleted';
ALTER TABLE workflow_version DISABLE TRIGGER workflow_version_immutable;
UPDATE workflow_version SET status = 'superseded' WHERE status = 'deleted';
ALTER TABLE workflow_version ENABLE TRIGGER workflow_version_immutable;
ALTER TABLE requisition DROP CONSTRAINT requisition_status_check;
ALTER TABLE requisition ADD CONSTRAINT requisition_status_check CHECK (status IN
 ('draft','approval_pending','sourcing_in_progress','sourced','awarded','open_for_purchase',
 'completed','rejected','withdrawn','cancelled','expired'));
ALTER TABLE project DROP CONSTRAINT project_status_check;
ALTER TABLE project ADD CONSTRAINT project_status_check CHECK
 (status IN ('draft','approval_pending','active','deferred','completed','abandoned'));
ALTER TABLE workflow_version DROP CONSTRAINT workflow_version_status_check;
ALTER TABLE workflow_version ADD CONSTRAINT workflow_version_status_check
 CHECK (status IN ('draft','published','superseded'));
ALTER TABLE budget_change_request DROP CONSTRAINT budget_change_request_status_check;
ALTER TABLE budget_change_request ADD CONSTRAINT budget_change_request_status_check
 CHECK (status IN ('pending','approved','rejected','withdrawn'));
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
