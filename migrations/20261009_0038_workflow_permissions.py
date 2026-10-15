"""Grant workflow configuration access to existing system administrators and auditors."""

from typing import Protocol

revision = "20261009_0038"
down_revision = "20261009_0037"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
INSERT INTO permission(code) VALUES ('workflow.read'),('workflow.manage') ON CONFLICT DO NOTHING;
INSERT INTO role_permission(org_id,role_id,permission_code)
 SELECT r.org_id,r.id,p.code FROM role r CROSS JOIN permission p
 WHERE r.is_system AND (
 (r.code = 'organization-administrator' AND p.code IN ('workflow.read','workflow.manage'))
 OR (r.code = 'auditor' AND p.code = 'workflow.read')) ON CONFLICT DO NOTHING;
"""
DOWNGRADE_SQL = """
DELETE FROM role_permission WHERE permission_code IN ('workflow.read','workflow.manage');
DELETE FROM permission WHERE code IN ('workflow.read','workflow.manage');
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
