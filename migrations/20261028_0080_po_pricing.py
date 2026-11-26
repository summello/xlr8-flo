"""Tax, freight and budget amount on every PO line; freight mode on the order (E13-S02)."""

from flo.kernel.migrate import MigrationConnection

revision = "20261028_0080"
down_revision = "20261028_0079"

# Existing rows (none are released: 0078 is not on main) become price = line_total = budget_amount,
# tax = freight = 0, rounded half up to the currency exponent.
# ponytail: the exponent list below mirrors kernel/money.py for the backfill only; it runs on no
# released row, so a new zero- or three-decimal currency needs no change here.
UPGRADE_SQL = """
ALTER TABLE purchase_order
 ADD COLUMN freight_mode text NOT NULL DEFAULT 'line'
  CHECK (freight_mode IN ('line','document')),
 ADD COLUMN document_freight NUMERIC(18,4) CHECK (document_freight >= 0),
 ADD CONSTRAINT purchase_order_freight_mode_doc
  CHECK ((freight_mode = 'document') = (document_freight IS NOT NULL));
ALTER TABLE po_line
 ADD COLUMN price NUMERIC(18,4) NOT NULL DEFAULT 0,
 ADD COLUMN tax NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK (tax >= 0),
 ADD COLUMN freight NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK (freight >= 0),
 ADD COLUMN line_total NUMERIC(18,4) NOT NULL DEFAULT 0,
 ADD COLUMN budget_amount NUMERIC(18,4) NOT NULL DEFAULT 0,
 ADD COLUMN budget_amount_project NUMERIC(18,4) NOT NULL DEFAULT 0;
CREATE OR REPLACE FUNCTION pg_temp.po_exponent(code text) RETURNS int LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE WHEN code IN ('BHD','IQD','JOD','KWD','LYD','OMR','TND') THEN 3
  WHEN code IN ('BIF','CLP','DJF','GNF','ISK','JPY','KMF','KRW','PYG','RWF','UGX','UYI','VND',
   'VUV','XAF','XOF','XPF') THEN 0 ELSE 2 END $$;
DO $$
DECLARE org uuid;
BEGIN
 FOR org IN SELECT id FROM organization LOOP
  PERFORM set_config('app.org_id', org::text, true);
  UPDATE po_line l SET
   price = round(l.unit_price * l.quantity, pg_temp.po_exponent(trim(p.currency))),
   line_total = round(l.unit_price * l.quantity, pg_temp.po_exponent(trim(p.currency))),
   budget_amount = round(l.unit_price * l.quantity, pg_temp.po_exponent(trim(p.currency))),
   budget_amount_project = round(
    round(l.unit_price * l.quantity, pg_temp.po_exponent(trim(p.currency)))
    * (p.rate->>'rate')::numeric, pg_temp.po_exponent(p.rate->>'to'))
  FROM purchase_order p WHERE p.org_id = l.org_id AND p.id = l.po_id AND l.org_id = org;
 END LOOP;
END $$;
ALTER TABLE po_line
 ALTER COLUMN price DROP DEFAULT, ALTER COLUMN tax DROP DEFAULT,
 ALTER COLUMN freight DROP DEFAULT, ALTER COLUMN line_total DROP DEFAULT,
 ALTER COLUMN budget_amount DROP DEFAULT, ALTER COLUMN budget_amount_project DROP DEFAULT;
"""

DOWNGRADE_SQL = """
ALTER TABLE po_line
 DROP COLUMN price, DROP COLUMN tax, DROP COLUMN freight, DROP COLUMN line_total,
 DROP COLUMN budget_amount, DROP COLUMN budget_amount_project;
ALTER TABLE purchase_order
 DROP CONSTRAINT purchase_order_freight_mode_doc,
 DROP COLUMN document_freight, DROP COLUMN freight_mode;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: the tax, freight and budget figures have no earlier home; down drops them.
    connection.execute(DOWNGRADE_SQL)
