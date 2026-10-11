"""Permanent global ECB reference rates and ingest reports."""
from typing import Protocol

revision = "20261008_0028"
down_revision = "20261008_0027"

class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...

UPGRADE_SQL = """
CREATE TABLE fx_rate (
 base char(3) NOT NULL DEFAULT 'EUR', quote char(3) NOT NULL,
 rate NUMERIC(18,8) NOT NULL CHECK (rate > 0),
 effective_date date NOT NULL, source text NOT NULL DEFAULT 'ECB',
 fetched_at timestamptz NOT NULL,
 PRIMARY KEY (base, quote, effective_date, source)
);
CREATE TRIGGER fx_rate_append_only BEFORE UPDATE OR DELETE ON fx_rate
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER fx_rate_no_truncate BEFORE TRUNCATE ON fx_rate
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
CREATE TABLE fx_ingest_run (
 id bigserial PRIMARY KEY, started_at timestamptz NOT NULL,
 finished_at timestamptz NOT NULL, status text NOT NULL CHECK (status IN ('ok','failed')),
 rows_inserted integer NOT NULL DEFAULT 0, newest_effective_date date, error_class text
);
INSERT INTO permission(code) VALUES ('fx.read') ON CONFLICT (code) DO NOTHING;
"""
DOWNGRADE_SQL = """
DELETE FROM role_permission WHERE permission_code = 'fx.read';
DELETE FROM permission WHERE code = 'fx.read';
DROP TABLE fx_ingest_run;
DROP TABLE fx_rate;
"""
def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)

def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
