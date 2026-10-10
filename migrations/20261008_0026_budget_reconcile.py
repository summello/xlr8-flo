"""Immutable reconciliation reports."""

from flo.kernel.migrate import MigrationConnection

revision = "20261008_0026"
down_revision = "20261008_0025"

UPGRADE_SQL = """
CREATE TABLE reconcile_run (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 started_at timestamptz NOT NULL, finished_at timestamptz NOT NULL,
 projects_checked int NOT NULL, drift_rows int NOT NULL,
 status text NOT NULL CHECK (status IN ('ok','drift','failed')), error_class text,
 UNIQUE (org_id, id)
);
CREATE INDEX reconcile_latest ON reconcile_run(org_id, started_at DESC, id DESC);
CREATE TABLE balance_drift_report (
 id uuid PRIMARY KEY, org_id uuid NOT NULL, run_id uuid NOT NULL,
 project_id uuid NOT NULL, bucket ledger_bucket NOT NULL, currency char(3) NOT NULL,
 ledger_total numeric(18,4) NOT NULL, balance_total numeric(18,4) NOT NULL,
 difference numeric(18,4) NOT NULL, detected_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id, run_id) REFERENCES reconcile_run(org_id, id),
 FOREIGN KEY (org_id, project_id) REFERENCES project(org_id, id),
 UNIQUE (run_id, project_id, bucket)
);
CREATE INDEX drift_page ON balance_drift_report(org_id, run_id, id);
ALTER TABLE reconcile_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE reconcile_run FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON reconcile_run
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE balance_drift_report ENABLE ROW LEVEL SECURITY;
ALTER TABLE balance_drift_report FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON balance_drift_report
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TRIGGER reconcile_run_append_only BEFORE UPDATE OR DELETE ON reconcile_run
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER reconcile_run_no_truncate BEFORE TRUNCATE ON reconcile_run
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER drift_append_only BEFORE UPDATE OR DELETE ON balance_drift_report
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER drift_no_truncate BEFORE TRUNCATE ON balance_drift_report
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(
        "DROP TABLE balance_drift_report; DROP TABLE reconcile_run;"
    )
