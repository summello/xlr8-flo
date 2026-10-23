"""People have a display name, backfilled from their latest accepted invitation (E22-S07)."""

from typing import Protocol

revision = "20261022_0072"
down_revision = "20261022_0071"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


# row_version is the If-Match version for PATCH /me (convention O); identity had none.
UPGRADE_SQL = """
ALTER TABLE identity
    ADD COLUMN display_name text,
    ADD COLUMN row_version integer NOT NULL DEFAULT 1,
    ADD CONSTRAINT identity_display_name_length
        CHECK (display_name IS NULL OR length(btrim(display_name)) BETWEEN 1 AND 200);
-- invitation is FORCE RLS: read each organization under its own scope into one temp table,
-- then give every identity its globally latest used invitation name.
CREATE TEMP TABLE identity_name_backfill (used_by uuid, full_name text, used_at timestamptz)
    ON COMMIT DROP;
DO $$
DECLARE org uuid;
BEGIN
    FOR org IN SELECT DISTINCT org_id FROM identity_membership LOOP
        PERFORM set_config('app.org_id', org::text, true);
        INSERT INTO identity_name_backfill
            SELECT used_by, btrim(full_name), used_at FROM invitation
            WHERE used_by IS NOT NULL AND length(btrim(full_name)) BETWEEN 1 AND 200;
    END LOOP;
END $$;
UPDATE identity i SET display_name = latest.full_name
FROM (
    SELECT DISTINCT ON (used_by) used_by, full_name FROM identity_name_backfill
    ORDER BY used_by, used_at DESC
) latest
WHERE latest.used_by = i.id AND i.display_name IS NULL;
DROP TABLE identity_name_backfill
"""

DOWNGRADE_SQL = """
ALTER TABLE identity
    DROP CONSTRAINT identity_display_name_length,
    DROP COLUMN row_version,
    DROP COLUMN display_name
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
