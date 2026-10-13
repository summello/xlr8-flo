"""Project status events may be written by a system actor (requisition expiry), not a person."""
from typing import Protocol, cast

revision = "20261009_0051"
down_revision = "20261009_0050"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


def upgrade(connection: MigrationConnection) -> None:
    connection.execute("""
ALTER TABLE project_status_event ALTER COLUMN actor_id DROP NOT NULL,
 ADD COLUMN system_actor text NULL,
 ADD CONSTRAINT project_status_event_actor CHECK (actor_id IS NOT NULL OR system_actor IS NOT NULL);
""")


def downgrade(connection: MigrationConnection) -> None:
    row = cast(
        "tuple[int] | None",
        cast(
            "_Cursor",
            connection.execute("SELECT count(*) FROM project_status_event WHERE actor_id IS NULL"),
        ).fetchone(),
    )
    if row and row[0]:
        raise RuntimeError("cannot downgrade: project_status_event rows have no actor_id")
    connection.execute("""
ALTER TABLE project_status_event DROP CONSTRAINT project_status_event_actor,
 DROP COLUMN system_actor, ALTER COLUMN actor_id SET NOT NULL;
""")


class _Cursor(Protocol):
    def fetchone(self) -> object: ...
