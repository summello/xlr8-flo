"""Append-only, tenant-isolated financial ledger."""

from typing import Protocol

revision = "20260826_0019"
down_revision = "20260826_0018"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


# ponytail: unpartitioned to about 5M rows; partition by effective_date with a
# separate ledger_idempotency table to preserve organization-wide key uniqueness.
UPGRADE_SQL = """
CREATE TYPE ledger_type AS ENUM ('allocation','reservation','commitment','actual',
 'release','reversal','transfer','adjustment');
CREATE TYPE ledger_bucket AS ENUM ('allocated','reserved','committed','actual');
CREATE TABLE ledger_entry (
 id BIGSERIAL PRIMARY KEY, org_id uuid NOT NULL, bu_id uuid NOT NULL,
 project_id uuid NOT NULL, entry_type ledger_type NOT NULL, bucket ledger_bucket NOT NULL,
 amount numeric(18,4) NOT NULL CHECK (amount <> 0), currency char(3) NOT NULL,
 source_type text NOT NULL CHECK (source_type IN
 ('requisition','purchase_order','manual','transfer','import','system')),
 source_id uuid, transfer_group_id uuid,
 reverses_entry_id bigint REFERENCES ledger_entry(id),
 department_code text NOT NULL, ledger_account_code text NOT NULL,
 effective_date date NOT NULL, posted_at timestamptz NOT NULL DEFAULT now(),
 actor_id uuid NOT NULL, reason text, idempotency_key text,
 UNIQUE (org_id, idempotency_key),
 FOREIGN KEY (org_id, project_id, bu_id) REFERENCES project(org_id, id, bu_id),
 FOREIGN KEY (org_id, bu_id) REFERENCES org_unit(org_id, id),
 CONSTRAINT ledger_type_bucket_ok CHECK (
  (entry_type IN ('allocation','transfer','adjustment') AND bucket = 'allocated') OR
  (entry_type = 'reservation' AND bucket = 'reserved') OR
  (entry_type = 'commitment' AND bucket = 'committed') OR
  (entry_type = 'actual' AND bucket = 'actual') OR
  (entry_type = 'release' AND bucket IN ('reserved','committed')) OR
  entry_type = 'reversal'),
 CONSTRAINT ledger_sign_ok CHECK (
  (entry_type IN ('allocation','reservation','commitment','actual') AND amount > 0) OR
  (entry_type = 'release' AND amount < 0) OR
  entry_type IN ('transfer','adjustment','reversal')),
 CONSTRAINT ledger_source_link CHECK (
  (source_type IN ('requisition','purchase_order','transfer','import') AND source_id IS NOT NULL)
  OR (source_type = 'manual' AND reason IS NOT NULL) OR source_type = 'system'),
 CONSTRAINT ledger_transfer_group CHECK (entry_type <> 'transfer' OR transfer_group_id IS NOT NULL),
 CONSTRAINT ledger_reversal_link CHECK (
  (entry_type = 'reversal' AND reverses_entry_id IS NOT NULL) OR
  (entry_type <> 'reversal' AND reverses_entry_id IS NULL)),
 CONSTRAINT ledger_adjustment_reason CHECK (entry_type <> 'adjustment' OR reason IS NOT NULL)
);
ALTER TABLE ledger_entry ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger_entry FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ledger_entry
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TRIGGER ledger_entry_append_only BEFORE UPDATE OR DELETE ON ledger_entry
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER ledger_entry_append_only_truncate BEFORE TRUNCATE ON ledger_entry
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
CREATE INDEX ledger_project_date ON ledger_entry(org_id, project_id, effective_date);
CREATE INDEX ledger_source ON ledger_entry(org_id, source_type, source_id);
CREATE INDEX ledger_transfer ON ledger_entry(org_id, transfer_group_id)
 WHERE transfer_group_id IS NOT NULL;
CREATE UNIQUE INDEX ledger_one_reversal ON ledger_entry(reverses_entry_id)
 WHERE reverses_entry_id IS NOT NULL;
CREATE FUNCTION enforce_ledger_reversal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE original ledger_entry%ROWTYPE;
BEGIN
 IF NEW.entry_type <> 'reversal' OR NEW.reverses_entry_id IS NULL THEN
  RETURN NEW;
 END IF;
 SELECT * INTO original FROM ledger_entry
 WHERE id = NEW.reverses_entry_id AND org_id = NEW.org_id;
 IF NOT FOUND OR NEW.bucket <> original.bucket OR NEW.amount <> -original.amount
  OR NEW.currency <> original.currency OR NEW.project_id <> original.project_id
  OR NEW.bu_id <> original.bu_id THEN
  RAISE EXCEPTION 'reversal must negate the same organization, project, BU, bucket and currency'
   USING ERRCODE = '23514';
 END IF;
 IF EXISTS (SELECT 1 FROM ledger_entry WHERE reverses_entry_id = NEW.reverses_entry_id) THEN
  RAISE EXCEPTION 'ledger entry already reversed' USING ERRCODE = '23505';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ledger_reversal_integrity BEFORE INSERT ON ledger_entry
 FOR EACH ROW EXECUTE FUNCTION enforce_ledger_reversal();
INSERT INTO permission(code) VALUES
 ('ledger.read'), ('budget.allocate'), ('budget.adjust'), ('budget.transfer')
 ON CONFLICT (code) DO NOTHING;
"""

# budget.transfer belongs to the original RBAC catalog; preserve it and its grants.
DOWNGRADE_SQL = """
DELETE FROM role_permission WHERE permission_code IN
 ('ledger.read','budget.allocate','budget.adjust');
DELETE FROM permission WHERE code IN
 ('ledger.read','budget.allocate','budget.adjust');
DROP TABLE ledger_entry;
DROP FUNCTION enforce_ledger_reversal();
DROP TYPE ledger_bucket;
DROP TYPE ledger_type;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
