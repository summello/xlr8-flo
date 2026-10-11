"""Organization fiscal calendar, immutable periods and transition history."""

from typing import Protocol

revision = "20260826_0016"
down_revision = "20260826_0015"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE fiscal_calendar (
 org_id uuid PRIMARY KEY REFERENCES organization(id),
 start_month smallint NOT NULL CHECK (start_month BETWEEN 1 AND 12),
 changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE fiscal_period (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 fiscal_year int NOT NULL, period_no smallint NOT NULL CHECK (period_no BETWEEN 1 AND 12),
 starts_on date NOT NULL, ends_on date NOT NULL CHECK (ends_on >= starts_on),
 status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
 closed_by uuid REFERENCES identity(id), closed_at timestamptz,
 UNIQUE (org_id, fiscal_year, period_no), UNIQUE (org_id, id),
 EXCLUDE USING gist (org_id WITH =, daterange(starts_on, ends_on, '[]') WITH &&)
);
CREATE TABLE fiscal_period_event (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 period_id uuid NOT NULL, action text NOT NULL CHECK (action IN ('close','reopen')),
 actor_id uuid NOT NULL REFERENCES identity(id), reason text,
 at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id, period_id) REFERENCES fiscal_period(org_id, id),
 CHECK (action <> 'reopen' OR (reason IS NOT NULL AND length(btrim(reason)) >= 10))
);
CREATE FUNCTION protect_fiscal_period() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'fiscal periods cannot be deleted' USING ERRCODE = '23514';
 END IF;
 IF ROW(NEW.id, NEW.org_id, NEW.fiscal_year, NEW.period_no, NEW.starts_on, NEW.ends_on)
    IS DISTINCT FROM
    ROW(OLD.id, OLD.org_id, OLD.fiscal_year, OLD.period_no, OLD.starts_on, OLD.ends_on) THEN
  RAISE EXCEPTION 'fiscal period dates and identity are immutable' USING ERRCODE = '23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER fiscal_period_immutable BEFORE UPDATE OR DELETE ON fiscal_period
 FOR EACH ROW EXECUTE FUNCTION protect_fiscal_period();
CREATE FUNCTION record_fiscal_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status IS DISTINCT FROM OLD.status THEN
  INSERT INTO fiscal_period_event(id, org_id, period_id, action, actor_id, reason)
  VALUES (gen_random_uuid(), NEW.org_id, NEW.id,
    CASE WHEN NEW.status = 'closed' THEN 'close' ELSE 'reopen' END,
    nullif(current_setting('app.fiscal_actor', true), '')::uuid,
    nullif(current_setting('app.fiscal_reason', true), ''));
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER fiscal_period_transition AFTER UPDATE ON fiscal_period
 FOR EACH ROW EXECUTE FUNCTION record_fiscal_transition();
CREATE TRIGGER fiscal_period_event_append_only BEFORE UPDATE OR DELETE ON fiscal_period_event
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
ALTER TABLE fiscal_calendar ENABLE ROW LEVEL SECURITY;
ALTER TABLE fiscal_calendar FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fiscal_calendar
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE fiscal_period ENABLE ROW LEVEL SECURITY;
ALTER TABLE fiscal_period FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fiscal_period
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE fiscal_period_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE fiscal_period_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fiscal_period_event
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
INSERT INTO permission(code) VALUES
 ('fiscal.manage'), ('fiscal.read'), ('fiscal.close'), ('fiscal.reopen');
"""
DOWNGRADE_SQL = """
DELETE FROM role_permission WHERE permission_code IN
 ('fiscal.manage','fiscal.read','fiscal.close','fiscal.reopen');
DELETE FROM permission WHERE code IN
 ('fiscal.manage','fiscal.read','fiscal.close','fiscal.reopen');
DROP TABLE fiscal_period_event;
DROP TABLE fiscal_period;
DROP TABLE fiscal_calendar;
DROP FUNCTION record_fiscal_transition();
DROP FUNCTION protect_fiscal_period();
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # btree_gist is shared with effective-dated addresses.
    connection.execute(DOWNGRADE_SQL)
