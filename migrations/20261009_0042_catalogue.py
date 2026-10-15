"""BU/OU-scoped catalogue items and their append-only event history."""

from typing import Protocol

revision = "20261009_0042"
down_revision = "20261009_0041"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE catalogue_item (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 bu_id uuid NOT NULL, line_type text NOT NULL CHECK (line_type IN ('item','service')),
 description text NOT NULL, normalized text NOT NULL, uom_code text NOT NULL,
 category_code text, last_unit_price NUMERIC(18,4) NOT NULL, currency char(3) NOT NULL,
 last_event text NOT NULL CHECK (last_event IN
  ('requested','sourced','purchased','supplied','serviced')),
 last_event_at timestamptz NOT NULL, last_vendor_id uuid,
 times_requested int NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(org_id,id),
 CONSTRAINT catalogue_item_key UNIQUE (org_id,bu_id,line_type,normalized,uom_code)
);
CREATE INDEX catalogue_item_recent ON catalogue_item(org_id,bu_id,last_event_at DESC);
CREATE TABLE catalogue_event (
 id bigserial PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 catalogue_item_id uuid NOT NULL,
 event text NOT NULL CHECK (event IN
  ('requested','sourced','purchased','supplied','serviced')),
 source_type text NOT NULL, source_id uuid NOT NULL, vendor_id uuid,
 quantity NUMERIC(18,4) NOT NULL, unit_price NUMERIC(18,4) NOT NULL, currency char(3) NOT NULL,
 at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id,catalogue_item_id) REFERENCES catalogue_item(org_id,id),
 CONSTRAINT catalogue_event_once
  UNIQUE (org_id,source_type,source_id,catalogue_item_id,event)
);
CREATE TRIGGER catalogue_event_append_only BEFORE UPDATE OR DELETE ON catalogue_event
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER catalogue_event_append_only_truncate BEFORE TRUNCATE ON catalogue_event
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
ALTER TABLE catalogue_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalogue_item FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON catalogue_item
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE catalogue_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalogue_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON catalogue_event
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE requisition_line ADD COLUMN catalogue_item_id uuid;
ALTER TABLE requisition_line ADD CONSTRAINT requisition_line_catalogue_fk
 FOREIGN KEY (org_id,catalogue_item_id) REFERENCES catalogue_item(org_id,id);
"""
DOWNGRADE_SQL = """
ALTER TABLE requisition_line DROP COLUMN catalogue_item_id;
DROP TABLE catalogue_event;
DROP TABLE catalogue_item;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
