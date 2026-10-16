"""Vendor tax and banking data behind one table, and the reveal permission (E11-S04)."""

from typing import Protocol

revision = "20261014_0060"
down_revision = "20261014_0059"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


CODE = "vendor.sensitive.reveal"
# Roles that get the reveal permission (decision 6h-4). The auditor does not; the organization
# administrator gets it through the "every code" rule, as in 0053.
ROLES = ("vendor-manager", "budget-finance-user", "organization-administrator")
_ROLES = ",".join(f"'{role}'" for role in ROLES)

# Every grant this migration inserts is recorded so downgrade removes exactly those.
TRACK = "migration_0060_grant"

UPGRADE_SQL = f"""
CREATE TABLE vendor_sensitive (
 org_id uuid NOT NULL REFERENCES organization(id),
 vendor_id uuid NOT NULL,
 tax_id text CHECK (char_length(tax_id) BETWEEN 1 AND 64),
 tax_id_country char(2),
 bank_account_name text CHECK (char_length(bank_account_name) BETWEEN 1 AND 200),
 bank_account_number text CHECK (char_length(bank_account_number) BETWEEN 1 AND 64),
 bank_routing text CHECK (char_length(bank_routing) BETWEEN 1 AND 64),
 version integer NOT NULL DEFAULT 1,
 updated_by uuid REFERENCES identity(id),
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (org_id, vendor_id),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id)
);
ALTER TABLE vendor_sensitive ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_sensitive FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON vendor_sensitive
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);

CREATE TABLE {TRACK}(org_id uuid NOT NULL, role_id uuid NOT NULL);
ALTER TABLE {TRACK} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {TRACK} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {TRACK}
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);

DO $$ DECLARE org uuid; added integer; BEGIN
 INSERT INTO permission(code) VALUES ('{CODE}') ON CONFLICT DO NOTHING;
 GET DIAGNOSTICS added = ROW_COUNT;
 -- the tracking table is tenant-scoped, so whether this migration inserted the permission
 -- itself is kept in its comment
 IF added > 0 THEN COMMENT ON TABLE {TRACK} IS 'permission_inserted'; END IF;
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id',org::text,true);
  WITH ins AS (INSERT INTO role_permission(org_id,role_id,permission_code)
   SELECT org,r.id,'{CODE}' FROM role r
   WHERE r.org_id=org AND r.is_system AND r.code IN ({_ROLES})
   ON CONFLICT DO NOTHING RETURNING org_id,role_id)
   INSERT INTO {TRACK}(org_id,role_id) SELECT org_id,role_id FROM ins;
 END LOOP;
END $$;
"""

# irreversible: down drops vendor_sensitive and the tax and banking values in it.
DOWNGRADE_SQL = f"""
DO $$ DECLARE org uuid; inserted boolean;
BEGIN
 inserted := coalesce(obj_description('{TRACK}'::regclass, 'pg_class'), '') = 'permission_inserted';
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id',org::text,true);
  DELETE FROM role_permission rp USING {TRACK} t
   WHERE t.org_id=org AND rp.org_id=org AND rp.role_id=t.role_id AND rp.permission_code='{CODE}';
  -- a permission this migration created has no grants that predate it
  IF inserted THEN DELETE FROM role_permission WHERE permission_code='{CODE}'; END IF;
 END LOOP;
 IF inserted THEN DELETE FROM permission WHERE code='{CODE}'; END IF;
END $$;
DROP TABLE {TRACK};
DROP TABLE vendor_sensitive;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
