"""Project schedule containers and manual progress."""

from typing import Protocol

revision = "20261009_0032"
down_revision = "20261009_0031"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE project
 ADD COLUMN health text NOT NULL DEFAULT 'unknown'
 CHECK (health IN ('unknown','on_track','at_risk','off_track')),
 ADD COLUMN percent_complete smallint NOT NULL DEFAULT 0 CHECK (percent_complete BETWEEN 0 AND 100),
 ADD COLUMN actual_start date,
 ADD COLUMN actual_end date,
 ADD CONSTRAINT project_actual_dates CHECK (actual_end >= actual_start);
CREATE TABLE project_phase (
 id uuid PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 project_id uuid NOT NULL,
 sub_project_id uuid,
 name text NOT NULL,
 sequence smallint NOT NULL,
 planned_start date,
 planned_end date,
 actual_start date,
 actual_end date,
 percent_complete smallint NOT NULL DEFAULT 0 CHECK (percent_complete BETWEEN 0 AND 100),
 status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','in_progress','done','skipped')),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id, project_id) REFERENCES project(org_id, id),
 FOREIGN KEY (org_id, sub_project_id) REFERENCES project(org_id, id),
 UNIQUE (project_id, sequence),
 UNIQUE (project_id, id),
 CHECK (planned_end >= planned_start),
 CHECK (actual_end >= actual_start)
);
CREATE UNIQUE INDEX project_phase_child ON project_phase(sub_project_id)
 WHERE sub_project_id IS NOT NULL;
CREATE TABLE project_milestone (
 id uuid PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 project_id uuid NOT NULL,
 phase_id uuid,
 name text NOT NULL,
 due_date date NOT NULL,
 completed_on date,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id, project_id) REFERENCES project(org_id, id),
 FOREIGN KEY (project_id, phase_id) REFERENCES project_phase(project_id, id)
);
ALTER TABLE project_phase ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_phase FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON project_phase
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE project_milestone ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_milestone FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON project_milestone
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute("""
    DROP TABLE project_milestone;
    DROP TABLE project_phase;
    ALTER TABLE project DROP COLUMN health, DROP COLUMN percent_complete,
      DROP COLUMN actual_start, DROP COLUMN actual_end;
    """)
