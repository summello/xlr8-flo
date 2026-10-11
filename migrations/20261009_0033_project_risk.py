"""Tenant-scoped project risks with database-computed scores."""

from typing import Protocol

revision = "20261009_0033"
down_revision = "20261009_0032"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


def upgrade(connection: MigrationConnection) -> None:
    connection.execute("""
    CREATE TABLE project_risk (
      id uuid PRIMARY KEY,
      org_id uuid NOT NULL REFERENCES organization(id),
      project_id uuid NOT NULL,
      title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
      description text CHECK (length(description) <= 4000),
      likelihood smallint NOT NULL CHECK (likelihood BETWEEN 1 AND 5),
      impact smallint NOT NULL CHECK (impact BETWEEN 1 AND 5),
      score smallint GENERATED ALWAYS AS (likelihood * impact) STORED,
      owner_id uuid NOT NULL,
      mitigation text CHECK (length(mitigation) <= 4000),
      due_date date,
      status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','mitigating','closed','accepted')),
      closed_reason text,
      created_by uuid NOT NULL REFERENCES identity(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      version bigint NOT NULL DEFAULT 1,
      FOREIGN KEY (org_id, project_id) REFERENCES project(org_id, id),
      CHECK (status <> 'closed' OR btrim(coalesce(closed_reason,'')) <> '')
    );
    CREATE INDEX project_risk_score ON project_risk(project_id, score DESC);
    ALTER TABLE project_risk ENABLE ROW LEVEL SECURITY;
    ALTER TABLE project_risk FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON project_risk
      USING (org_id = current_setting('app.org_id')::uuid)
      WITH CHECK (org_id = current_setting('app.org_id')::uuid);
    """)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute("DROP TABLE project_risk")
