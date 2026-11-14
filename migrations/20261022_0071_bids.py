"""Vendor bids keyed by the buyer, immutable once submitted (E12-S04)."""

from typing import Protocol

revision = "20261022_0071"
down_revision = "20261022_0070"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE bid (
 id uuid PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL, vendor_id uuid NOT NULL,
 rfq_version int NOT NULL,
 currency char(3) NOT NULL,
 sent_at timestamptz NOT NULL,
 evidence_attachment_id uuid,
 keyed_by uuid NOT NULL REFERENCES identity(id),
 keyed_at timestamptz NOT NULL DEFAULT now(),
 freight NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK (freight >= 0),
 lead_time_days int CHECK (lead_time_days >= 0),
 valid_until date,
 delivery_terms text CHECK (char_length(delivery_terms) <= 500),
 warranty text CHECK (char_length(warranty) <= 2000),
 exceptions text CHECK (char_length(exceptions) <= 5000),
 status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted')),
 submitted_at timestamptz,
 replaces_bid_id uuid REFERENCES bid(id),
 row_version int NOT NULL DEFAULT 1,
 UNIQUE (org_id, id),
 CHECK ((status = 'submitted') = (submitted_at IS NOT NULL)),
 CHECK (status = 'draft' OR evidence_attachment_id IS NOT NULL),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id),
 FOREIGN KEY (rfq_id, vendor_id) REFERENCES rfq_invitee(rfq_id, vendor_id),
 FOREIGN KEY (rfq_id, rfq_version) REFERENCES rfq_version(rfq_id, version),
 FOREIGN KEY (org_id, evidence_attachment_id) REFERENCES attachment(org_id, id)
);
CREATE INDEX bid_rfq ON bid(rfq_id, vendor_id);
CREATE UNIQUE INDEX bid_one_draft ON bid(rfq_id, vendor_id) WHERE status = 'draft';
CREATE UNIQUE INDEX bid_one_original ON bid(rfq_id, vendor_id)
 WHERE status = 'submitted' AND replaces_bid_id IS NULL;
CREATE UNIQUE INDEX bid_replaced_once ON bid(replaces_bid_id) WHERE replaces_bid_id IS NOT NULL;
ALTER TABLE bid ENABLE ROW LEVEL SECURITY;
ALTER TABLE bid FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON bid
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TABLE bid_line (
 org_id uuid NOT NULL REFERENCES organization(id),
 bid_id uuid NOT NULL,
 rfq_line_id uuid NOT NULL REFERENCES rfq_line(id),
 quantity NUMERIC(18,4) NOT NULL CHECK (quantity > 0),
 quoted boolean NOT NULL,
 unit_price NUMERIC(18,4) CHECK (unit_price >= 0),
 tax NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK (tax >= 0),
 line_total NUMERIC(18,4),
 CHECK (quoted = (unit_price IS NOT NULL) AND quoted = (line_total IS NOT NULL)),
 PRIMARY KEY (bid_id, rfq_line_id),
 FOREIGN KEY (org_id, bid_id) REFERENCES bid(org_id, id)
);
ALTER TABLE bid_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE bid_line FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON bid_line
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TABLE bid_requirement (
 org_id uuid NOT NULL REFERENCES organization(id),
 bid_id uuid NOT NULL,
 requirement_id uuid NOT NULL REFERENCES rfq_requirement(id),
 met boolean NOT NULL,
 note text CHECK (char_length(note) <= 1000),
 PRIMARY KEY (bid_id, requirement_id),
 FOREIGN KEY (org_id, bid_id) REFERENCES bid(org_id, id)
);
ALTER TABLE bid_requirement ENABLE ROW LEVEL SECURITY;
ALTER TABLE bid_requirement FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON bid_requirement
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE FUNCTION bid_guard() RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'a bid cannot be deleted' USING ERRCODE = '55000';
 END IF;
 IF OLD.status = 'submitted' THEN
  RAISE EXCEPTION 'a submitted bid is immutable' USING ERRCODE = '55000';
 END IF;
 RETURN NEW;
END
$guard$;
CREATE TRIGGER bid_immutable BEFORE UPDATE OR DELETE ON bid
 FOR EACH ROW EXECUTE FUNCTION bid_guard();

CREATE FUNCTION bid_child_guard() RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
 IF TG_OP <> 'INSERT' AND (SELECT status FROM bid WHERE id = OLD.bid_id) <> 'draft' THEN
  RAISE EXCEPTION 'the lines and answers of a submitted bid are immutable' USING ERRCODE = '55000';
 END IF;
 IF TG_OP <> 'DELETE' AND (SELECT status FROM bid WHERE id = NEW.bid_id) <> 'draft' THEN
  RAISE EXCEPTION 'the lines and answers of a submitted bid are immutable' USING ERRCODE = '55000';
 END IF;
 IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END
$guard$;
CREATE TRIGGER bid_line_immutable BEFORE INSERT OR UPDATE OR DELETE ON bid_line
 FOR EACH ROW EXECUTE FUNCTION bid_child_guard();
CREATE TRIGGER bid_requirement_immutable BEFORE INSERT OR UPDATE OR DELETE ON bid_requirement
 FOR EACH ROW EXECUTE FUNCTION bid_child_guard();

CREATE TRIGGER bid_append_only_truncate BEFORE TRUNCATE ON bid
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER bid_line_append_only_truncate BEFORE TRUNCATE ON bid_line
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER bid_requirement_append_only_truncate BEFORE TRUNCATE ON bid_requirement
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
"""

DOWNGRADE_SQL = """
DROP TABLE bid_requirement;
DROP TABLE bid_line;
DROP TABLE bid;
DROP FUNCTION bid_child_guard();
DROP FUNCTION bid_guard();
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: captured bids, their lines and answers have no earlier home; down drops them.
    connection.execute(DOWNGRADE_SQL)
