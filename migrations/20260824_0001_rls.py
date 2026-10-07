"""Enable and force tenant RLS on every existing business table.

Revision ID: 20260824_0001
Revises: None
"""

from __future__ import annotations

from typing import Protocol

revision = "20260824_0001"
down_revision: str | None = None


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
DO $migration$
DECLARE
    business_table record;
BEGIN
    FOR business_table IN
        SELECT columns.table_schema, columns.table_name
        FROM information_schema.columns AS columns
        JOIN pg_catalog.pg_class AS tables
          ON tables.oid = format('%I.%I', columns.table_schema, columns.table_name)::regclass
        WHERE columns.table_schema = 'public'
          AND columns.column_name = 'org_id'
          AND tables.relkind IN ('r', 'p')
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
            business_table.table_schema,
            business_table.table_name
        );
        EXECUTE format(
            'ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',
            business_table.table_schema,
            business_table.table_name
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS tenant_isolation ON %I.%I',
            business_table.table_schema,
            business_table.table_name
        );
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I.%I '
            'USING (org_id = current_setting(''app.org_id'')::uuid) '
            'WITH CHECK (org_id = current_setting(''app.org_id'')::uuid)',
            business_table.table_schema,
            business_table.table_name
        );
    END LOOP;
END
$migration$;
"""

DOWNGRADE_SQL = """
DO $migration$
DECLARE
    business_table record;
BEGIN
    FOR business_table IN
        SELECT columns.table_schema, columns.table_name
        FROM information_schema.columns AS columns
        JOIN pg_catalog.pg_class AS tables
          ON tables.oid = format('%I.%I', columns.table_schema, columns.table_name)::regclass
        WHERE columns.table_schema = 'public'
          AND columns.column_name = 'org_id'
          AND tables.relkind IN ('r', 'p')
    LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS tenant_isolation ON %I.%I',
            business_table.table_schema,
            business_table.table_name
        );
        EXECUTE format(
            'ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY',
            business_table.table_schema,
            business_table.table_name
        );
        EXECUTE format(
            'ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY',
            business_table.table_schema,
            business_table.table_name
        );
    END LOOP;
END
$migration$;
"""


def upgrade(connection: MigrationConnection) -> None:
    """Protect every existing table whose business rows carry ``org_id``."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove the policies installed by :func:`upgrade` without changing data."""

    connection.execute(DOWNGRADE_SQL)
