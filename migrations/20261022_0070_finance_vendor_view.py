"""The finance role can open the vendor profile it may reveal tax and banking data on (E11-S13)."""

from typing import Protocol

revision = "20261022_0070"
down_revision = "20261022_0069"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


GRANTS = {"budget-finance-user": ("vendor.view",)}
_PAIRS = ",".join(f"('{role}','{code}')" for role, codes in GRANTS.items() for code in codes)

# Only the rows this migration inserts are recorded, so downgrade leaves a grant that was
# already there alone.
TRACK = "migration_0070_grant"

UPGRADE_SQL = f"""
CREATE TABLE {TRACK}(org_id uuid NOT NULL, role_id uuid NOT NULL, permission_code text NOT NULL);
ALTER TABLE {TRACK} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {TRACK} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {TRACK}
 USING(org_id=current_setting('app.org_id', true)::uuid)
 WITH CHECK(org_id=current_setting('app.org_id', true)::uuid);
DO $$ DECLARE org uuid; BEGIN
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id',org::text,true);
  WITH ins AS (INSERT INTO role_permission(org_id,role_id,permission_code)
   SELECT org,r.id,g.code FROM role r JOIN (VALUES {_PAIRS}) AS g(role_code,code)
    ON g.role_code=r.code
   WHERE r.org_id=org AND r.is_system
   ON CONFLICT DO NOTHING RETURNING org_id,role_id,permission_code)
   INSERT INTO {TRACK}(org_id,role_id,permission_code)
    SELECT org_id,role_id,permission_code FROM ins;
  INSERT INTO audit_log(org_id,actor_kind,action,target_type,outcome,reason,
   correlation_id,before,after)
   SELECT org,'system','role.permission.grant','role','success',
    'Finance can view the vendor profile it may reveal on (E11-S13)','migration-20261022_0070',
    '{{}}'::jsonb,jsonb_build_object('grants',jsonb_agg(
     jsonb_build_object('role',r.code,'permission',t.permission_code) ORDER BY r.code,
      t.permission_code))
   FROM {TRACK} t JOIN role r ON r.id=t.role_id WHERE t.org_id=org HAVING count(*)>0;
 END LOOP;
END $$;
"""

DOWNGRADE_SQL = f"""
DO $$ DECLARE org uuid; BEGIN
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id',org::text,true);
  DELETE FROM role_permission rp USING {TRACK} t
   WHERE t.org_id=org AND rp.org_id=org AND rp.role_id=t.role_id
    AND rp.permission_code=t.permission_code;
 END LOOP;
END $$;
DROP TABLE {TRACK};
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
