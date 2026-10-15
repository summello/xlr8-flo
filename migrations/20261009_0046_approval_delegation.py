"""Delegation periods, task reminders and escalation rules."""

from typing import Protocol

revision = "20261009_0046"
down_revision = "20261009_0045"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE approval_delegation (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 delegator_id uuid NOT NULL REFERENCES identity(id),
 delegate_id uuid NOT NULL REFERENCES identity(id),
 starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
 document_types text[] NULL, reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 revoked_at timestamptz NULL, revoked_by uuid NULL REFERENCES identity(id),
 CHECK (starts_at < ends_at), CHECK (delegator_id <> delegate_id),
 CHECK (ends_at <= starts_at + interval '366 days'),
 CHECK (document_types IS NULL OR cardinality(document_types) > 0)
);
CREATE INDEX approval_delegation_delegate ON approval_delegation(org_id,delegate_id,starts_at);
CREATE INDEX approval_delegation_delegator ON approval_delegation(org_id,delegator_id,starts_at);
-- One delegator cannot hold overlapping live periods; an advisory lock serialises racers.
CREATE FUNCTION approval_delegation_no_overlap() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.revoked_at IS NOT NULL THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.org_id::text || NEW.delegator_id::text, 0));
 IF EXISTS (SELECT 1 FROM approval_delegation d WHERE d.org_id = NEW.org_id
   AND d.delegator_id = NEW.delegator_id AND d.revoked_at IS NULL AND d.id <> NEW.id
   AND tstzrange(d.starts_at, d.ends_at, '[)') && tstzrange(NEW.starts_at, NEW.ends_at, '[)'))
 THEN RAISE EXCEPTION 'delegation periods overlap' USING ERRCODE = '23P01'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER approval_delegation_no_overlap BEFORE INSERT OR UPDATE ON approval_delegation
 FOR EACH ROW EXECUTE FUNCTION approval_delegation_no_overlap();
ALTER TABLE approval_delegation ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_delegation FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON approval_delegation
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE approval_task ADD COLUMN escalated_at timestamptz NULL,
 ADD COLUMN escalation_rule jsonb NULL, ADD COLUMN reminded_on date NULL;
"""
DOWNGRADE_SQL = """
ALTER TABLE approval_task DROP COLUMN escalated_at, DROP COLUMN escalation_rule,
 DROP COLUMN reminded_on;
DROP TABLE approval_delegation;
DROP FUNCTION approval_delegation_no_overlap();
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
