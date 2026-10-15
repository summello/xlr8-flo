"""Grant delegation and approval administration to the baseline system roles."""

from typing import Protocol

revision = "20261009_0047"
down_revision = "20261009_0046"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
INSERT INTO permission(code) VALUES ('approval.delegate'),('approval.admin') ON CONFLICT DO NOTHING;
INSERT INTO role_permission(org_id,role_id,permission_code)
 SELECT r.org_id,r.id,p.code FROM role r CROSS JOIN permission p
 WHERE r.is_system AND (
 (r.code = 'organization-administrator' AND p.code IN ('approval.delegate','approval.admin'))
 OR (r.code = 'approver' AND p.code = 'approval.delegate')) ON CONFLICT DO NOTHING;
"""
DOWNGRADE_SQL = """
DELETE FROM role_permission WHERE permission_code IN ('approval.delegate','approval.admin');
DELETE FROM permission WHERE code IN ('approval.delegate','approval.admin');
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
