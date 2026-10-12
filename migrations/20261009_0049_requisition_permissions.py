"""Requisition lifecycle permissions and existing system-role grants."""

from typing import Protocol

revision = "20261009_0049"
down_revision = "20261009_0048"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
INSERT INTO permission(code) VALUES ('requisition.submit'),('requisition.withdraw'),('requisition.cancel') ON CONFLICT DO NOTHING;
DO $$ DECLARE org uuid; BEGIN
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id',org::text,true);
  INSERT INTO role_permission(org_id,role_id,permission_code)
  SELECT org,r.id,p.code FROM role r CROSS JOIN permission p
  WHERE r.org_id=org AND r.is_system AND
   ((r.code='requestor' AND p.code IN ('requisition.submit','requisition.withdraw')) OR
    (r.code='organization-administrator' AND p.code IN ('requisition.submit','requisition.withdraw','requisition.cancel')))
  ON CONFLICT DO NOTHING;
 END LOOP;
END $$;
"""
DOWNGRADE_SQL = """
DELETE FROM role_permission WHERE permission_code IN ('requisition.submit','requisition.withdraw','requisition.cancel'); DELETE FROM permission WHERE code IN ('requisition.submit','requisition.withdraw','requisition.cancel');
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
