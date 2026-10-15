"""Make every checked permission grantable: catalogue codes, Project Manager role, role grants."""

from typing import Protocol

revision = "20261014_0053"
down_revision = "20261009_0052"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


NEW_CODES = ("project_request.create", "master_code.substitute")
RETIRED = ("project.view", "project.edit")  # checked by no API code; project.read/update replace
CODES = (
    "budget.adjust budget.allocate fiscal.close fiscal.manage fiscal.read fiscal.reopen fx.read "
    "import.read import.run ledger.read master.manage master.read org.setting.manage "
    "org.unit.manage org.unit.read project.close.override project.complete project.read "
    "project.submit project.update"
).split() + list(NEW_CODES)

# Grants for the system roles except organization-administrator, who gets every code.
GRANTS = {
    "project-administrator": (
        "project.read project.update project.submit project.complete project.close.override "
        "budget.allocate ledger.read master.read"
    ),
    "project-manager": (
        "project.read project.create project.update project.submit project.complete "
        "requisition.view requisition.create requisition.edit requisition.submit "
        "requisition.withdraw project_request.create master_code.substitute budget.view "
        "ledger.read master.read record.read report.view"
    ),
    "budget-finance-user": "budget.allocate budget.adjust ledger.read fiscal.read fx.read",
    "requestor": "project.read",
    "approver": "project.read",
    "auditor": (
        "ledger.read fiscal.read master.read import.read fx.read project.read org.unit.read"
    ),
}
_PAIRS = ",".join(
    f"('{role}','{code}')" for role, codes in GRANTS.items() for code in codes.split()
)
_CODES = ",".join(f"('{code}')" for code in CODES)

_RETIRED = ",".join(f"'{code}'" for code in RETIRED)

# Every row this migration inserts, renames or deletes is recorded here so downgrade can undo
# exactly that and nothing else. kind: permission_added | permission_removed | role_added |
# role_renamed (code = the original code) | grant_added | grant_removed.
TRACK = "migration_0053_change"
_AUDIT = (
    "INSERT INTO audit_log(org_id,actor_kind,action,target_type,target_id,outcome,reason,"
    "correlation_id,before,after) VALUES (org,'system','role.rename','role',%s,'success',"
    "%s,'migration-20261014_0053',jsonb_build_object('code',%s),jsonb_build_object('code',%s));"
)

UPGRADE_SQL = f"""
CREATE TABLE {TRACK}(kind text NOT NULL, org_id uuid, role_id uuid, code text);
WITH ins AS (INSERT INTO permission(code) VALUES {_CODES} ON CONFLICT DO NOTHING RETURNING code)
 INSERT INTO {TRACK}(kind,code) SELECT 'permission_added',code FROM ins;
DO $$ DECLARE org uuid; rec record; renamed text; BEGIN
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id',org::text,true);
  FOR rec IN SELECT id FROM role WHERE org_id=org AND code='project-manager' AND NOT is_system LOOP
   renamed := 'project-manager-custom-' || replace(rec.id::text,'-','');
   UPDATE role SET code=renamed WHERE id=rec.id;
   INSERT INTO {TRACK}(kind,org_id,role_id,code) VALUES ('role_renamed',org,rec.id,'project-manager');
   {_AUDIT % ("rec.id", "'project-manager is the system Project Manager role'", "'project-manager'", "renamed")}
  END LOOP;
  WITH gone AS (DELETE FROM role_permission WHERE permission_code IN ({_RETIRED})
   RETURNING org_id,role_id,permission_code)
   INSERT INTO {TRACK}(kind,org_id,role_id,code) SELECT 'grant_removed',org_id,role_id,permission_code
    FROM gone;
  WITH ins AS (INSERT INTO role(id,org_id,code,name,is_system)
   VALUES (gen_random_uuid(),org,'project-manager','Project Manager',true)
   ON CONFLICT (org_id,code) DO NOTHING RETURNING id)
   INSERT INTO {TRACK}(kind,org_id,role_id) SELECT 'role_added',org,id FROM ins;
  WITH ins AS (INSERT INTO role_permission(org_id,role_id,permission_code)
   SELECT org,r.id,p.code FROM role r CROSS JOIN permission p
   WHERE r.org_id=org AND r.is_system AND r.code='organization-administrator'
    AND p.code NOT IN ({_RETIRED})
   ON CONFLICT DO NOTHING RETURNING org_id,role_id,permission_code)
   INSERT INTO {TRACK}(kind,org_id,role_id,code) SELECT 'grant_added',org_id,role_id,permission_code
    FROM ins;
  WITH ins AS (INSERT INTO role_permission(org_id,role_id,permission_code)
   SELECT org,r.id,g.code FROM role r JOIN (VALUES {_PAIRS}) AS g(role_code,code)
    ON g.role_code=r.code
   WHERE r.org_id=org AND r.is_system
   ON CONFLICT DO NOTHING RETURNING org_id,role_id,permission_code)
   INSERT INTO {TRACK}(kind,org_id,role_id,code) SELECT 'grant_added',org_id,role_id,permission_code
    FROM ins;
 END LOOP;
END $$;
WITH gone AS (DELETE FROM permission WHERE code IN ({_RETIRED}) RETURNING code)
 INSERT INTO {TRACK}(kind,code) SELECT 'permission_removed',code FROM gone;
"""
DOWNGRADE_SQL = f"""
INSERT INTO permission(code) SELECT code FROM {TRACK} WHERE kind='permission_removed'
 ON CONFLICT DO NOTHING;
DO $$ DECLARE org uuid; rec record; BEGIN
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id',org::text,true);
  DELETE FROM role_permission rp USING {TRACK} t
   WHERE t.kind='grant_added' AND t.org_id=org AND rp.org_id=org AND rp.role_id=t.role_id
    AND rp.permission_code=t.code;
  DELETE FROM role_permission WHERE role_id IN
   (SELECT role_id FROM {TRACK} WHERE kind='role_added' AND org_id=org);
  DELETE FROM user_role WHERE role_id IN
   (SELECT role_id FROM {TRACK} WHERE kind='role_added' AND org_id=org);
  DELETE FROM role WHERE id IN (SELECT role_id FROM {TRACK} WHERE kind='role_added' AND org_id=org);
  FOR rec IN SELECT role_id,code FROM {TRACK} WHERE kind='role_renamed' AND org_id=org LOOP
   {_AUDIT % ("rec.role_id", "'Migration 0053 reverted'", "(SELECT code FROM role WHERE id=rec.role_id)", "rec.code")}
   UPDATE role SET code=rec.code WHERE id=rec.role_id;
  END LOOP;
  INSERT INTO role_permission(org_id,role_id,permission_code)
   SELECT org_id,role_id,code FROM {TRACK} WHERE kind='grant_removed' AND org_id=org
   ON CONFLICT DO NOTHING;
  DELETE FROM role_permission WHERE permission_code IN
   (SELECT code FROM {TRACK} WHERE kind='permission_added');
 END LOOP;
END $$;
DELETE FROM permission WHERE code IN (SELECT code FROM {TRACK} WHERE kind='permission_added');
DROP TABLE {TRACK};
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
