"""Purchasing users read vendors and the project ledger for the PO screens (E13-S12)."""

from typing import Protocol

revision = "20261102_0088"
down_revision = "20261101_0087"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


# Only the rows this migration inserts are recorded, so downgrade leaves a grant that was
# already there alone.
UPGRADE_SQL = """
CREATE TABLE migration_0088_grant(
 org_id uuid NOT NULL, role_id uuid NOT NULL, permission_code text NOT NULL);
ALTER TABLE migration_0088_grant ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_0088_grant FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON migration_0088_grant
 USING(org_id=current_setting('app.org_id')::uuid)
 WITH CHECK(org_id=current_setting('app.org_id')::uuid);
DO $$ DECLARE org uuid; BEGIN
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id',org::text,true);
  WITH ins AS (INSERT INTO role_permission(org_id,role_id,permission_code)
   SELECT org,r.id,g.code FROM role r
    JOIN (VALUES ('vendor.view'),('ledger.read')) AS g(code) ON true
   WHERE r.org_id=org AND r.is_system AND r.code='purchasing-user'
   ON CONFLICT DO NOTHING RETURNING org_id,role_id,permission_code)
   INSERT INTO migration_0088_grant(org_id,role_id,permission_code)
    SELECT org_id,role_id,permission_code FROM ins;
  INSERT INTO audit_log(org_id,actor_kind,action,target_type,outcome,reason,
   correlation_id,before,after)
   SELECT org,'system','role.permission.grant','role','success',
    'Purchasing users read vendors and the ledger for the PO screens (E13-S12)',
    'migration-20261102_0088','{}'::jsonb,jsonb_build_object('grants',jsonb_agg(
     jsonb_build_object('role',r.code,'permission',t.permission_code) ORDER BY t.permission_code))
   FROM migration_0088_grant t JOIN role r ON r.id=t.role_id WHERE t.org_id=org HAVING count(*)>0;
 END LOOP;
END $$;
"""

DOWNGRADE_SQL = """
DO $$ DECLARE org uuid; BEGIN
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id',org::text,true);
  DELETE FROM role_permission rp USING migration_0088_grant t
   WHERE t.org_id=org AND rp.org_id=org AND rp.role_id=t.role_id
    AND rp.permission_code=t.permission_code;
 END LOOP;
END $$;
DROP TABLE migration_0088_grant;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
