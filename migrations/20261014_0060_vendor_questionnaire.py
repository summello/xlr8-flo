"""Versioned onboarding questionnaire, per-request answers and the vendor.request grant (E11-S05)."""

from typing import Protocol

revision = "20261014_0060"
down_revision = "20261014_0059"

# Every baseline role but the read-only auditor: any employee may ask for a new vendor.
GRANTED_TO = (
    "organization-administrator",
    "project-administrator",
    "project-manager",
    "requestor",
    "approver",
    "sourcing-specialist",
    "purchasing-user",
    "vendor-manager",
    "budget-finance-user",
    "warehouse-asset-user",
    "executive-viewer",
)
_ROLES = ",".join(f"'{code}'" for code in GRANTED_TO)


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


def _tenant_table(name: str) -> str:
    return f"""
ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {name} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {name}
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
"""


UPGRADE_SQL = f"""
CREATE TABLE vendor_questionnaire (
 org_id uuid PRIMARY KEY REFERENCES organization(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
{_tenant_table("vendor_questionnaire")}
CREATE TABLE vendor_questionnaire_version (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES vendor_questionnaire(org_id),
 number int NOT NULL CHECK (number >= 1),
 status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','retired')),
 threshold int NOT NULL DEFAULT 0 CHECK (threshold BETWEEN 0 AND 100),
 row_version int NOT NULL DEFAULT 1,
 created_by uuid NOT NULL REFERENCES identity(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 published_at timestamptz, published_by uuid REFERENCES identity(id),
 UNIQUE (org_id, number), UNIQUE (org_id, id)
);
CREATE UNIQUE INDEX vendor_questionnaire_one_published
 ON vendor_questionnaire_version(org_id) WHERE status = 'published';
{_tenant_table("vendor_questionnaire_version")}
CREATE TABLE vendor_question (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 version_id uuid NOT NULL, position int NOT NULL CHECK (position >= 1),
 key text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{{0,39}}$'),
 prompt text NOT NULL CHECK (char_length(prompt) BETWEEN 1 AND 500),
 type text NOT NULL CHECK (type IN
  ('text','number','yes_no','single_choice','multi_choice','date','file')),
 required boolean NOT NULL DEFAULT false,
 weight int NOT NULL CHECK (weight BETWEEN 0 AND 100),
 options jsonb, row_version int NOT NULL DEFAULT 1,
 UNIQUE (version_id, key),
 UNIQUE (version_id, position) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY (org_id, version_id) REFERENCES vendor_questionnaire_version(org_id, id)
);
{_tenant_table("vendor_question")}
CREATE TABLE vendor_onboarding (
 vendor_id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 version_id uuid NOT NULL,
 answers jsonb NOT NULL DEFAULT '{{}}'::jsonb,
 score int CHECK (score BETWEEN 0 AND 100), breakdown jsonb,
 requested_by uuid NOT NULL REFERENCES identity(id),
 submitted_at timestamptz, row_version int NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id),
 FOREIGN KEY (org_id, version_id) REFERENCES vendor_questionnaire_version(org_id, id)
);
{_tenant_table("vendor_onboarding")}

-- A published or retired version, and its questions, never change again (CUS-007).
CREATE FUNCTION vendor_question_guard() RETURNS trigger LANGUAGE plpgsql AS $guard$
DECLARE locked boolean;
BEGIN
 SELECT status <> 'draft' INTO locked FROM vendor_questionnaire_version
  WHERE id = (CASE WHEN TG_OP = 'INSERT' THEN NEW.version_id ELSE OLD.version_id END);
 IF locked IS DISTINCT FROM false THEN
  RAISE EXCEPTION 'vendor_question of a published or retired version is immutable: % is forbidden', TG_OP
   USING ERRCODE = '55000';
 END IF;
 RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END
$guard$;
CREATE TRIGGER vendor_question_immutable BEFORE INSERT OR UPDATE OR DELETE ON vendor_question
 FOR EACH ROW EXECUTE FUNCTION vendor_question_guard();
CREATE FUNCTION vendor_version_guard() RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
 IF TG_OP = 'DELETE' THEN
  IF OLD.status <> 'draft' THEN
   RAISE EXCEPTION 'a published or retired questionnaire version cannot be deleted'
    USING ERRCODE = '55000';
  END IF;
  RETURN OLD;
 END IF;
 IF OLD.status = 'published' AND NEW.status = 'retired'
  AND (NEW.id, NEW.org_id, NEW.number, NEW.threshold, NEW.published_at, NEW.published_by)
   IS NOT DISTINCT FROM
   (OLD.id, OLD.org_id, OLD.number, OLD.threshold, OLD.published_at, OLD.published_by) THEN
  RETURN NEW;
 END IF;
 IF OLD.status <> 'draft' THEN
  RAISE EXCEPTION 'a published or retired questionnaire version is immutable'
   USING ERRCODE = '55000';
 END IF;
 RETURN NEW;
END
$guard$;
CREATE TRIGGER vendor_version_immutable BEFORE UPDATE OR DELETE ON vendor_questionnaire_version
 FOR EACH ROW EXECUTE FUNCTION vendor_version_guard();

INSERT INTO permission(code) VALUES ('vendor.request') ON CONFLICT DO NOTHING;
DO $$ DECLARE org uuid; BEGIN
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id', org::text, true);
  INSERT INTO role_permission(org_id, role_id, permission_code)
   SELECT org, r.id, 'vendor.request' FROM role r
   WHERE r.org_id = org AND r.is_system AND r.code IN ({_ROLES})
   ON CONFLICT DO NOTHING;
 END LOOP;
END $$;
"""

DOWNGRADE_SQL = """
DO $$ DECLARE org uuid; BEGIN
 FOR org IN SELECT DISTINCT org_id FROM role LOOP
  PERFORM set_config('app.org_id', org::text, true);
  DELETE FROM role_permission WHERE org_id = org AND permission_code = 'vendor.request';
 END LOOP;
END $$;
DELETE FROM permission WHERE code = 'vendor.request';
DROP TABLE vendor_onboarding;
DROP TABLE vendor_question;
DROP TABLE vendor_questionnaire_version;
DROP TABLE vendor_questionnaire;
DROP FUNCTION vendor_version_guard();
DROP FUNCTION vendor_question_guard();
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: questionnaires, answers and scores recorded since upgrade are dropped.
    connection.execute(DOWNGRADE_SQL)
