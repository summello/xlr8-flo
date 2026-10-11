"""Tenant-scoped notes, immutable revisions and retained attachment versions."""
from typing import Protocol

revision = "20261009_0041"
down_revision = "20261009_0040"

class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...

UPGRADE_SQL = """
CREATE TABLE note (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 document_type text NOT NULL, document_id uuid NOT NULL,
 author_id uuid NOT NULL REFERENCES identity(id), body text NOT NULL,
 visibility text NOT NULL CHECK (visibility IN ('internal','shareable')),
 locked boolean NOT NULL DEFAULT false, version int NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(), edited_at timestamptz,
 UNIQUE(org_id,id), CHECK (length(body) BETWEEN 1 AND 10000)
);
CREATE TABLE note_revision (
 id bigserial PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 note_id uuid NOT NULL, body text NOT NULL,
 visibility text NOT NULL CHECK (visibility IN ('internal','shareable')),
 edited_by uuid NOT NULL REFERENCES identity(id), edited_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(org_id,note_id) REFERENCES note(org_id,id)
);
CREATE TABLE attachment (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 document_type text NOT NULL, document_id uuid NOT NULL,
 created_by uuid NOT NULL REFERENCES identity(id), created_at timestamptz NOT NULL DEFAULT now(),
 deleted_at timestamptz, deleted_by uuid REFERENCES identity(id), UNIQUE(org_id,id)
);
CREATE TABLE attachment_version (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id), attachment_id uuid NOT NULL,
 version int NOT NULL CHECK(version > 0), original_filename text NOT NULL, display_name text NOT NULL,
 storage_key text NOT NULL UNIQUE, media_type text NOT NULL,
 size_bytes bigint NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760), sha256 char(64) NOT NULL,
 scan_status text NOT NULL CHECK(scan_status IN ('pending','basic_checks_passed','clean','quarantined','rejected')),
 uploaded_by uuid NOT NULL REFERENCES identity(id), uploaded_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(attachment_id,version), FOREIGN KEY(org_id,attachment_id) REFERENCES attachment(org_id,id)
);
CREATE FUNCTION collaboration_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'note revisions are append-only' USING ERRCODE='55000'; END $$;
CREATE TRIGGER note_revision_append_only BEFORE UPDATE OR DELETE ON note_revision
 FOR EACH ROW EXECUTE FUNCTION collaboration_revision_guard();
CREATE FUNCTION collaboration_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'attachment versions cannot be deleted' USING ERRCODE='55000'; END IF;
 IF (to_jsonb(NEW) - 'scan_status') IS DISTINCT FROM (to_jsonb(OLD) - 'scan_status') THEN
 RAISE EXCEPTION 'only scan_status can change' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER attachment_version_guard BEFORE UPDATE OR DELETE ON attachment_version
 FOR EACH ROW EXECUTE FUNCTION collaboration_version_guard();
CREATE INDEX note_document ON note(org_id,document_type,document_id);
CREATE INDEX attachment_document ON attachment(org_id,document_type,document_id);

ALTER TABLE note ENABLE ROW LEVEL SECURITY;
ALTER TABLE note FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON note
 USING (org_id=current_setting('app.org_id')::uuid)
 WITH CHECK (org_id=current_setting('app.org_id')::uuid);

ALTER TABLE note_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE note_revision FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON note_revision
 USING (org_id=current_setting('app.org_id')::uuid)
 WITH CHECK (org_id=current_setting('app.org_id')::uuid);

ALTER TABLE attachment ENABLE ROW LEVEL SECURITY;
ALTER TABLE attachment FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON attachment
 USING (org_id=current_setting('app.org_id')::uuid)
 WITH CHECK (org_id=current_setting('app.org_id')::uuid);

ALTER TABLE attachment_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE attachment_version FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON attachment_version
 USING (org_id=current_setting('app.org_id')::uuid)
 WITH CHECK (org_id=current_setting('app.org_id')::uuid);
"""
DOWNGRADE_SQL = """
DROP TABLE attachment_version, attachment, note_revision, note;
DROP FUNCTION collaboration_version_guard(), collaboration_revision_guard();
"""

def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)

def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
