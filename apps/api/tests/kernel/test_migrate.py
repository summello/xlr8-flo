from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from flo.kernel import migrate
from flo.kernel.config import Settings

ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS = ROOT / "migrations"


def _database_url() -> tuple[str, bool]:
    configured = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    url = configured or "postgresql://flo:flo-local@127.0.0.1:5432/flo_test"
    return url.replace("postgresql+psycopg://", "postgresql://", 1), configured is not None


def legacy_draft(create):
    """Seed a standalone draft on a schema older than migration 0054 (no sponsor tables)."""
    from unittest.mock import patch

    from flo.modules.requisitions.service import RequisitionService

    with patch.object(RequisitionService, "_require_sponsors"):
        return create()


@pytest.fixture
def empty_database() -> Iterator[str]:
    administrative_url, configured = _database_url()
    database_name = f"flo_migrate_{uuid4().hex}"
    connection_parameters = conninfo_to_dict(administrative_url)
    connection_parameters["dbname"] = database_name
    database_url = make_conninfo(**connection_parameters)
    try:
        with psycopg.connect(administrative_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    except psycopg.Error as exc:
        if configured:
            pytest.fail(f"configured Postgres is unavailable: {type(exc).__name__}")
        pytest.skip("local Postgres is unavailable; run the repository stack")
        raise
    try:
        yield database_url
    finally:
        with psycopg.connect(administrative_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name))
            )


def _write_revision(
    directory: Path,
    filename: str,
    revision: str,
    down_revision: str | None,
    upgrade_body: str = "pass",
) -> None:
    body = textwrap.indent(textwrap.dedent(upgrade_body).strip(), "    ")
    (directory / filename).write_text(
        f"""revision = {revision!r}
down_revision = {down_revision!r}

def upgrade(connection):
{body}

def downgrade(connection):
    pass
""",
        encoding="utf-8",
    )


def test_settings_default_and_environment_select_the_migrations_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MIGRATIONS_DIR", raising=False)
    assert Settings().migrations_dir == Path("/app/migrations")

    monkeypatch.setenv("MIGRATIONS_DIR", str(tmp_path))
    assert Settings().migrations_dir == tmp_path


def test_real_migration_chain_validates_with_the_metadata_gap() -> None:
    revisions = migrate.discover_migrations(MIGRATIONS)

    assert [item.revision for item in revisions] == [
        "20260824_0001",
        "20260824_0002",
        "20260825_0003",
        "20260825_0004",
        "20260825_0005",
        "20260825_0007",
        "20260825_0008",
        "20260825_0009",
        "20260825_0010",
        "20260825_0011",
        "20260825_0012",
        "20260826_0013",
        "20260826_0014",
        "20260826_0015",
        "20260826_0016",
        "20260826_0017",
        "20260826_0018",
        "20260826_0019",
        "20260826_0020",
        "20260826_0021",
        "20260826_0022",
        "20260826_0023",
        "20261008_0024",
        "20261008_0025",
        "20261008_0026",
        "20261008_0027",
        "20261008_0028",
        "20261009_0029",
        "20261009_0030",
        "20261009_0031",
        "20261009_0032",
        "20261009_0033",
        "20261009_0034",
        "20261009_0035",
        "20261009_0036",
        "20261009_0037",
        "20261009_0038",
        "20261009_0039",
        "20261009_0040",
        "20261009_0041",
        "20261009_0042",
        "20261009_0043",
        "20261009_0044",
        "20261009_0045",
        "20261009_0046",
        "20261009_0047",
        "20261009_0048",
        "20261009_0049",
        "20261009_0050",
        "20261009_0051",
        "20261009_0052",
        "20261014_0053",
        "20261014_0054",
        "20261014_0055",
        "20261014_0056",
        "20261014_0057",
        "20261014_0058",
        "20261014_0059",
        "20261014_0060",
        "20261014_0061",
        "20261014_0062",
        "20261015_0063",
        "20261021_0064",
        "20261021_0065",
        "20261022_0066",
        "20261022_0067",
        "20261022_0068",
        "20261022_0069",
        "20261022_0070",
        "20261022_0071",
        "20261022_0072",
        "20261022_0073",
        "20261022_0074",
        "20261022_0075",
        "20261022_0076",
        "20261022_0077",
        "20261027_0078",
        "20261028_0079",
        "20261028_0080",
    ]
    assert revisions[5].down_revision == "20260825_0005"


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("duplicate", "duplicate revision duplicate"),
        ("dangling", "revision child has unknown down_revision absent"),
        ("two-heads", "multiple migration heads: left, right"),
        ("file-order", "filename order disagrees with chain at revision child"),
    ),
)
def test_invalid_chains_fail_before_connecting_to_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected: str,
) -> None:
    if case == "duplicate":
        _write_revision(tmp_path, "0001_first.py", "duplicate", None)
        _write_revision(tmp_path, "0002_second.py", "duplicate", None)
    elif case == "dangling":
        _write_revision(tmp_path, "0001_child.py", "child", "absent")
    elif case == "two-heads":
        _write_revision(tmp_path, "0001_root.py", "root", None)
        _write_revision(tmp_path, "0002_left.py", "left", "root")
        _write_revision(tmp_path, "0003_right.py", "right", "root")
    else:
        _write_revision(tmp_path, "0001_child.py", "child", "root")
        _write_revision(tmp_path, "0002_root.py", "root", None)

    def unexpected_connection(*args: object, **kwargs: object) -> object:
        pytest.fail("chain validation must finish before any database connection")

    monkeypatch.setenv("MIGRATIONS_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://runner:private@database.invalid/flo")
    monkeypatch.setattr(migrate.psycopg, "connect", unexpected_connection)

    assert migrate.main([]) == 1
    output = capsys.readouterr().err
    assert expected in output
    assert "private" not in output
    assert "postgresql://" not in output


def test_apply_to_head_second_run_and_reverse_downgrade(
    empty_database: str,
) -> None:
    revisions = migrate.discover_migrations(MIGRATIONS)
    expected = tuple(item.revision for item in revisions)

    assert migrate.apply_migrations(revisions, empty_database) == expected
    assert migrate.apply_migrations(revisions, empty_database) == ()

    with psycopg.connect(empty_database, autocommit=True) as connection:
        recorded = tuple(
            row[0]
            for row in connection.execute(
                "SELECT revision FROM schema_migration ORDER BY applied_at, revision"
            ).fetchall()
        )
        assert recorded == expected
        for item in reversed(revisions):
            with connection.transaction():
                item.downgrade(connection)  # type: ignore[arg-type]
        remaining = connection.execute(
            "SELECT relname FROM pg_class "
            "JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
            "WHERE nspname = 'public' AND relkind IN ('r', 'p', 'S', 'v', 'm', 'f') "
            "ORDER BY relname"
        ).fetchall()
        assert remaining == [("schema_migration",)]


def test_failed_revision_rolls_back_only_that_revision(
    empty_database: str,
    tmp_path: Path,
) -> None:
    _write_revision(
        tmp_path,
        "0001_stable.py",
        "stable",
        None,
        'connection.execute("CREATE TABLE stable_table (id integer PRIMARY KEY)")',
    )
    _write_revision(
        tmp_path,
        "0002_failing.py",
        "failing",
        "stable",
        """
        connection.execute("CREATE TABLE failing_table (id integer PRIMARY KEY)")
        raise RuntimeError("sensitive database detail")
        """,
    )
    revisions = migrate.discover_migrations(tmp_path)

    with pytest.raises(migrate.RevisionApplyError) as failure:
        migrate.apply_migrations(revisions, empty_database)
    assert failure.value.revision == "failing"
    assert failure.value.exception_type == "RuntimeError"

    with psycopg.connect(empty_database, autocommit=True) as connection:
        assert connection.execute("SELECT to_regclass('public.stable_table')").fetchone() == (
            "stable_table",
        )
        assert connection.execute("SELECT to_regclass('public.failing_table')").fetchone() == (
            None,
        )
        assert connection.execute(
            "SELECT revision FROM schema_migration ORDER BY revision"
        ).fetchall() == [("stable",)]


def test_failure_output_names_only_revision_and_exception_type(
    empty_database: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    password = urlsplit(empty_database).password
    planted_secret = f"never-print-this {empty_database} {password}"
    _write_revision(
        tmp_path,
        "0001_secret.py",
        "secret_failure",
        None,
        f"raise RuntimeError({planted_secret!r})",
    )
    monkeypatch.setenv("MIGRATIONS_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", empty_database)

    assert migrate.main([]) == 1
    output = capsys.readouterr().err
    assert output == "migration secret_failure failed: RuntimeError\n"
    assert empty_database not in output
    assert "never-print-this" not in output
    if password is not None:
        assert password not in output


def test_applied_revision_missing_from_image_is_rejected_without_secrets(
    empty_database: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_revision(tmp_path, "0001_known.py", "known", None)
    with psycopg.connect(empty_database, autocommit=True) as connection:
        connection.execute(
            "CREATE TABLE schema_migration ("
            "revision text PRIMARY KEY, "
            "applied_at timestamptz NOT NULL DEFAULT now())"
        )
        connection.execute("INSERT INTO schema_migration (revision) VALUES ('newer_than_image')")
    monkeypatch.setenv("MIGRATIONS_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", empty_database)

    assert migrate.main([]) == 1
    output = capsys.readouterr().err
    assert output == "migration refused: database contains unknown revision newer_than_image\n"
    assert empty_database not in output


def test_two_runner_processes_apply_every_real_revision_once(
    empty_database: str,
) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = empty_database
    environment["MIGRATIONS_DIR"] = str(MIGRATIONS)
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "flo.kernel.migrate"],
            cwd=ROOT / "apps" / "api",
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=30) for process in processes]

    for process, (stdout, stderr) in zip(processes, results, strict=True):
        assert process.returncode == 0, stdout + stderr
    with psycopg.connect(empty_database, autocommit=True) as connection:
        assert connection.execute("SELECT count(*) FROM schema_migration").fetchone() == (
            len(migrate.discover_migrations(MIGRATIONS)),
        )


def test_check_validates_without_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_connection(*args: object, **kwargs: object) -> object:
        pytest.fail("--check must not connect to the database")

    monkeypatch.setenv("MIGRATIONS_DIR", str(MIGRATIONS))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(migrate.psycopg, "connect", unexpected_connection)

    assert migrate.main(["--check"]) == 0


def test_runner_disables_server_side_prepared_statements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect = MagicMock()
    connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(migrate.psycopg, "connect", connect)

    assert migrate.apply_migrations((), "postgresql+psycopg://database.invalid/flo") == ()
    connect.assert_called_once_with("postgresql://database.invalid/flo", prepare_threshold=None)


def test_invitation_migration_preserves_seed_and_database_guards(empty_database):
    chain = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0031"
    )
    migrate.apply_migrations(chain[:-1], empty_database)
    from psycopg.errors import CheckViolation, UniqueViolation

    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import create

    with psycopg.connect(empty_database, autocommit=True) as connection:
        organization = create(connection)
        before = connection.execute("SELECT * FROM identity ORDER BY id").fetchall()
        chain[-1].upgrade(connection)
        actor = before[0][0]
        invitation = uuid4()
        with tenant_transaction(connection, Scope(organization.org_id)):
            connection.execute(
                "INSERT INTO "
                "invitation(id,org_id,email,role_code,scope_type,"
                "invited_by,sent_at,expires_at) "
                "VALUES (%s,%s,'person@example.test','executive-viewer','org',%s,"
                "now(),now()+interval '7 days')",
                (invitation, organization.org_id, actor),
            )
            with pytest.raises(UniqueViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO "
                    "invitation(id,org_id,email,role_code,scope_type,"
                    "invited_by,sent_at,expires_at) "
                    "VALUES (%s,%s,'PERSON@example.test','executive-viewer','org',%s,"
                    "now(),now()+interval '7 days')",
                    (uuid4(), organization.org_id, actor),
                )
            with pytest.raises(CheckViolation), connection.transaction():
                connection.execute(
                    "UPDATE invitation SET "
                    "used_at=now(),used_by=%s,withdrawn_at=now(),withdrawn_by=%s WHERE id=%s",
                    (actor, actor, invitation),
                )
            for assignment in (
                "scope_type='invalid'",
                "resend_counter=-1",
                "used_at=now()",
                "withdrawn_at=now()",
            ):
                with pytest.raises(CheckViolation), connection.transaction():
                    connection.execute(
                        f"UPDATE invitation SET {assignment} WHERE id=%s", (invitation,)
                    )
            with pytest.raises(CheckViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO invitation_token VALUES ('plaintext',%s,%s)",
                    (invitation, organization.org_id),
                )
        chain[-1].downgrade(connection)
        assert connection.execute("SELECT * FROM identity ORDER BY id").fetchall() == before
        assert connection.execute(
            "SELECT to_regclass('invitation'), to_regclass('invitation_token')"
        ).fetchone() == (None, None)
        chain[-1].upgrade(connection)
        assert connection.execute("SELECT * FROM identity ORDER BY id").fetchall() == before


def test_project_schedule_migration_preserves_existing_project(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import MasterCreate, OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    chain = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0032"
    )
    migrate.apply_migrations(chain[:-1], empty_database)
    with psycopg.connect(empty_database, autocommit=True) as conn:
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        with correlation_context("schedule-migration"), tenant_transaction(conn, scope):
            org = OrgService(conn, scope, actor)
            bu = org.create_unit(OrgUnitCreate(code="BU", name="BU", kind="bu"))
            for kind, code in (("department", "D"), ("ledger_account", "L")):
                org.create_master(
                    kind,
                    MasterCreate(
                        code=code,
                        name=code,
                        effective_from=date(2000, 1, 1),
                        attributes={"account_type": "expense"} if kind == "ledger_account" else {},
                    ),
                )
            project_id = uuid4()
            conn.execute(
                "INSERT INTO project (id,org_id,bu_id,number,name,owner_id,department_code,"
                "ledger_account_code,currency,created_by) VALUES (%s,%s,%s,'P','Seed',%s,"
                "'D','L','USD',%s)",
                (project_id, tenant.org_id, bu.id, actor, actor),
            )
            before = conn.execute("SELECT * FROM project").fetchall()
        chain[-1].upgrade(conn)
        assert conn.execute(
            "SELECT health,percent_complete,actual_start,actual_end FROM project"
        ).fetchone() == ("unknown", 0, None, None)
        assert conn.execute(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('project_phase','project_milestone') ORDER BY relname"
        ).fetchall() == [("project_milestone", True, True), ("project_phase", True, True)]
        chain[-1].downgrade(conn)
        assert conn.execute("SELECT * FROM project").fetchall() == before
        assert conn.execute(
            "SELECT to_regclass('project_phase'),to_regclass('project_milestone')"
        ).fetchone() == (None, None)
        chain[-1].upgrade(conn)
        assert conn.execute("SELECT name FROM project WHERE id=%s", (project_id,)).fetchone() == (
            "Seed",
        )


def test_project_risk_migration_preserves_existing_project(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import MasterCreate, OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    chain = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0033"
    )
    migrate.apply_migrations(chain[:-1], empty_database)
    with psycopg.connect(empty_database, autocommit=True) as conn:
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        with correlation_context("risk-migration"), tenant_transaction(conn, scope):
            org = OrgService(conn, scope, actor)
            bu = org.create_unit(OrgUnitCreate(code="BU", name="BU", kind="bu"))
            for kind, code in (("department", "D"), ("ledger_account", "L")):
                org.create_master(
                    kind,
                    MasterCreate(
                        code=code,
                        name=code,
                        effective_from=date(2000, 1, 1),
                        attributes={"account_type": "expense"} if kind == "ledger_account" else {},
                    ),
                )
            project_id = uuid4()
            conn.execute(
                "INSERT INTO project (id,org_id,bu_id,number,name,owner_id,department_code,"
                "ledger_account_code,currency,created_by) VALUES (%s,%s,%s,'P','Seed',%s,"
                "'D','L','USD',%s)",
                (project_id, tenant.org_id, bu.id, actor, actor),
            )
            before = conn.execute("SELECT * FROM project").fetchall()
            seeds = {
                table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                for table in ("identity", "organization")
            }
        chain[-1].upgrade(conn)
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname='project_risk'"
        ).fetchone() == (True, True)
        assert conn.execute("SELECT * FROM project").fetchall() == before
        chain[-1].downgrade(conn)
        assert conn.execute("SELECT * FROM project").fetchall() == before
        assert conn.execute("SELECT to_regclass('project_risk')").fetchone() == (None,)
        chain[-1].upgrade(conn)
        for table, rows in seeds.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == rows
        assert conn.execute("SELECT name FROM project WHERE id=%s", (project_id,)).fetchone() == (
            "Seed",
        )


def test_import_rows_migration_preserves_batch_and_enforces_tenant_guards(empty_database):
    from psycopg.errors import CheckViolation, ForeignKeyViolation, InsufficientPrivilege

    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import admin_id, create

    chain = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0034"
    )
    migrate.apply_migrations(chain[:-1], empty_database)
    with psycopg.connect(empty_database, autocommit=True) as conn:
        tenant = create(conn)
        other = create(conn, "SECOND", "second@example.test")
        actor = admin_id(conn)
        batch = uuid4()
        with tenant_transaction(conn, Scope(tenant.org_id)):
            conn.execute(
                "INSERT INTO import_batch(id,org_id,template,template_version,uploader_id,"
                "file_name,file_sha256,file_size,mapping,counts) "
                "VALUES(%s,%s,'probe',1,%s,'data.csv',%s,1,'{}','{}')",
                (batch, tenant.org_id, actor, "a" * 64),
            )
            before = conn.execute("SELECT * FROM import_batch").fetchall()
        chain[-1].upgrade(conn)
        assert conn.execute(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('import_row','import_result') ORDER BY relname"
        ).fetchall() == [("import_result", True, True), ("import_row", True, True)]
        with tenant_transaction(conn, Scope(tenant.org_id)):
            conn.execute(
                "INSERT INTO import_row(org_id,batch_id,row_no,action,raw) "
                "VALUES(%s,%s,2,'create','{}')",
                (tenant.org_id, batch),
            )
            with pytest.raises(CheckViolation), conn.transaction():
                conn.execute("UPDATE import_row SET action='warning' WHERE batch_id=%s", (batch,))
            for table, columns, values in (
                ("import_row", "action,raw", "'create','{}'"),
                ("import_result", "record_type,record_id", "'probe',gen_random_uuid()"),
            ):
                with pytest.raises(ForeignKeyViolation), conn.transaction():
                    conn.execute(
                        f"INSERT INTO {table}(org_id,batch_id,row_no,{columns}) "
                        f"VALUES(%s,%s,3,{values})",
                        (other.org_id, batch),
                    )
        role = "import_row_rls_" + uuid4().hex
        conn.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
        )
        try:
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            conn.execute(
                sql.SQL("GRANT SELECT,INSERT ON import_row,import_result TO {}").format(
                    sql.Identifier(role)
                )
            )
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            with tenant_transaction(conn, Scope(other.org_id)):
                assert conn.execute("SELECT * FROM import_row").fetchall() == []
                assert conn.execute("SELECT * FROM import_result").fetchall() == []
            for table, columns, values in (
                ("import_row", "action,raw", "'create','{}'"),
                ("import_result", "record_type,record_id", "'probe',gen_random_uuid()"),
            ):
                with pytest.raises(InsufficientPrivilege):
                    with tenant_transaction(conn, Scope(other.org_id)):
                        conn.execute(
                            f"INSERT INTO {table}(org_id,batch_id,row_no,{columns}) "
                            f"VALUES(%s,%s,4,{values})",
                            (tenant.org_id, batch),
                        )
        finally:
            conn.execute("RESET ROLE")
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        chain[-1].downgrade(conn)
        assert conn.execute("SELECT * FROM import_batch").fetchall() == before
        assert conn.execute(
            "SELECT to_regclass('import_row'),to_regclass('import_result')"
        ).fetchone() == (None, None)
        chain[-1].upgrade(conn)
        assert conn.execute("SELECT id FROM import_batch").fetchall() == [(batch,)]


def test_import_async_migration_preserves_data_and_plants_key_guards(empty_database):
    from psycopg.errors import CheckViolation, UniqueViolation

    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import admin_id, create

    chain = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0035"
    )
    migrate.apply_migrations(chain[:-1], empty_database)
    with psycopg.connect(empty_database, autocommit=True) as conn:
        tenant = create(conn)
        actor = admin_id(conn)
        id = uuid4()
        with tenant_transaction(conn, Scope(tenant.org_id)):
            conn.execute(
                "INSERT INTO import_batch(id,org_id,template,template_version,"
                "uploader_id,"
                "file_name,file_sha256,file_size,counts) "
                "VALUES(%s,%s,'probe',1,%s,'seed.csv',%s,1,'{}')",
                (id, tenant.org_id, actor, "a" * 64),
            )
            before = conn.execute("SELECT * FROM import_batch").fetchall()
        chain[-1].upgrade(conn)
        with tenant_transaction(conn, Scope(tenant.org_id)):
            assert conn.execute(
                "SELECT external_key,progress,cancel_requested,error_class "
                "FROM import_batch WHERE id=%s",
                (id,),
            ).fetchone() == (None, {}, False, None)
            for key in ("", "k" * 129):
                with pytest.raises(CheckViolation), conn.transaction():
                    conn.execute("UPDATE import_batch SET external_key=%s WHERE id=%s", (key, id))
            conn.execute("UPDATE import_batch SET external_key='seed' WHERE id=%s", (id,))
            with pytest.raises(UniqueViolation), conn.transaction():
                conn.execute(
                    "INSERT INTO import_batch(id,org_id,template,template_version,"
                    "uploader_id,"
                    "file_name,file_sha256,file_size,counts,external_key) "
                    "VALUES(%s,%s,'probe',1,%s,'other.csv',%s,1,'{}','seed')",
                    (uuid4(), tenant.org_id, actor, "b" * 64),
                )
        chain[-1].downgrade(conn)
        with tenant_transaction(conn, Scope(tenant.org_id)):
            assert conn.execute("SELECT * FROM import_batch").fetchall() == before
        chain[-1].upgrade(conn)
        with tenant_transaction(conn, Scope(tenant.org_id)):
            assert conn.execute("SELECT id FROM import_batch").fetchall() == [(id,)]


def test_workflow_permissions_roundtrip_preserves_definition_and_versions(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.modules.approvals.schemas import WorkflowCreate, WorkflowVersionWrite
    from flo.modules.approvals.service import WorkflowService
    from tests.org.test_bootstrap import admin_id, create

    revisions = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0038"
    )
    migrate.apply_migrations(revisions, empty_database)
    with psycopg.connect(empty_database, autocommit=True) as conn:
        org = create(conn)
        with correlation_context("workflow-migration"):
            service = WorkflowService(conn, Scope(org.org_id), admin_id(conn))
            row = service.create(
                WorkflowCreate(document_type="project", name="Review", scope_type="org")
            )
            graph = {
                "nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
                "edges": [{"from": "start", "to": "end"}],
            }
            service.create_version(row.id, WorkflowVersionWrite(graph=graph))
            service.publish(row.id, 1)
            service.create_version(row.id, WorkflowVersionWrite(graph=graph))
        before = {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in ("workflow_definition", "workflow_version", "identity", "organization")
        }
        # Permissions are independently reversible without removing stored routing versions.
        revisions[-1].downgrade(conn)
        assert (
            conn.execute("SELECT code FROM permission WHERE code LIKE 'workflow.%'").fetchall()
            == []
        )
        revisions[-1].upgrade(conn)
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == rows
        # Table downgrade drops only this story's tables and leaves tenant/identity data intact.
        revisions[-1].downgrade(conn)
        revisions[-2].downgrade(conn)
        for table in ("identity", "organization"):
            assert conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == before[table]
        revisions[-2].upgrade(conn)
        revisions[-1].upgrade(conn)
        assert conn.execute("SELECT count(*) FROM workflow_version").fetchone() == (0,)


def test_requisition_migration_roundtrip_preserves_existing_data_and_plants_guards(
    empty_database,
):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import MasterCreate, OrgUnitCreate
    from flo.modules.org.service import OrgService
    from flo.modules.requisitions.schemas import RequisitionCreate
    from flo.modules.requisitions.service import RequisitionService
    from tests.org.test_bootstrap import admin_id, create, settings

    revisions = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0039"
    )
    migrate.apply_migrations(revisions[:-1], empty_database)
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("requisition-migration"),
    ):
        # ProjectService reads project_sponsor; this old schema predates it (E22-S01).
        conn.execute("CREATE TABLE project_sponsor(org_id uuid, project_id uuid, user_id uuid)")
        # ProjectService reads project.requested_budget; this old schema predates it (E22-S01).
        next(
            r for r in migrate.discover_migrations(MIGRATIONS) if r.revision == "20261015_0063"
        ).upgrade(conn)
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        org = OrgService(conn, scope, actor)
        bu = org.create_unit(OrgUnitCreate(code="BU", name="BU", kind="bu"))
        for kind, code in [("department", "D"), ("ledger_account", "L")]:
            org.create_master(
                kind,
                MasterCreate(
                    code=code,
                    name=code,
                    effective_from=date(2000, 1, 1),
                    attributes={"account_type": "expense"} if kind == "ledger_account" else {},
                ),
            )
        from types import SimpleNamespace

        project = SimpleNamespace(id=uuid4())
        second_project = SimpleNamespace(id=uuid4())
        with tenant_transaction(conn, scope):
            for index, row in enumerate((project, second_project)):
                conn.execute(
                    "INSERT INTO project(id,org_id,bu_id,number,name,owner_id,"
                    "department_code,ledger_account_code,currency,created_by) "
                    "VALUES (%s,%s,%s,%s,'Seed',%s,'D','L','USD',%s)",
                    (row.id, tenant.org_id, bu.id, f"PRJ-{index}", actor, actor),
                )
        foreign = create(conn, "SECOND", "second@example.test")
        conn.execute("ALTER TABLE identity ADD COLUMN IF NOT EXISTS display_name text")  # E22-S07
        before = conn.execute("SELECT * FROM project ORDER BY id").fetchall()
        tenant_rows = {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in ("organization", "identity")
        }
        revisions[-1].upgrade(conn)
        req = RequisitionService(conn, scope, actor, settings(conn)).create(
            RequisitionCreate(title="Seed", project_id=project.id)
        )
        conn.execute(
            "UPDATE project SET generated_by_requisition_id=%s WHERE id=%s",
            (req.id, project.id),
        )
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(
                "UPDATE project SET generated_by_requisition_id=%s WHERE id=%s",
                (req.id, second_project.id),
            )
        role = "requisition_probe_" + uuid4().hex
        from psycopg import sql

        conn.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
        )
        try:
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            conn.execute(
                sql.SQL("GRANT SELECT,INSERT,UPDATE ON requisition TO {}").format(
                    sql.Identifier(role)
                )
            )
            conn.execute(
                sql.SQL("ALTER TABLE requisition OWNER TO {}").format(sql.Identifier(role))
            )
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            with tenant_transaction(conn, scope):
                assert conn.execute("SELECT id FROM requisition").fetchall() == [(req.id,)]
            with tenant_transaction(conn, Scope(foreign.org_id)):
                assert conn.execute("SELECT id FROM requisition").fetchall() == []
            with (
                pytest.raises(psycopg.errors.InsufficientPrivilege),
                tenant_transaction(conn, scope),
            ):
                conn.execute(
                    "INSERT INTO requisition SELECT "
                    "(jsonb_populate_record(NULL::requisition,to_jsonb(r) || "
                    "jsonb_build_object('id',gen_random_uuid(),'org_id',%s::uuid))).* "
                    "FROM requisition r WHERE r.id=%s",
                    (foreign.org_id, req.id),
                )
        finally:
            conn.execute("RESET ROLE")
            conn.execute(
                sql.SQL("ALTER TABLE requisition OWNER TO {}").format(
                    sql.Identifier(conn.info.user)
                )
            )
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        saved_req = conn.execute("SELECT * FROM requisition ORDER BY id").fetchall()
        saved_project = conn.execute("SELECT * FROM project ORDER BY id").fetchall()
        for statement, error in [
            ("UPDATE requisition SET status='invented'", psycopg.errors.CheckViolation),
            ("UPDATE requisition SET estimate=-1", psycopg.errors.CheckViolation),
            (
                "UPDATE requisition SET standalone=false,project_id=NULL",
                psycopg.errors.CheckViolation,
            ),
            (
                "UPDATE requisition SET project_id=gen_random_uuid()",
                psycopg.errors.ForeignKeyViolation,
            ),
            (
                "UPDATE project SET generated_by_requisition_id=gen_random_uuid()",
                psycopg.errors.ForeignKeyViolation,
            ),
        ]:
            with pytest.raises(error), conn.transaction():
                conn.execute(statement)
        for status in (
            "draft",
            "approval_pending",
            "sourcing_in_progress",
            "sourced",
            "awarded",
            "open_for_purchase",
            "completed",
            "rejected",
            "withdrawn",
            "cancelled",
            "expired",
        ):
            with conn.transaction():
                conn.execute("UPDATE requisition SET status=%s", (status,))
        conn.execute("UPDATE requisition SET status='draft'")

        # A rolled-back downgrade preserves the new documents and provenance byte-for-byte.
        class Rollback(Exception):
            pass

        with pytest.raises(Rollback), conn.transaction():
            revisions[-1].downgrade(conn)
            assert conn.execute("SELECT to_regclass('requisition')").fetchone() == (None,)
            raise Rollback
        assert conn.execute("SELECT * FROM requisition ORDER BY id").fetchall() == saved_req
        assert conn.execute("SELECT * FROM project ORDER BY id").fetchall() == saved_project
        # Committed removal of a new table preserves the pre-story tenant/project rows.
        revisions[-1].downgrade(conn)
        assert conn.execute("SELECT * FROM project ORDER BY id").fetchall() == before
        for table, rows in tenant_rows.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == rows
        revisions[-1].upgrade(conn)
        assert conn.execute("SELECT * FROM requisition ORDER BY id").fetchall() == []
        assert conn.execute("SELECT generated_by_requisition_id FROM project").fetchall() == [
            (None,),
            (None,),
        ]


def test_collaboration_migration_roundtrip_preserves_data_and_plants_guards(empty_database):
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import admin_id, create

    revisions = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0041"
    )
    migrate.apply_migrations(revisions[:-1], empty_database)
    with psycopg.connect(empty_database, autocommit=True) as conn:
        tenant = create(conn)
        other = create(conn, "OTHER", "other@example.test")
        actor = admin_id(conn)
        before = {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in ("organization", "identity", "project", "requisition")
        }
        revisions[-1].upgrade(conn)
        note, attachment, version = uuid4(), uuid4(), uuid4()
        with tenant_transaction(conn, Scope(tenant.org_id)):
            conn.execute(
                "INSERT INTO note(id,org_id,document_type,document_id,author_id,body,visibility) "
                "VALUES(%s,%s,'project',%s,%s,'seed','internal')",
                (note, tenant.org_id, uuid4(), actor),
            )
            conn.execute(
                "INSERT INTO note_revision(org_id,note_id,body,visibility,edited_by) "
                "VALUES(%s,%s,'prior','internal',%s)",
                (tenant.org_id, note, actor),
            )
            conn.execute(
                "INSERT INTO attachment(id,org_id,document_type,document_id,created_by) "
                "VALUES(%s,%s,'project',%s,%s)",
                (attachment, tenant.org_id, uuid4(), actor),
            )
            conn.execute(
                "INSERT INTO attachment_version(id,org_id,attachment_id,version,original_filename,"
                "display_name,storage_key,media_type,size_bytes,sha256,scan_status,uploaded_by) "
                "VALUES(%s,%s,%s,1,'seed.pdf','seed.pdf',%s,'application/pdf',5,%s,'pending',%s)",
                (version, tenant.org_id, attachment, str(uuid4()), "a" * 64, actor),
            )
        tables = ("note", "note_revision", "attachment", "attachment_version")
        role = "collaboration_probe_" + uuid4().hex
        conn.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
        )
        try:
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            conn.execute(
                sql.SQL(
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON "
                    "note,note_revision,attachment,attachment_version TO {}"
                ).format(sql.Identifier(role))
            )
            conn.execute(
                sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                    sql.Identifier(role)
                )
            )
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            with tenant_transaction(conn, Scope(tenant.org_id)):
                conn.execute("UPDATE attachment_version SET scan_status='clean'")
                for statement in (
                    "DELETE FROM attachment_version",
                    "UPDATE attachment_version SET media_type='text/plain'",
                    "DELETE FROM note_revision",
                    "UPDATE note_revision SET body='tamper'",
                ):
                    with (
                        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
                        conn.transaction(),
                    ):
                        conn.execute(statement)
                for statement in (
                    "UPDATE attachment_version SET size_bytes=0",
                    "UPDATE attachment_version SET scan_status='invented'",
                ):
                    with (
                        pytest.raises(
                            (
                                psycopg.errors.ObjectNotInPrerequisiteState,
                                psycopg.errors.CheckViolation,
                            )
                        ),
                        conn.transaction(),
                    ):
                        conn.execute(statement)
                for table in tables:
                    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                        conn.execute(
                            f"INSERT INTO {table} SELECT "
                            f"(jsonb_populate_record(NULL::{table},to_jsonb(t) || "
                            "jsonb_build_object('org_id',%s::uuid))).* "
                            f"FROM {table} t",
                            (other.org_id,),
                        )
            with tenant_transaction(conn, Scope(other.org_id)):
                for table in tables:
                    assert conn.execute(f"SELECT * FROM {table}").fetchall() == []
        finally:
            conn.execute("RESET ROLE")
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        saved = {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() for table in tables
        }

        class Rollback(Exception):
            pass

        with pytest.raises(Rollback), conn.transaction():
            revisions[-1].downgrade(conn)
            assert conn.execute("SELECT to_regclass('note')").fetchone() == (None,)
            raise Rollback
        for table, rows in saved.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == rows
        revisions[-1].downgrade(conn)
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == rows
        revisions[-1].upgrade(conn)
        # New-table downgrade removes story data; replaying the retained snapshot proves
        # the same notes, revisions and versions survive serialization and re-creation.
        from psycopg.types.json import Jsonb

        for table in tables:
            columns = [
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s ORDER BY ordinal_position",
                    (table,),
                ).fetchall()
            ]
            for row in saved[table]:
                values = {
                    k: str(v) if v is not None else None for k, v in zip(columns, row, strict=True)
                }
                conn.execute(
                    f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table},%s)).*",
                    (Jsonb(values),),
                )
            assert conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == saved[table]


def test_requisition_lines_migration_roundtrip_preserves_lines_and_header(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import BaseCurrencyPut, MasterCreate, OrgUnitCreate
    from flo.modules.org.service import OrgService
    from flo.modules.requisitions.schemas import RequisitionCreate
    from flo.modules.requisitions.service import RequisitionService
    from tests.org.test_bootstrap import admin_id, create, settings

    revisions = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0040"
    )
    migrate.apply_migrations(revisions[:-1], empty_database)
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("line-migration"),
    ):
        tenant = create(conn)
        scope = Scope(tenant.org_id)
        actor = admin_id(conn)
        org = OrgService(conn, scope, actor)
        org.set_base_currency(BaseCurrencyPut(currency="USD"))
        bu = org.create_unit(OrgUnitCreate(code="BU", name="BU", kind="bu"))
        requisitions = RequisitionService(conn, scope, actor, settings(conn))
        conn.execute("ALTER TABLE identity ADD COLUMN IF NOT EXISTS display_name text")  # E22-S07
        row = legacy_draft(
            lambda: requisitions.create(RequisitionCreate(title="Preserved", bu_id=bu.id))
        )
        before = conn.execute(
            "SELECT id,title,estimate,currency,version FROM requisition"
        ).fetchall()
        revisions[-1].upgrade(conn)
        assert (
            conn.execute("SELECT id,title,estimate,currency,version FROM requisition").fetchall()
            == before
        )
        org.create_master(
            "uom",
            MasterCreate(
                code="EA",
                name="Each",
                effective_from=date(2000, 1, 1),
                attributes={"dimension": "count"},
            ),
        )
        # Written with SQL: the service now also selects columns later revisions add.
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO requisition_line (id,org_id,requisition_id,line_no,line_type,"
                "description,quantity,uom_code,unit_price,amount,currency) VALUES "
                "(gen_random_uuid(),%s,%s,1,'item','Pump',3,'EA',0.10,0.30,'USD')",
                (scope.org_id, row.id),
            )
            conn.execute(
                "UPDATE requisition SET estimate=0.30,version=2,last_line_no=1 WHERE id=%s",
                (row.id,),
            )
        saved = conn.execute("SELECT * FROM requisition_line").fetchall()
        header = conn.execute("SELECT * FROM requisition").fetchall()

        class Rollback(Exception):
            pass

        with pytest.raises(Rollback), conn.transaction():
            revisions[-1].downgrade(conn)
            revisions[-1].upgrade(conn)
            raise Rollback
        assert conn.execute("SELECT * FROM requisition_line").fetchall() == saved
        assert conn.execute("SELECT * FROM requisition").fetchall() == header
        # Removing the feature preserves all pre-existing header data. A rolled-back
        # down/up above proves existing line data survives an aborted migration.
        revisions[-1].downgrade(conn)
        assert conn.execute(
            "SELECT id,title,estimate,currency,version FROM requisition"
        ).fetchall() == [(row.id, "Preserved", Decimal("0.3000"), "USD", 2)]
        revisions[-1].upgrade(conn)
        assert (
            conn.execute("SELECT to_regclass('requisition_line')").fetchone()[0]
            == "requisition_line"
        )
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT last_line_no FROM requisition").fetchone() == (0,)


def test_catalogue_migration_roundtrip_preserves_items_events_and_lines(empty_database):
    from uuid import uuid4

    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import BaseCurrencyPut, MasterCreate, OrgUnitCreate
    from flo.modules.org.service import OrgService
    from flo.modules.requisitions.catalogue import record_event
    from flo.modules.requisitions.lines import LineService
    from flo.modules.requisitions.schemas import LineCreate, RequisitionCreate
    from flo.modules.requisitions.service import RequisitionService
    from tests.org.test_bootstrap import admin_id, create, settings

    revisions = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0042"
    )
    migrate.apply_migrations(revisions[:-1], empty_database)
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("catalogue-migration"),
    ):
        tenant = create(conn)
        scope = Scope(tenant.org_id)
        actor = admin_id(conn)
        org = OrgService(conn, scope, actor)
        org.set_base_currency(BaseCurrencyPut(currency="USD"))
        bu = org.create_unit(OrgUnitCreate(code="BU", name="BU", kind="bu"))
        org.create_master(
            "uom",
            MasterCreate(
                code="EA",
                name="Each",
                effective_from=date(2000, 1, 1),
                attributes={"dimension": "count"},
            ),
        )
        conn.execute("ALTER TABLE identity ADD COLUMN IF NOT EXISTS display_name text")  # E22-S07
        row = legacy_draft(
            lambda: RequisitionService(conn, scope, actor, settings(conn)).create(
                RequisitionCreate(title="Preserved", bu_id=bu.id)
            )
        )
        revisions[-1].upgrade(conn)
        LineService(conn, scope, actor).write(
            row.id,
            "1",
            body=LineCreate(
                line_type="item", description="Pump", quantity="3", unit_price="0.10", uom_code="EA"
            ),
        )
        lines_before = conn.execute(
            "SELECT id,line_no,description,quantity,unit_price,amount FROM requisition_line"
        ).fetchall()
        item = record_event(
            conn,
            scope,
            bu_id=bu.id,
            line_type="item",
            description="Pump",
            uom_code="EA",
            category_code=None,
            event="requested",
            source_type="requisition_line",
            source_id=uuid4(),
            quantity="3",
            unit_price="0.1000",
            currency="USD",
        )
        items = conn.execute("SELECT * FROM catalogue_item").fetchall()
        events = conn.execute("SELECT * FROM catalogue_event").fetchall()
        assert [r[0] for r in items] == [item] and len(events) == 1

        class Rollback(Exception):
            pass

        with pytest.raises(Rollback), conn.transaction():
            revisions[-1].downgrade(conn)
            revisions[-1].upgrade(conn)
            raise Rollback
        assert conn.execute("SELECT * FROM catalogue_item").fetchall() == items
        assert conn.execute("SELECT * FROM catalogue_event").fetchall() == events
        # Removing the feature keeps every pre-existing line and header.
        revisions[-1].downgrade(conn)
        assert (
            conn.execute(
                "SELECT id,line_no,description,quantity,unit_price,amount FROM requisition_line"
            ).fetchall()
            == lines_before
        )
        assert conn.execute("SELECT to_regclass('catalogue_item')").fetchone() == (None,)
        revisions[-1].upgrade(conn)
        assert conn.execute("SELECT to_regclass('catalogue_event')").fetchone()[0] == (
            "catalogue_event"
        )
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM catalogue_item").fetchone() == (0,)


def test_approval_instance_migration_roundtrip_preserves_running_instance_and_tasks(
    empty_database,
):
    from uuid import uuid4

    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.approvals.schemas import WorkflowCreate, WorkflowVersionWrite
    from flo.modules.approvals.service import WorkflowService
    from tests.org.test_bootstrap import admin_id, create

    revisions = tuple(
        r
        for r in migrate.discover_migrations(MIGRATIONS)
        if r.path.name.split("_", 2)[2].startswith(("approval_instances", "catalogue"))
        or r.revision <= "20261009_0041"
    )
    target = next(r for r in revisions if "approval_instances" in r.path.name)
    migrate.apply_migrations([r for r in revisions if r is not target], empty_database)
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("approval-instance-migration"),
    ):
        org = create(conn)
        actor = admin_id(conn)
        scope = Scope(org.org_id)
        graph = {
            "nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
            "edges": [{"from": "start", "to": "end"}],
        }
        service = WorkflowService(conn, scope, actor)
        row = service.create(
            WorkflowCreate(document_type="requisition", name="R", scope_type="org")
        )
        service.create_version(row.id, WorkflowVersionWrite(graph=graph))
        service.publish(row.id, 1)
        versions_before = conn.execute("SELECT * FROM workflow_version ORDER BY id").fetchall()
        target.upgrade(conn)
        version_id = versions_before[0][0]
        instance, task, document = uuid4(), uuid4(), uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO approval_instance(id,org_id,document_type,document_id,"
                "workflow_version_id,document_version,material_digest,requester_id,status,"
                "current_node_id) VALUES (%s,%s,'requisition',%s,%s,1,'digest',%s,'running','n1')",
                (instance, org.org_id, document, version_id, actor),
            )
            conn.execute(
                "INSERT INTO approval_task(id,org_id,instance_id,node_id,name,approver_rule,"
                "mode,allow_repeat,status) VALUES (%s,%s,%s,'n1','Finance','{}'::jsonb,"
                "'\"any\"'::jsonb,false,'open')",
                (task, org.org_id, instance),
            )
            instances = conn.execute("SELECT * FROM approval_instance").fetchall()
            tasks = conn.execute("SELECT * FROM approval_task").fetchall()

        class Rollback(Exception):
            pass

        with pytest.raises(Rollback), conn.transaction():
            target.downgrade(conn)
            target.upgrade(conn)
            raise Rollback
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT * FROM approval_instance").fetchall() == instances
            assert conn.execute("SELECT * FROM approval_task").fetchall() == tasks
        # The downgrade removes only this story's tables; workflow versions are untouched.
        target.downgrade(conn)
        assert conn.execute("SELECT * FROM workflow_version ORDER BY id").fetchall() == (
            versions_before
        )
        assert conn.execute("SELECT to_regclass('approval_instance')").fetchone() == (None,)
        target.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM approval_instance").fetchone() == (0,)


def test_approval_decisions_migration_roundtrip_preserves_seeded_instances(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.approvals.schemas import WorkflowCreate, WorkflowVersionWrite
    from flo.modules.approvals.service import WorkflowService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    revisions = tuple(r for r in revisions if r.revision <= "20261009_0044")
    target = revisions[-1]
    assert target.revision == "20261009_0044"
    migrate.apply_migrations(revisions[:-1], empty_database)
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("decision-migration"),
    ):
        org = create(conn)
        actor = admin_id(conn)
        scope = Scope(org.org_id)
        service = WorkflowService(conn, scope, actor)
        row = service.create(
            WorkflowCreate(document_type="requisition", name="R", scope_type="org")
        )
        service.create_version(
            row.id,
            WorkflowVersionWrite(
                graph={
                    "nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
                    "edges": [{"from": "start", "to": "end"}],
                }
            ),
        )
        version = service.publish(row.id, 1)
        instance, task = uuid4(), uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO approval_instance(id,org_id,document_type,document_id,"
                "workflow_version_id,document_version,material_digest,requester_id,status) "
                "VALUES(%s,%s,'requisition',%s,%s,1,'digest',%s,'running')",
                (instance, scope.org_id, uuid4(), version.id, actor),
            )
            conn.execute(
                "INSERT INTO approval_task(id,org_id,instance_id,node_id,name,approver_rule,"
                "mode,allow_repeat,status) VALUES(%s,%s,%s,'one','Finance','{}',"
                "'\"any\"',false,'open')",
                (task, scope.org_id, instance),
            )
            before = conn.execute("SELECT * FROM approval_instance").fetchall()
            tasks = conn.execute("SELECT * FROM approval_task").fetchall()
            target.upgrade(conn)
            conn.execute(
                "INSERT INTO approval_decision(id,org_id,instance_id,task_id,actor_id,authority,"
                "outcome,comment,document_version,members_at_decision) "
                "VALUES(%s,%s,%s,%s,%s,'member','comment','Evidence',1,'[]')",
                (uuid4(), scope.org_id, instance, task, actor),
            )
            assert conn.execute("SELECT count(*) FROM approval_decision").fetchone() == (1,)
            target.downgrade(conn)
            assert conn.execute("SELECT to_regclass('approval_decision')").fetchone() == (None,)
            assert conn.execute("SELECT * FROM approval_instance").fetchall() == before
            assert conn.execute("SELECT * FROM approval_task").fetchall() == tasks
            target.upgrade(conn)
            assert conn.execute("SELECT count(*) FROM approval_decision").fetchone() == (0,)
            assert conn.execute("SELECT * FROM approval_instance").fetchall() == before
            assert conn.execute("SELECT * FROM approval_task").fetchall() == tasks


def test_approval_material_migration_roundtrip_preserves_seeded_instances(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.approvals.schemas import WorkflowCreate, WorkflowVersionWrite
    from flo.modules.approvals.service import WorkflowService
    from tests.org.test_bootstrap import admin_id, create

    revisions = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0045"
    )
    target = revisions[-1]
    assert target.revision == "20261009_0045"
    migrate.apply_migrations(revisions[:-1], empty_database)
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("decision-migration"),
    ):
        org = create(conn)
        actor = admin_id(conn)
        scope = Scope(org.org_id)
        service = WorkflowService(conn, scope, actor)
        row = service.create(
            WorkflowCreate(document_type="requisition", name="R", scope_type="org")
        )
        service.create_version(
            row.id,
            WorkflowVersionWrite(
                graph={
                    "nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
                    "edges": [{"from": "start", "to": "end"}],
                }
            ),
        )
        version = service.publish(row.id, 1)
        instance, task = uuid4(), uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO approval_instance(id,org_id,document_type,document_id,"
                "workflow_version_id,document_version,material_digest,requester_id,status) "
                "VALUES(%s,%s,'requisition',%s,%s,1,'digest',%s,'running')",
                (instance, scope.org_id, uuid4(), version.id, actor),
            )
            conn.execute(
                "INSERT INTO approval_task(id,org_id,instance_id,node_id,name,approver_rule,"
                "mode,allow_repeat,status) VALUES(%s,%s,%s,'one','Finance','{}',"
                "'\"any\"',false,'open')",
                (task, scope.org_id, instance),
            )
            before = conn.execute("SELECT * FROM approval_instance").fetchall()
            tasks = conn.execute("SELECT * FROM approval_task").fetchall()
            target.upgrade(conn)
            assert conn.execute(
                "SELECT approved_material_digest FROM approval_instance"
            ).fetchone() == (None,)
            conn.execute(
                "UPDATE approval_instance SET approved_material_digest='approved-digest',"
                "approved_version=1 WHERE id=%s",
                (instance,),
            )
            target.downgrade(conn)
            assert conn.execute("SELECT * FROM approval_instance").fetchall() == before
            assert conn.execute("SELECT * FROM approval_task").fetchall() == tasks
            target.upgrade(conn)
            assert conn.execute(
                "SELECT approved_material_digest FROM approval_instance"
            ).fetchone() == (None,)
            assert conn.execute("SELECT * FROM approval_task").fetchall() == tasks


def test_delegation_migration_roundtrip_and_overlap_guard(empty_database):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.approvals.schemas import WorkflowCreate, WorkflowVersionWrite
    from flo.modules.approvals.service import WorkflowService
    from tests.org.test_bootstrap import admin_id, create

    revisions = tuple(
        r for r in migrate.discover_migrations(MIGRATIONS) if r.revision <= "20261009_0046"
    )
    target = revisions[-1]
    assert target.revision == "20261009_0046"
    migrate.apply_migrations(revisions[:-1], empty_database)
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("delegation-migration"),
    ):
        org = create(conn)
        owner = admin_id(conn)
        scope = Scope(org.org_id)
        service = WorkflowService(conn, scope, owner)
        row = service.create(
            WorkflowCreate(document_type="requisition", name="R", scope_type="org")
        )
        service.create_version(
            row.id,
            WorkflowVersionWrite(
                graph={
                    "nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
                    "edges": [{"from": "start", "to": "end"}],
                }
            ),
        )
        version = service.publish(row.id, 1)
        instance, task = uuid4(), uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO approval_instance(id,org_id,document_type,document_id,"
                "workflow_version_id,document_version,material_digest,requester_id,status) "
                "VALUES(%s,%s,'requisition',%s,%s,1,'digest',%s,'running')",
                (instance, scope.org_id, uuid4(), version.id, owner),
            )
            conn.execute(
                "INSERT INTO approval_task(id,org_id,instance_id,node_id,name,approver_rule,"
                "mode,allow_repeat,status) VALUES(%s,%s,%s,'one','Finance','{}',"
                "'\"any\"',false,'open')",
                (task, scope.org_id, instance),
            )
            tasks_before = conn.execute("SELECT * FROM approval_task ORDER BY id").fetchall()
        target.upgrade(conn)
        start = datetime(2026, 10, 12, tzinfo=UTC)
        other = uuid4()
        conn.execute(
            "INSERT INTO identity(id,email,password_hash) "
            "VALUES (%s,'d@example.test','$argon2id$test-placeholder')",
            (other,),
        )

        def insert(starts, ends):
            with tenant_transaction(conn, Scope(org.org_id)):
                conn.execute(
                    "INSERT INTO approval_delegation(id,org_id,delegator_id,delegate_id,"
                    "starts_at,ends_at,reason) VALUES (%s,%s,%s,%s,%s,%s,'away')",
                    (uuid4(), org.org_id, owner, other, starts, ends),
                )

        insert(start, start + timedelta(days=2))
        insert(start + timedelta(days=2), start + timedelta(days=4))  # back to back is fine
        with pytest.raises(psycopg.errors.ExclusionViolation):
            insert(start + timedelta(days=1), start + timedelta(days=3))
        with tenant_transaction(conn, Scope(org.org_id)):
            before = conn.execute("SELECT * FROM approval_delegation ORDER BY id").fetchall()
        target.downgrade(conn)
        assert conn.execute("SELECT to_regclass('approval_delegation')").fetchone() == (None,)
        target.upgrade(conn)
        with tenant_transaction(conn, Scope(org.org_id)):
            columns = [
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='approval_task' AND column_name NOT IN "
                    "('escalated_at','escalation_rule','reminded_on') ORDER BY ordinal_position"
                ).fetchall()
            ]
            assert (
                conn.execute(
                    sql.SQL("SELECT {} FROM approval_task ORDER BY id").format(
                        sql.SQL(",").join(map(sql.Identifier, columns))
                    )
                ).fetchall()
                == tasks_before
            )
            assert conn.execute("SELECT count(*) FROM approval_delegation").fetchone() == (0,)
        assert len(before) == 2


def test_requisition_lifecycle_permissions_roundtrip_preserves_existing_data(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import BaseCurrencyPut, OrgUnitCreate
    from flo.modules.org.service import OrgService
    from flo.modules.requisitions.schemas import RequisitionCreate
    from flo.modules.requisitions.service import RequisitionService
    from tests.org.test_bootstrap import admin_id, create, settings

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261009_0044"], empty_database
    )
    table = next(r for r in revisions if r.revision == "20261009_0048")
    permissions = next(r for r in revisions if r.revision == "20261009_0049")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("requisition-lifecycle-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        org = OrgService(conn, scope, actor)
        org.set_base_currency(BaseCurrencyPut(currency="USD"))
        bu = org.create_unit(OrgUnitCreate(code="BU", name="BU", kind="bu"))
        conn.execute("ALTER TABLE identity ADD COLUMN IF NOT EXISTS display_name text")  # E22-S07
        row = legacy_draft(
            lambda: RequisitionService(conn, scope, actor, settings(conn)).create(
                RequisitionCreate(title="Preserve", bu_id=bu.id)
            )
        )
        # The catalogue has the new codes, so model a pre-upgrade tenant explicitly.
        conn.execute(
            "DELETE FROM role_permission WHERE permission_code IN "
            "('requisition.submit','requisition.withdraw','requisition.cancel')"
        )
        conn.execute(
            "DELETE FROM permission WHERE code IN "
            "('requisition.submit','requisition.withdraw','requisition.cancel')"
        )
        before = conn.execute("SELECT id,title,status,version FROM requisition").fetchall()
        grants = conn.execute(
            "SELECT org_id,role_id,permission_code FROM role_permission ORDER BY 1,2,3"
        ).fetchall()
        for _ in range(2):
            table.upgrade(conn)
            permissions.upgrade(conn)
            with tenant_transaction(conn, scope):
                codes = {
                    r[0]
                    for r in conn.execute(
                        "SELECT rp.permission_code FROM role_permission rp JOIN role r "
                        "ON r.id=rp.role_id WHERE r.org_id=%s AND r.code='requestor'",
                        (tenant.org_id,),
                    ).fetchall()
                }
                assert {"requisition.submit", "requisition.withdraw"} <= codes
                assert "requisition.cancel" not in codes
                conn.execute(
                    "INSERT INTO requisition_status_event(org_id,requisition_id,from_status,"
                    "to_status,actor_id,funding_round) VALUES(%s,%s,'draft','withdrawn',%s,0)",
                    (tenant.org_id, row.id, actor),
                )
            assert (
                conn.execute("SELECT id,title,status,version FROM requisition").fetchall() == before
            )
            assert conn.execute("SELECT count(*) FROM requisition_status_event").fetchone() == (1,)
            saved_events = conn.execute("SELECT * FROM requisition_status_event").fetchall()
            saved_grants = conn.execute("SELECT * FROM role_permission ORDER BY 1,2,3").fetchall()
            with pytest.raises(RuntimeError, match="migration rollback plant"), conn.transaction():
                permissions.downgrade(conn)
                table.downgrade(conn)
                table.upgrade(conn)
                permissions.upgrade(conn)
                raise RuntimeError("migration rollback plant")
            assert conn.execute("SELECT * FROM requisition_status_event").fetchall() == saved_events
            assert (
                conn.execute("SELECT * FROM role_permission ORDER BY 1,2,3").fetchall()
                == saved_grants
            )
            permissions.downgrade(conn)
            table.downgrade(conn)
            assert (
                conn.execute("SELECT id,title,status,version FROM requisition").fetchall() == before
            )
            assert (
                conn.execute(
                    "SELECT org_id,role_id,permission_code FROM role_permission ORDER BY 1,2,3"
                ).fetchall()
                == grants
            )
        table.upgrade(conn)
        permissions.upgrade(conn)


def test_project_event_system_actor_roundtrip_preserves_data_and_refuses_unsafe_downgrade(
    empty_database,
):
    from uuid import uuid4

    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.modules.org.schemas import BaseCurrencyPut, OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261009_0050"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261009_0051")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("project-event-actor-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        org = OrgService(conn, Scope(tenant.org_id), actor)
        org.set_base_currency(BaseCurrencyPut(currency="USD"))
        bu = org.create_unit(OrgUnitCreate(code="BU", name="BU", kind="bu"))
        project_id = uuid4()
        conn.execute(
            "INSERT INTO project(id,org_id,bu_id,number,name,owner_id,department_code,"
            "ledger_account_code,currency,created_by) VALUES(%s,%s,%s,'P','P',%s,'D','L','USD',%s)",
            (project_id, tenant.org_id, bu.id, actor, actor),
        )
        conn.execute(
            "INSERT INTO project_status_event(org_id,project_id,from_status,to_status,actor_id)"
            " VALUES(%s,%s,'draft','abandoned',%s)",
            (tenant.org_id, project_id, actor),
        )
        before = conn.execute("SELECT id,project_id,actor_id FROM project_status_event").fetchall()
        mig.upgrade(conn)
        assert (
            conn.execute("SELECT id,project_id,actor_id FROM project_status_event").fetchall()
            == before
        )
        insert = (
            "INSERT INTO project_status_event(org_id,project_id,from_status,to_status,"
            "actor_id,system_actor) VALUES(%s,%s,'draft','abandoned',NULL,%s)"
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert, (tenant.org_id, project_id, None))
        conn.execute(insert, (tenant.org_id, project_id, "requisition-expiry"))
        with pytest.raises(RuntimeError, match="no actor_id"):
            mig.downgrade(conn)
        # The refused downgrade changed nothing: the system row survives.
        assert conn.execute(
            "SELECT count(*) FROM project_status_event WHERE system_actor IS NOT NULL"
        ).fetchone() == (1,)
        conn.execute("ALTER TABLE project_status_event DISABLE TRIGGER USER")
        conn.execute("DELETE FROM project_status_event WHERE actor_id IS NULL")
        conn.execute("ALTER TABLE project_status_event ENABLE TRIGGER USER")
        mig.downgrade(conn)
        assert (
            conn.execute("SELECT id,project_id,actor_id FROM project_status_event").fetchall()
            == before
        )
        mig.upgrade(conn)


def test_requisition_revisions_roundtrip_and_plants_the_immutability_guards(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import BaseCurrencyPut, OrgUnitCreate
    from flo.modules.org.service import OrgService
    from flo.modules.requisitions.schemas import RequisitionCreate
    from flo.modules.requisitions.service import RequisitionService
    from tests.org.test_bootstrap import admin_id, create, settings

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261009_0051"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261009_0052")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("requisition-revisions-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        org = OrgService(conn, scope, actor)
        org.set_base_currency(BaseCurrencyPut(currency="USD"))
        bu = org.create_unit(OrgUnitCreate(code="BU", name="BU", kind="bu"))
        conn.execute("ALTER TABLE identity ADD COLUMN IF NOT EXISTS display_name text")  # E22-S07
        req = legacy_draft(
            lambda: RequisitionService(conn, scope, actor, settings(conn)).create(
                RequisitionCreate(title="Seed", bu_id=bu.id)
            )
        )
        before = conn.execute("SELECT id,title,status,version FROM requisition").fetchall()
        mig.upgrade(conn)
        assert conn.execute("SELECT id,title,status,version FROM requisition").fetchall() == before
        insert = (
            "INSERT INTO requisition_revision(id,org_id,requisition_id,revision_no,kind,status,"
            "snapshot,material_digest,created_by) VALUES(gen_random_uuid(),%s,%s,%s,%s,%s,"
            "'{}'::jsonb,%s,%s)"
        )

        def run(statement, *args):
            with tenant_transaction(conn, scope):
                return conn.execute(statement, args).rowcount

        digest = "a" * 64
        run(insert, tenant.org_id, req.id, 1, "submission", "applied", digest, actor)
        run(insert, tenant.org_id, req.id, 2, "amendment", "draft", digest, actor)
        with pytest.raises(psycopg.errors.UniqueViolation):  # one open amendment, in the table
            run(insert, tenant.org_id, req.id, 3, "amendment", "pending", digest, actor)
        with pytest.raises(psycopg.errors.UniqueViolation):
            run(insert, tenant.org_id, req.id, 2, "submission", "applied", digest, actor)
        for refused in (
            "UPDATE requisition_revision SET snapshot='{\"x\":1}'::jsonb WHERE revision_no=1",
            "UPDATE requisition_revision SET status='withdrawn' WHERE revision_no=1",
            "DELETE FROM requisition_revision WHERE revision_no=1",
        ):
            with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
                run(refused)
        run("UPDATE requisition_revision SET snapshot='{\"x\":1}'::jsonb WHERE revision_no=2")
        run("UPDATE requisition_revision SET status='pending' WHERE revision_no=2")
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            run("UPDATE requisition_revision SET snapshot='{}'::jsonb WHERE revision_no=2")
        # A decided pending row may change nothing but status and decided_at.
        for rewrite in (
            "snapshot='{\"x\":2}'::jsonb",
            "note='rewritten'",
            "material_digest='" + "b" * 64 + "'",
        ):
            with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
                run(
                    "UPDATE requisition_revision SET status='applied',decided_at=now(),"
                    + rewrite
                    + " WHERE revision_no=2"
                )
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            run("DELETE FROM requisition_revision WHERE revision_no=2")
        run("UPDATE requisition_revision SET status='applied',decided_at=now() WHERE revision_no=2")
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            run("UPDATE requisition_revision SET note='late' WHERE revision_no=2")
        run(insert, tenant.org_id, req.id, 3, "amendment", "draft", digest, actor)
        assert run("DELETE FROM requisition_revision WHERE revision_no=3") == 1
        mig.downgrade(conn)
        assert conn.execute("SELECT id,title,status,version FROM requisition").fetchall() == before
        assert conn.execute("SELECT to_regclass('requisition_revision')").fetchone() == (None,)
        mig.upgrade(conn)
        assert conn.execute("SELECT id,title,status,version FROM requisition").fetchall() == before
        assert conn.execute("SELECT count(*) FROM requisition_revision").fetchone() == (0,)


def test_vendors_roundtrip_preserves_rows_and_down_drops_only_its_objects(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261014_0055"], empty_database
    )
    previous = next(r for r in revisions if r.revision == "20261014_0055")
    mig = next(r for r in revisions if r.revision == "20261014_0056")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("vendors-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        units_before = conn.execute("SELECT id FROM org_unit ORDER BY id").fetchall()
        mig.upgrade(conn)
        vendor_id = uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-000001','T','L','global',%s)",
                (vendor_id, tenant.org_id, actor),
            )
            conn.execute(
                "INSERT INTO vendor_local_disable(org_id,vendor_id,unit_id,disabled_by,reason)"
                " VALUES(%s,%s,%s,%s,'policy')",
                (tenant.org_id, vendor_id, bu.id, actor),
            )
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-000002','T2','L2','restricted',%s)",
                (uuid4(), tenant.org_id, actor),
            )
        with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-000001','D','D','global',%s)",
                (uuid4(), tenant.org_id, actor),
            )
        with pytest.raises(psycopg.errors.CheckViolation), tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,status,availability,"
                "created_by) VALUES(%s,%s,'V-000009','D','D','bogus','global',%s)",
                (uuid4(), tenant.org_id, actor),
            )
        with tenant_transaction(conn, scope):
            snapshot = conn.execute(
                "SELECT id,number,status FROM vendor ORDER BY number"
            ).fetchall()
        # The previous revision round-trips without touching vendor data.
        previous.downgrade(conn)
        previous.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert (
                conn.execute("SELECT id,number,status FROM vendor ORDER BY number").fetchall()
                == snapshot
            )
            assert conn.execute("SELECT count(*) FROM vendor_local_disable").fetchone() == (1,)
        mig.downgrade(conn)
        for table in ("vendor", "vendor_unit", "vendor_local_disable"):
            assert conn.execute("SELECT to_regclass(%s)", (table,)).fetchone() == (None,)
        assert conn.execute("SELECT id FROM org_unit ORDER BY id").fetchall() == units_before
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor").fetchone() == (0,)


def test_vendor_contacts_roundtrip_preserves_vendors_and_plants_the_unique_index(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261014_0056"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261014_0057")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("contacts-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        vendor_id = uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-000001','T','L','global',%s)",
                (vendor_id, tenant.org_id, actor),
            )
        mig.upgrade(conn)
        insert = (
            "INSERT INTO vendor_contact(id,org_id,vendor_id,type,name,email,receives_documents)"
            " VALUES(%s,%s,%s,%s,'N','n@example.test',false)"
        )
        with tenant_transaction(conn, scope):
            conn.execute(insert, (uuid4(), tenant.org_id, vendor_id, "billing"))
            conn.execute(insert, (uuid4(), tenant.org_id, vendor_id, "LEGAL"))
            conn.execute(insert, (uuid4(), tenant.org_id, vendor_id, "LEGAL"))
        with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(conn, scope):
            conn.execute(insert, (uuid4(), tenant.org_id, vendor_id, "billing"))
        mig.downgrade(conn)
        assert conn.execute("SELECT to_regclass('vendor_contact')").fetchone() == (None,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor").fetchone() == (1,)
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor_contact").fetchone() == (0,)


def test_rfq_roundtrip_preserves_rows_and_down_returns_held_quantity(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261014_0057"], empty_database
    )
    previous = next(r for r in revisions if r.revision == "20261014_0057")
    mig = next(r for r in revisions if r.revision == "20261014_0058")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("rfq-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        requisition, line = uuid4(), uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO requisition(id,org_id,number,title,bu_id,requester_id,created_by,"
                "currency,standalone)"
                " VALUES(%s,%s,'REQ-1','R',%s,%s,%s,'USD',true)",
                (requisition, tenant.org_id, bu.id, actor, actor),
            )
            conn.execute(
                "INSERT INTO requisition_line(id,org_id,requisition_id,line_no,line_type,"
                "description,quantity,uom_code,unit_price,amount,currency,sourced_qty)"
                " VALUES(%s,%s,%s,1,'item','Pump',10,'EA',1,10,'USD',4)",
                (line, tenant.org_id, requisition),
            )
        mig.upgrade(conn)
        rfq = uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq(id,org_id,number,title,unit_id,currency,owner_id)"
                " VALUES(%s,%s,'RFQ-00001','T',%s,'USD',%s)",
                (rfq, tenant.org_id, bu.id, actor),
            )
            conn.execute(
                "INSERT INTO rfq_line(id,org_id,rfq_id,line_no,requisition_id,"
                "requisition_line_id,requisition_version,quantity,description,uom_code,currency)"
                " VALUES(%s,%s,%s,1,%s,%s,1,4,'Pump','EA','USD')",
                (uuid4(), tenant.org_id, rfq, requisition, line),
            )
        with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq_line(id,org_id,rfq_id,line_no,requisition_id,"
                "requisition_line_id,requisition_version,quantity,description,uom_code,currency)"
                " VALUES(%s,%s,%s,2,%s,%s,1,1,'Pump','EA','USD')",
                (uuid4(), tenant.org_id, rfq, requisition, line),
            )
        with pytest.raises(psycopg.errors.CheckViolation), tenant_transaction(conn, scope):
            conn.execute("UPDATE rfq SET status='bogus' WHERE id=%s", (rfq,))
        with tenant_transaction(conn, scope):
            snapshot = conn.execute("SELECT id,number,status FROM rfq").fetchall()
        # The previous revision round-trips without touching RFQ data.
        previous.downgrade(conn)
        previous.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT id,number,status FROM rfq").fetchall() == snapshot
        mig.downgrade(conn)
        for table in ("rfq", "rfq_line"):
            assert conn.execute("SELECT to_regclass(%s)", (table,)).fetchone() == (None,)
        with tenant_transaction(conn, scope):
            assert conn.execute(
                "SELECT sourced_qty FROM requisition_line WHERE id=%s", (line,)
            ).fetchone() == (0,)
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM rfq").fetchone() == (0,)


def test_rfq_content_roundtrip_preserves_rfqs_and_constrains_invitees(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0067"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0068")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("rfq-content-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        rfq, vendor = uuid4(), uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq(id,org_id,number,title,unit_id,currency,owner_id)"
                " VALUES(%s,%s,'RFQ-00001','T',%s,'USD',%s)",
                (rfq, tenant.org_id, bu.id, actor),
            )
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-1','V','V','global',%s)",
                (vendor, tenant.org_id, actor),
            )
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            deadline, zone = conn.execute(
                "SELECT deadline_at, deadline_tz FROM rfq WHERE id=%s", (rfq,)
            ).fetchone()
            assert deadline is not None and zone == "UTC"  # existing RFQs are backfilled
            conn.execute(
                "INSERT INTO rfq_invitee(org_id,rfq_id,vendor_id,contact_ids,vendor_snapshot,"
                "invited_by) VALUES(%s,%s,%s,%s,'{}',%s)",
                (tenant.org_id, rfq, vendor, [uuid4()], actor),
            )
        with pytest.raises(psycopg.errors.NotNullViolation), tenant_transaction(conn, scope):
            conn.execute("UPDATE rfq SET deadline_at = NULL WHERE id=%s", (rfq,))
        with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq_invitee(org_id,rfq_id,vendor_id,contact_ids,vendor_snapshot,"
                "invited_by) VALUES(%s,%s,%s,%s,'{}',%s)",
                (tenant.org_id, rfq, vendor, [uuid4()], actor),
            )
        with pytest.raises(psycopg.errors.CheckViolation), tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq_requirement(id,org_id,rfq_id,position,text,waived)"
                " VALUES(%s,%s,%s,1,'x',true)",
                (uuid4(), tenant.org_id, rfq),
            )
        for table in ("rfq_requirement", "rfq_attachment_visibility", "rfq_invitee"):
            assert conn.execute(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname=%s",
                (table,),
            ).fetchone() == (True,)
        mig.downgrade(conn)
        for table in ("rfq_requirement", "rfq_attachment_visibility", "rfq_invitee"):
            assert conn.execute("SELECT to_regclass(%s)", (table,)).fetchone() == (None,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT number FROM rfq WHERE id=%s", (rfq,)).fetchone() == (
                "RFQ-00001",
            )
        mig.upgrade(conn)


def test_vendor_documents_roundtrip_preserves_vendors_and_attachments(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261014_0058"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261014_0059")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("documents-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        vendor_id, attachment_id, version_id = uuid4(), uuid4(), uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-000001','T','L','global',%s)",
                (vendor_id, tenant.org_id, actor),
            )
            conn.execute(
                "INSERT INTO attachment(id,org_id,document_type,document_id,created_by) "
                "VALUES(%s,%s,'vendor',%s,%s)",
                (attachment_id, tenant.org_id, vendor_id, actor),
            )
            conn.execute(
                "INSERT INTO attachment_version(id,org_id,attachment_id,version,original_filename,"
                "display_name,storage_key,media_type,size_bytes,sha256,scan_status,uploaded_by) "
                "VALUES(%s,%s,%s,1,'a.pdf','a.pdf','k1','application/pdf',5,%s,"
                "'basic_checks_passed',%s)",
                (version_id, tenant.org_id, attachment_id, "0" * 64, actor),
            )
        mig.upgrade(conn)
        insert = (
            "INSERT INTO vendor_document(id,org_id,vendor_id,type_code,attachment_id,"
            "attachment_version_id,issue_date,expiry_date,verification,rejection_reason) "
            "VALUES(%s,%s,%s,'TAX',%s,%s,%s,%s,%s,%s)"
        )
        base = (uuid4(), tenant.org_id, vendor_id, attachment_id, version_id)
        for dates, verification, reason in [
            (("2026-02-01", "2026-01-01"), "unverified", None),
            (("2026-01-01", "2026-02-01"), "rejected", None),
            (("2026-01-01", "2026-02-01"), "maybe", None),
        ]:
            with (
                pytest.raises(psycopg.errors.CheckViolation),
                tenant_transaction(conn, scope),
            ):
                conn.execute(insert, (*base, *dates, verification, reason))
        with tenant_transaction(conn, scope):
            conn.execute(insert, (*base, "2026-01-01", "2026-02-01", "unverified", None))
        with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(conn, scope):
            conn.execute(
                insert, (uuid4(), *base[1:], "2026-01-01", "2026-02-01", "unverified", None)
            )
        mig.downgrade(conn)
        assert conn.execute("SELECT to_regclass('vendor_document')").fetchone() == (None,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor").fetchone() == (1,)
            assert conn.execute("SELECT count(*) FROM attachment").fetchone() == (1,)
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor_document").fetchone() == (0,)


def test_vendor_questionnaire_roundtrip_grants_the_permission_and_keeps_data(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261014_0059"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261014_0060")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("questionnaire-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        vendor_id, version_id = uuid4(), uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-000001','T','L','global',%s)",
                (vendor_id, tenant.org_id, actor),
            )
            grants_before = conn.execute(
                "SELECT role_id,permission_code FROM role_permission ORDER BY 1,2"
            ).fetchall()
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            granted = {
                r[0]
                for r in conn.execute(
                    "SELECT r.code FROM role_permission rp JOIN role r ON r.id = rp.role_id "
                    "WHERE rp.permission_code = 'vendor.request'"
                ).fetchall()
            }
            from flo.modules.identity.models import BASELINE_ROLES

            assert granted == {str(c) for c, _ in BASELINE_ROLES} - {"auditor"}
            assert "auditor" not in granted
            conn.execute("INSERT INTO vendor_questionnaire(org_id) VALUES(%s)", (tenant.org_id,))
            insert_version = (
                "INSERT INTO vendor_questionnaire_version(id,org_id,number,status,created_by) "
                "VALUES(%s,%s,%s,%s,%s)"
            )
            conn.execute(insert_version, (version_id, tenant.org_id, 1, "draft", actor))
            conn.execute(
                "INSERT INTO vendor_question(id,org_id,version_id,position,key,prompt,type,weight)"
                " VALUES(%s,%s,%s,1,'a','A','text',100)",
                (uuid4(), tenant.org_id, version_id),
            )
            conn.execute(
                "INSERT INTO vendor_onboarding(vendor_id,org_id,version_id,requested_by) "
                "VALUES(%s,%s,%s,%s)",
                (vendor_id, tenant.org_id, version_id, actor),
            )
            conn.execute(
                "UPDATE vendor_questionnaire_version SET status='published' WHERE id=%s",
                (version_id,),
            )
        for statement in (
            "UPDATE vendor_question SET weight = 1",
            "DELETE FROM vendor_question",
            "UPDATE vendor_questionnaire_version SET threshold = 5",
        ):
            with (
                pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
                tenant_transaction(conn, scope),
            ):
                conn.execute(statement)
        with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(conn, scope):
            conn.execute(insert_version, (uuid4(), tenant.org_id, 2, "published", actor))
        with pytest.raises(psycopg.errors.CheckViolation), tenant_transaction(conn, scope):
            conn.execute(insert_version, (uuid4(), tenant.org_id, 3, "bogus", actor))
        mig.downgrade(conn)
        for table in (
            "vendor_questionnaire",
            "vendor_questionnaire_version",
            "vendor_question",
            "vendor_onboarding",
        ):
            assert conn.execute("SELECT to_regclass(%s)", (table,)).fetchone() == (None,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor").fetchone() == (1,)
            assert (
                conn.execute(
                    "SELECT role_id,permission_code FROM role_permission ORDER BY 1,2"
                ).fetchall()
                == grants_before
            )
        assert conn.execute(
            "SELECT count(*) FROM permission WHERE code='vendor.request'"
        ).fetchone() == (0,)
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor_onboarding").fetchone() == (0,)


def test_vendor_sensitive_roundtrip_grants_and_removes_only_what_it_added(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261014_0061"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261014_0062")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("sensitive-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        vendor_id = uuid4()

        def grants():
            with tenant_transaction(conn, scope):
                return conn.execute(
                    "SELECT r.code FROM role_permission p JOIN role r ON r.id=p.role_id "
                    "WHERE p.permission_code='vendor.sensitive.reveal' ORDER BY 1"
                ).fetchall()

        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-000001','T','L','global',%s)",
                (vendor_id, tenant.org_id, actor),
            )
            before = conn.execute("SELECT count(*) FROM role_permission").fetchone()[0]
        mig.upgrade(conn)
        assert grants() == [
            ("budget-finance-user",),
            ("organization-administrator",),
            ("vendor-manager",),
        ]  # not the auditor
        # the tracking table is tenant-isolated like every table that holds an org_id
        assert conn.execute(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
            "WHERE relname='migration_0062_grant'"
        ).fetchone() == (True,)
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO vendor_sensitive(org_id,vendor_id,tax_id) VALUES(%s,%s,'X1')",
                (tenant.org_id, vendor_id),
            )
        mig.downgrade(conn)
        assert conn.execute("SELECT to_regclass('vendor_sensitive')").fetchone() == (None,)
        assert conn.execute("SELECT to_regclass('migration_0062_grant')").fetchone() == (None,)
        assert conn.execute(
            "SELECT count(*) FROM permission WHERE code='vendor.sensitive.reveal'"
        ).fetchone() == (0,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor").fetchone() == (1,)
            assert conn.execute("SELECT count(*) FROM role_permission").fetchone() == (before,)
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor_sensitive").fetchone() == (0,)


def test_vendor_sensitive_downgrade_keeps_a_preexisting_permission_and_its_grants(
    empty_database,
):
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261014_0061"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261014_0062")
    with psycopg.connect(empty_database, autocommit=True) as conn:
        tenant = create(conn)
        scope = Scope(tenant.org_id)
        conn.execute("INSERT INTO permission(code) VALUES ('vendor.sensitive.reveal')")
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO role_permission(org_id,role_id,permission_code) "
                "SELECT org_id,id,'vendor.sensitive.reveal' FROM role WHERE code='auditor'"
            )

        def holders():
            with tenant_transaction(conn, scope):
                return conn.execute(
                    "SELECT r.code FROM role_permission p JOIN role r ON r.id=p.role_id "
                    "WHERE p.permission_code='vendor.sensitive.reveal' ORDER BY 1"
                ).fetchall()

        mig.upgrade(conn)
        assert ("vendor-manager",) in holders() and ("auditor",) in holders()
        mig.downgrade(conn)
        assert holders() == [("auditor",)]  # only what the migration added is gone
        assert conn.execute(
            "SELECT count(*) FROM permission WHERE code='vendor.sensitive.reveal'"
        ).fetchone() == (1,)


def test_project_requested_budget_roundtrip_keeps_projects_and_plants_the_check(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261014_0060"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261015_0063")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("requested-budget-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        bu = OrgService(conn, Scope(tenant.org_id), actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        insert = (
            "INSERT INTO project(id,org_id,bu_id,number,name,owner_id,department_code,"
            "ledger_account_code,currency,created_by{extra}) "
            "VALUES(%s,%s,%s,%s,'P',%s,'D','L','USD',%s{value})"
        )
        old = uuid4()
        conn.execute(
            insert.format(extra="", value=""), (old, tenant.org_id, bu.id, "P-1", actor, actor)
        )
        mig.upgrade(conn)
        assert conn.execute(
            "SELECT name, requested_budget FROM project WHERE id=%s", (old,)
        ).fetchone() == ("P", None)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                insert.format(extra=",requested_budget", value=",0"),
                (uuid4(), tenant.org_id, bu.id, "P-2", actor, actor),
            )
        conn.execute(
            insert.format(extra=",requested_budget", value=",1250.5"),
            (uuid4(), tenant.org_id, bu.id, "P-3", actor, actor),
        )
        mig.downgrade(conn)
        assert conn.execute("SELECT count(*) FROM project").fetchone() == (2,)
        assert conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='project' AND column_name='requested_budget'"
        ).fetchone() == (0,)
        mig.upgrade(conn)


def test_migration_0053_tracking_table_is_tenant_isolated_and_0053_still_reverses(empty_database):
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261015_0063"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261021_0064")
    base = next(r for r in revisions if r.revision == "20261014_0053")
    org_a, org_b = uuid4(), uuid4()
    role = f"app_0060_{uuid4().hex[:8]}"
    with psycopg.connect(empty_database, autocommit=True) as conn:
        total = conn.execute("SELECT count(*) FROM migration_0053_change").fetchone()
        assert total is not None
        for org in (org_a, org_b, None):
            conn.execute(
                "INSERT INTO migration_0053_change(kind,org_id,code) VALUES('grant_added',%s,'x')",
                (org,),
            )
        mig.upgrade(conn)
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname='migration_0053_change'"
        ).fetchone() == (True, True)
        # The test connection is a superuser and bypasses RLS, so read as a plain role.
        conn.execute(f"CREATE ROLE {role}")
        conn.execute(f"GRANT SELECT ON migration_0053_change TO {role}")
        conn.execute(f"SET ROLE {role}")
        try:
            assert conn.execute("SELECT count(*) FROM migration_0053_change").fetchone() == (0,)
            with tenant_transaction(conn, Scope(org_a)):
                assert conn.execute("SELECT org_id FROM migration_0053_change").fetchall() == [
                    (org_a,)
                ]
        finally:
            conn.execute("RESET ROLE")
            conn.execute(f"DROP OWNED BY {role}")
            conn.execute(f"DROP ROLE {role}")
        mig.downgrade(conn)
        assert conn.execute("SELECT count(*) FROM migration_0053_change").fetchone() == (
            total[0] + 3,
        )
        base.downgrade(conn)
        assert conn.execute("SELECT to_regclass('migration_0053_change')").fetchone() == (None,)


def test_vendor_row_version_roundtrip_preserves_rows(empty_database):
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261021_0064"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261021_0065")
    with psycopg.connect(empty_database, autocommit=True) as conn:
        tenant = create(conn)
        actor = admin_id(conn)
        vendor_id = uuid4()
        with tenant_transaction(conn, Scope(tenant.org_id)):
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-000001','T','L','global',%s)",
                (vendor_id, tenant.org_id, actor),
            )
            conn.execute(
                "INSERT INTO vendor_contact(id,org_id,vendor_id,type,name,email,"
                "receives_documents) VALUES(%s,%s,%s,'billing','B','b@example.test',false)",
                (uuid4(), tenant.org_id, vendor_id),
            )
        mig.upgrade(conn)
        with tenant_transaction(conn, Scope(tenant.org_id)):
            assert conn.execute("SELECT version FROM vendor").fetchall() == [(1,)]
            assert conn.execute("SELECT version FROM vendor_contact").fetchall() == [(1,)]
        mig.downgrade(conn)
        with tenant_transaction(conn, Scope(tenant.org_id)):
            assert conn.execute("SELECT number FROM vendor").fetchall() == [("V-000001",)]
            assert conn.execute("SELECT count(*) FROM vendor_contact").fetchone() == (1,)
        cols = conn.execute(
            "SELECT count(*) FROM information_schema.columns WHERE column_name='version' "
            "AND table_name IN ('vendor','vendor_contact')"
        ).fetchone()
        assert cols == (0,)


def test_baseline_role_reads_grant_system_roles_only_and_roundtrip_keeps_other_grants(
    empty_database,
):
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261021_0065"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0066")
    with psycopg.connect(empty_database, autocommit=True) as conn:
        tenant = create(conn)
        scope = Scope(tenant.org_id)
        with tenant_transaction(conn, scope):
            # seeding already applies the new grants; strip them to look like a tenant that
            # predates this story, keeping approver's master.read as one already held
            conn.execute(
                "DELETE FROM role_permission USING role r WHERE r.id=role_id "
                "AND r.is_system AND permission_code IN ('project.read','master.read') "
                "AND r.code IN ('requestor','approver','sourcing-specialist','purchasing-user',"
                "'vendor-manager')"
            )
            # an administrator's custom role that reuses a baseline code is never touched
            conn.execute("UPDATE role SET is_system=false WHERE code='sourcing-specialist'")
            # an unrelated grant, and one a system role already held before the migration
            conn.execute(
                "INSERT INTO role_permission(org_id,role_id,permission_code) "
                "SELECT org_id,id,'master.read' FROM role "
                "WHERE code='approver' ON CONFLICT DO NOTHING"
            )
            conn.execute(
                "INSERT INTO role_permission(org_id,role_id,permission_code) "
                "SELECT org_id,id,'asset.view' FROM role WHERE code='requestor'"
            )

        def grants():
            with tenant_transaction(conn, scope):
                return conn.execute(
                    "SELECT r.code,p.permission_code FROM role_permission p "
                    "JOIN role r ON r.id=p.role_id "
                    "WHERE p.permission_code IN ('project.read','master.read','asset.view') "
                    "AND r.code IN ('requestor','approver','sourcing-specialist',"
                    "'purchasing-user','vendor-manager','project-manager') "
                    "ORDER BY 1,2"
                ).fetchall()

        before = grants()
        mig.upgrade(conn)
        after = set(grants()) - set(before)
        assert after == {
            ("requestor", "master.read"),
            ("purchasing-user", "project.read"),
            ("purchasing-user", "master.read"),
            ("vendor-manager", "project.read"),
            ("vendor-manager", "master.read"),
        }
        assert not [g for g in grants() if g[0] == "sourcing-specialist" and g not in before]
        with tenant_transaction(conn, scope):
            assert conn.execute(
                "SELECT count(*) FROM audit_log WHERE action='role.permission.grant' "
                "AND correlation_id='migration-20261022_0066'"
            ).fetchone() == (1,)
        mig.downgrade(conn)
        assert grants() == before  # the custom role, approver and asset.view survive
        mig.upgrade(conn)


def test_vendor_changes_roundtrip_keeps_vendors_and_plants_the_pending_index(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0066"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0067")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("vendor-changes-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        vendor_id = uuid4()
        mig.upgrade(conn)
        change = (
            "INSERT INTO vendor_change(org_id,vendor_id,kind,reason,requested_by) "
            "VALUES(%s,%s,%s,%s,%s)"
        )
        with tenant_transaction(conn, Scope(tenant.org_id)):
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-000001','T','L','global',%s)",
                (vendor_id, tenant.org_id, actor),
            )
            conn.execute(change, (tenant.org_id, vendor_id, "suspend", "why", actor))
        with tenant_transaction(conn, Scope(tenant.org_id)):
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(change, (tenant.org_id, vendor_id, "reactivate", "why", actor))
        with tenant_transaction(conn, Scope(tenant.org_id)):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO vendor_change(org_id,vendor_id,kind,requested_by,status) "
                    "VALUES(%s,%s,'suspend',%s,'withdrawn')",
                    (tenant.org_id, vendor_id, actor),
                )
        mig.downgrade(conn)
        assert conn.execute("SELECT to_regclass('vendor_change')").fetchone() == (None,)
        with tenant_transaction(conn, Scope(tenant.org_id)):
            assert conn.execute("SELECT count(*) FROM vendor").fetchone() == (1,)
        mig.upgrade(conn)
        with tenant_transaction(conn, Scope(tenant.org_id)):
            assert conn.execute("SELECT count(*) FROM vendor_change").fetchone() == (0,)


def test_rfq_lifecycle_roundtrip_keeps_rfqs_and_versions_are_immutable(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0068"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0069")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("rfq-lifecycle-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        rfq = uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq(id,org_id,number,title,unit_id,currency,owner_id)"
                " VALUES(%s,%s,'RFQ-00001','T',%s,'USD',%s)",
                (rfq, tenant.org_id, bu.id, actor),
            )
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq_version(org_id,rfq_id,version,snapshot,diff,published_by)"
                " VALUES(%s,%s,1,'{}','{}',%s)",
                (tenant.org_id, rfq, actor),
            )
        for statement in ("UPDATE rfq_version SET reason='x'", "DELETE FROM rfq_version"):
            with (
                pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
                tenant_transaction(conn, scope),
            ):
                conn.execute(statement)
        mig.downgrade(conn)
        assert conn.execute("SELECT to_regclass('rfq_version')").fetchone() == (None,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM rfq").fetchone() == (1,)
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM rfq_version").fetchone() == (0,)


def test_bids_roundtrip_drops_bids_and_guards_submitted_ones(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0070"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0071")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("bids-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        rfq = uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq(id,org_id,number,title,unit_id,currency,owner_id)"
                " VALUES(%s,%s,'RFQ-00001','T',%s,'USD',%s)",
                (rfq, tenant.org_id, bu.id, actor),
            )
        mig.upgrade(conn)
        assert conn.execute("SELECT to_regclass('bid_line')").fetchone() != (None,)
        mig.downgrade(conn)
        for name in ("bid", "bid_line", "bid_requirement"):
            assert conn.execute("SELECT to_regclass(%s)", (name,)).fetchone() == (None,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM rfq").fetchone() == (1,)
        mig.upgrade(
            conn
        )  # reversible; the guards themselves are planted in tests/sourcing/test_bids


def test_finance_vendor_view_grant_roundtrip_leaves_other_grants_alone(empty_database):
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0069"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0070")
    with psycopg.connect(empty_database, autocommit=True) as conn:
        tenant = create(conn)
        scope = Scope(tenant.org_id)

        def held(role):
            with tenant_transaction(conn, scope):
                return conn.execute(
                    "SELECT p.permission_code FROM role_permission p "
                    "JOIN role r ON r.id=p.role_id WHERE r.code=%s "
                    "AND p.permission_code IN ('vendor.view','vendor.sensitive.reveal') ORDER BY 1",
                    (role,),
                ).fetchall()

        with tenant_transaction(conn, scope):  # look like a tenant that predates the grant
            conn.execute(
                "DELETE FROM role_permission USING role r WHERE r.id=role_id "
                "AND r.code='budget-finance-user' AND permission_code='vendor.view'"
            )
        before = held("budget-finance-user")
        assert ("vendor.view",) not in before
        mig.upgrade(conn)
        assert held("budget-finance-user") == [("vendor.sensitive.reveal",), ("vendor.view",)]
        mig.downgrade(conn)
        assert held("budget-finance-user") == before  # the reveal grant survives
        mig.upgrade(conn)


def test_identity_display_name_backfills_and_roundtrips(empty_database):
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0071"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0072")
    with psycopg.connect(empty_database, autocommit=True) as conn:
        first = create(conn)
        second = create(conn, "SECOND", "second@example.test")
        actor = admin_id(conn)
        conn.execute(
            "INSERT INTO identity_membership(identity_id, org_id) VALUES (%s,%s)",
            (actor, second.org_id),
        )

        def invite(org, email, name, used_at):
            with tenant_transaction(conn, Scope(org.org_id)):
                conn.execute(
                    "INSERT INTO invitation(id,org_id,email,full_name,role_code,scope_type,"
                    "invited_by,sent_at,expires_at,used_at,used_by) VALUES "
                    "(%s,%s,%s,%s,'viewer','org',%s,now(),now()+interval '1 day',%s,%s)",
                    (uuid4(), org.org_id, email, name, actor, used_at, actor),
                )

        # the latest used invitation overall is in the second org, whichever org is walked first
        invite(first, "a@example.test", "Old Name", "2026-10-01T00:00:00Z")
        invite(first, "b@example.test", " Alice Ng ", "2026-10-02T00:00:00Z")
        invite(second, "c@example.test", "Newest Second", "2026-10-03T00:00:00Z")
        invite(second, "d@example.test", "  ", "2026-10-09T00:00:00Z")  # blank names never win
        # invitation is FORCE RLS: run the migration as a role that cannot bypass it
        conn.execute("CREATE ROLE display_name_probe NOLOGIN NOSUPERUSER NOBYPASSRLS")
        try:
            conn.execute("GRANT ALL ON invitation, identity_membership TO display_name_probe")
            conn.execute("GRANT TEMP ON DATABASE " + conn.info.dbname + " TO display_name_probe")
            conn.execute("ALTER TABLE identity OWNER TO display_name_probe")
            for _ in range(2):
                conn.execute("SET ROLE display_name_probe")
                try:
                    mig.upgrade(conn)
                finally:
                    conn.execute("RESET ROLE")
                row = conn.execute(
                    "SELECT display_name, row_version FROM identity WHERE id=%s", (actor,)
                ).fetchone()
                assert row == ("Newest Second", 1)
                conn.execute("SET ROLE display_name_probe")
                try:
                    mig.downgrade(conn)
                finally:
                    conn.execute("RESET ROLE")
                assert conn.execute(
                    "SELECT count(*) FROM information_schema.columns WHERE table_name='identity' "
                    "AND column_name IN ('display_name','row_version')"
                ).fetchone() == (0,)
        finally:
            conn.execute("ALTER TABLE identity OWNER TO CURRENT_USER")
            conn.execute("DROP OWNED BY display_name_probe")
            conn.execute("DROP ROLE display_name_probe")
        assert conn.execute("SELECT count(*) FROM identity").fetchone() == (2,)


def test_bid_late_columns_roundtrip_and_keep_submitted_bids(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0072"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0073")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("bid-late-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        rfq, vendor, attachment, bid = uuid4(), uuid4(), uuid4(), uuid4()
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq(id,org_id,number,title,unit_id,currency,owner_id)"
                " VALUES(%s,%s,'RFQ-00001','T',%s,'USD',%s)",
                (rfq, tenant.org_id, bu.id, actor),
            )
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by)"
                " VALUES(%s,%s,'V-1','V','V','global',%s)",
                (vendor, tenant.org_id, actor),
            )
            conn.execute(
                "INSERT INTO rfq_invitee(rfq_id,org_id,vendor_id,contact_ids,vendor_snapshot,"
                "invited_by) VALUES(%s,%s,%s,ARRAY[gen_random_uuid()],'{}',%s)",
                (rfq, tenant.org_id, vendor, actor),
            )
            conn.execute(
                "INSERT INTO rfq_version(rfq_id,org_id,version,snapshot,diff,published_by)"
                " VALUES(%s,%s,1,'{}','{}',%s)",
                (rfq, tenant.org_id, actor),
            )
            conn.execute(
                "INSERT INTO attachment(id,org_id,document_type,document_id,created_by)"
                " VALUES(%s,%s,'bid',%s,%s)",
                (attachment, tenant.org_id, bid, actor),
            )
            conn.execute(
                "INSERT INTO bid(id,org_id,rfq_id,vendor_id,rfq_version,currency,sent_at,"
                "keyed_by,evidence_attachment_id,status,submitted_at,late_accepted_by,"
                "late_accepted_at,late_reason) VALUES(%s,%s,%s,%s,1,'USD',now(),%s,%s,"
                "'submitted',now(),%s,now(),'Courier delay')",
                (bid, tenant.org_id, rfq, vendor, actor, attachment, actor),
            )
        with pytest.raises(psycopg.errors.CheckViolation), tenant_transaction(conn, scope):
            conn.execute(  # a reason without who and when is refused
                "INSERT INTO bid(id,org_id,rfq_id,vendor_id,rfq_version,currency,sent_at,"
                "keyed_by,late_reason) VALUES(%s,%s,%s,%s,1,'USD',now(),%s,'x')",
                (uuid4(), tenant.org_id, rfq, vendor, actor),
            )
        mig.downgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM bid").fetchone() == (1,)
        mig.upgrade(conn)


def test_rfq_comparison_migration_is_reversible_and_tenant_scoped(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0073"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0074")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("rfq-comparison-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        rfq = uuid4()
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq(id,org_id,number,title,unit_id,currency,owner_id)"
                " VALUES(%s,%s,'RFQ-00001','T',%s,'USD',%s)",
                (rfq, tenant.org_id, bu.id, actor),
            )
            conn.execute(
                "INSERT INTO rfq_comparison(org_id,rfq_id,rate_date,frozen_at,rates)"
                ' VALUES(%s,%s,\'2026-10-20\',now(),\'{"EUR": {"rate": "1.1"}}\')',
                (tenant.org_id, rfq),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation), tenant_transaction(conn, scope):
            conn.execute(  # a comparison belongs to an RFQ of its own organization
                "INSERT INTO rfq_comparison(org_id,rfq_id,rate_date,frozen_at,rates)"
                " VALUES(%s,%s,'2026-10-20',now(),'{}')",
                (tenant.org_id, uuid4()),
            )
        mig.downgrade(conn)
        assert conn.execute("SELECT to_regclass('rfq_comparison')").fetchone() == (None,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM rfq").fetchone() == (1,)  # the RFQ stays
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM rfq_comparison").fetchone() == (0,)


def test_rfq_evaluation_migration_is_reversible_and_append_only(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0075"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0076")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("rfq-evaluation-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        rfq = uuid4()
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq(id,org_id,number,title,unit_id,currency,owner_id)"
                " VALUES(%s,%s,'RFQ-00001','T',%s,'USD',%s)",
                (rfq, tenant.org_id, bu.id, actor),
            )
            conn.execute(
                "INSERT INTO rfq_evaluator(org_id,rfq_id,identity_id,added_by) VALUES(%s,%s,%s,%s)",
                (tenant.org_id, rfq, actor, actor),
            )
            conn.execute(
                "INSERT INTO rfq_evaluator_declaration(org_id,rfq_id,identity_id,conflict)"
                " VALUES(%s,%s,%s,false)",
                (tenant.org_id, rfq, actor),
            )
        with pytest.raises(psycopg.errors.CheckViolation), tenant_transaction(conn, scope):
            conn.execute(  # a conflict without a detail is refused
                "INSERT INTO rfq_evaluator_declaration(org_id,rfq_id,identity_id,conflict)"
                " VALUES(%s,%s,%s,true)",
                (tenant.org_id, rfq, actor),
            )
        with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(conn, scope):
            conn.execute(  # a second clear declaration is refused
                "INSERT INTO rfq_evaluator_declaration(org_id,rfq_id,identity_id,conflict)"
                " VALUES(%s,%s,%s,false)",
                (tenant.org_id, rfq, actor),
            )
        with pytest.raises(psycopg.Error), tenant_transaction(conn, scope):
            conn.execute("DELETE FROM rfq_evaluator_declaration")  # append-only
        mig.downgrade(conn)
        assert conn.execute("SELECT to_regclass('rfq_score')").fetchone() == (None,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM rfq").fetchone() == (1,)  # the RFQ stays
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM rfq_evaluator").fetchone() == (0,)


def test_award_migration_is_reversible_one_live_award_per_rfq_and_keeps_the_rfq(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0076"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261022_0077")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("award-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        rfq = uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq(id,org_id,number,title,unit_id,currency,owner_id)"
                " VALUES(%s,%s,'RFQ-00001','T',%s,'USD',%s)",
                (rfq, tenant.org_id, bu.id, actor),
            )
        mig.upgrade(conn)

        def award(number, status):
            conn.execute(
                "INSERT INTO award(id,org_id,number,rfq_id,basis,status,rates,created_by)"
                " VALUES(%s,%s,%s,%s,'manual',%s,'{}',%s)",
                (uuid4(), tenant.org_id, number, rfq, status, actor),
            )

        with tenant_transaction(conn, scope):
            award("AWD-00001", "cancelled")
            award("AWD-00002", "draft")
        with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(conn, scope):
            award("AWD-00003", "approval_pending")  # a second live award on the RFQ
        with tenant_transaction(conn, scope):
            award("AWD-00004", "superseded")  # terminal awards do not count
        mig.downgrade(conn)
        for name in ("award", "award_line"):
            assert conn.execute("SELECT to_regclass(%s)", (name,)).fetchone() == (None,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM rfq").fetchone() == (1,)  # the RFQ stays
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM award").fetchone() == (0,)


def test_purchase_order_migration_up_down_and_preserves_awards(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261022_0077"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261027_0078")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("po-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        rfq, award = uuid4(), uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq(id,org_id,number,title,unit_id,currency,owner_id)"
                " VALUES(%s,%s,'RFQ-00001','T',%s,'USD',%s)",
                (rfq, tenant.org_id, bu.id, actor),
            )
            conn.execute(
                "INSERT INTO award(id,org_id,number,rfq_id,basis,status,rates,created_by)"
                " VALUES(%s,%s,'AWD-00001',%s,'manual','approved','{}',%s)",
                (award, tenant.org_id, rfq, actor),
            )
        mig.upgrade(conn)
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name IN ('award_line','purchase_order','po_line')"
            )
        }
        assert "ordered_qty" in columns
        assert not {c for c in columns if "amount" in c and c.startswith("ordered")}
        for table in ("purchase_order", "po_line"):
            assert conn.execute(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname=%s",
                (table,),
            ).fetchone() == (True,)
        mig.downgrade(conn)
        for name in ("purchase_order", "po_line"):
            assert conn.execute("SELECT to_regclass(%s)", (name,)).fetchone() == (None,)
        assert conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='award_line' AND column_name='ordered_qty'"
        ).fetchone() == (0,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM award").fetchone() == (1,)  # the award stays
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM award").fetchone() == (1,)
            assert conn.execute("SELECT count(*) FROM purchase_order").fetchone() == (0,)


def test_purchase_order_status_migration_up_down_and_preserves_orders(empty_database):
    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261027_0078"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261028_0079")
    with psycopg.connect(empty_database, autocommit=True) as conn:
        mig.upgrade(conn)
        check = "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname=%s"
        [(definition,)] = conn.execute(check, ("purchase_order_status_check",)).fetchall()
        assert all(s in definition for s in ("approval_pending", "superseded", "accepted"))
        mig.downgrade(conn)
        [(definition,)] = conn.execute(check, ("purchase_order_status_check",)).fetchall()
        assert "closed" not in definition and "deleted" in definition
        mig.upgrade(conn)


def test_po_pricing_migration_backfills_a_row_and_goes_down_and_up(empty_database):
    from flo.kernel.logging import correlation_context
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from tests.org.test_bootstrap import admin_id, create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261028_0079"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261028_0080")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("po-pricing-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        bu = OrgService(conn, scope, actor).create_unit(
            OrgUnitCreate(code="BU", name="BU", kind="bu")
        )
        rfq, award, po, rl, req, req_line, project = (uuid4() for _ in range(7))
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO rfq(id,org_id,number,title,unit_id,currency,owner_id)"
                " VALUES(%s,%s,'RFQ-00001','T',%s,'KWD',%s)",
                (rfq, tenant.org_id, bu.id, actor),
            )
            conn.execute(
                "INSERT INTO award(id,org_id,number,rfq_id,basis,status,rates,created_by)"
                " VALUES(%s,%s,'AWD-00001',%s,'manual','approved','{}',%s)",
                (award, tenant.org_id, rfq, actor),
            )
        # the PO tables carry foreign keys to requisitions and bids; a pre-existing row is
        # planted with those checks off, as the backfill reads only the row itself
        with tenant_transaction(conn, scope):
            conn.execute("SET LOCAL session_replication_role = replica")
            conn.execute(
                "INSERT INTO purchase_order(id,org_id,number,unit_id,vendor_id,requisition_id,"
                "vendor_snapshot,award_id,currency,rate,bill_to,recipients,requester_id,created_by)"
                " VALUES(%s,%s,'PO-1',%s,%s,%s,'{}',%s,'KWD','{\"to\":\"USD\",\"rate\":\"3.25\"}',"
                "'{}','[]',%s,%s)",
                (po, tenant.org_id, bu.id, uuid4(), req, award, actor, actor),
            )
            conn.execute(
                "INSERT INTO po_line(org_id,po_id,line_no,award_id,rfq_line_id,vendor_id,"
                "requisition_id,requisition_line_id,description,uom_code,quantity,unit_price,"
                "project_id) VALUES(%s,%s,1,%s,%s,%s,%s,%s,'d','EA','3','1.2345',%s)",
                (tenant.org_id, po, award, rl, uuid4(), req, req_line, project),
            )
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            row = conn.execute(
                "SELECT price,tax,freight,line_total,budget_amount,budget_amount_project"
                " FROM po_line"
            ).fetchone()
            mode = conn.execute("SELECT freight_mode, document_freight FROM purchase_order")
            assert mode.fetchone() == ("line", None)
        # 3 x 1.2345 = 3.7035 -> 3.704 KWD; x 3.25 = 12.038 -> 12.04 USD
        assert row == (
            Decimal("3.7040"),
            0,
            0,
            Decimal("3.7040"),
            Decimal("3.7040"),
            Decimal("12.0400"),
        )
        mig.downgrade(conn)
        assert conn.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_name='po_line'"
            " AND column_name IN ('price','tax','freight','line_total','budget_amount')"
        ).fetchone() == (0,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM po_line").fetchone() == (1,)
        mig.upgrade(conn)
