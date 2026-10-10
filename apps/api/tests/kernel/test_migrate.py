from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from datetime import date
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


@pytest.fixture
def empty_database() -> Iterator[str]:
    administrative_url, configured = _database_url()
    database_name = f"flo_migrate_{uuid4().hex}"
    connection_parameters = conninfo_to_dict(administrative_url)
    connection_parameters["dbname"] = database_name
    database_url = make_conninfo(**connection_parameters)
    try:
        with psycopg.connect(administrative_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )
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
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
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
        connection.execute(
            "INSERT INTO schema_migration (revision) VALUES ('newer_than_image')"
        )
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
    chain = tuple(r for r in migrate.discover_migrations(MIGRATIONS)
                  if r.revision <= "20261009_0031")
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
                "scope_type='invalid'", "resend_counter=-1",
                "used_at=now()", "withdrawn_at=now()",
            ):
                with pytest.raises(CheckViolation), connection.transaction():
                    connection.execute(f"UPDATE invitation SET {assignment} WHERE id=%s",
                                       (invitation,))
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

    chain = tuple(r for r in migrate.discover_migrations(MIGRATIONS)
                  if r.revision <= "20261009_0032")
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

    chain = tuple(r for r in migrate.discover_migrations(MIGRATIONS)
                  if r.revision <= "20261009_0033")
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
            seeds = {table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                     for table in ("identity", "organization")}
        chain[-1].upgrade(conn)
        assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                            "WHERE relname='project_risk'").fetchone() == (True, True)
        assert conn.execute("SELECT * FROM project").fetchall() == before
        chain[-1].downgrade(conn)
        assert conn.execute("SELECT * FROM project").fetchall() == before
        assert conn.execute(
            "SELECT to_regclass('project_risk')"
        ).fetchone() == (None,)
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

    chain = tuple(r for r in migrate.discover_migrations(MIGRATIONS)
                  if r.revision <= "20261009_0034")
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
                "VALUES(%s,%s,2,'create','{}')", (tenant.org_id, batch),
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
                        f"VALUES(%s,%s,3,{values})", (other.org_id, batch),
                    )
        role = "import_row_rls_" + uuid4().hex
        conn.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
        )
        try:
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            conn.execute(
                sql.SQL("GRANT SELECT,INSERT ON import_row,import_result TO {}")
                .format(sql.Identifier(role))
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
                        conn.execute(f"INSERT INTO {table}(org_id,batch_id,row_no,{columns}) "
                                     f"VALUES(%s,%s,4,{values})", (tenant.org_id, batch))
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

    chain = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(chain[:-1], empty_database)
    with psycopg.connect(empty_database, autocommit=True) as conn:
        tenant = create(conn)
        actor = admin_id(conn)
        id = uuid4()
        with tenant_transaction(conn, Scope(tenant.org_id)):
            conn.execute("INSERT INTO import_batch(id,org_id,template,template_version,"
                         "uploader_id,"
                         "file_name,file_sha256,file_size,counts) "
                         "VALUES(%s,%s,'probe',1,%s,'seed.csv',%s,1,'{}')",
                         (id, tenant.org_id, actor, "a" * 64))
            before = conn.execute("SELECT * FROM import_batch").fetchall()
        chain[-1].upgrade(conn)
        with tenant_transaction(conn, Scope(tenant.org_id)):
            assert conn.execute(
                "SELECT external_key,progress,cancel_requested,error_class "
                "FROM import_batch WHERE id=%s", (id,),
            ).fetchone() == (None, {}, False, None)
            for key in ("", "k" * 129):
                with pytest.raises(CheckViolation), conn.transaction():
                    conn.execute("UPDATE import_batch SET external_key=%s WHERE id=%s", (key, id))
            conn.execute("UPDATE import_batch SET external_key='seed' WHERE id=%s", (id,))
            with pytest.raises(UniqueViolation), conn.transaction():
                conn.execute("INSERT INTO import_batch(id,org_id,template,template_version,"
                         "uploader_id,"
                             "file_name,file_sha256,file_size,counts,external_key) "
                             "VALUES(%s,%s,'probe',1,%s,'other.csv',%s,1,'{}','seed')",
                             (uuid4(), tenant.org_id, actor, "b" * 64))
        chain[-1].downgrade(conn)
        with tenant_transaction(conn, Scope(tenant.org_id)):
            assert conn.execute("SELECT * FROM import_batch").fetchall() == before
        chain[-1].upgrade(conn)
        with tenant_transaction(conn, Scope(tenant.org_id)):
            assert conn.execute("SELECT id FROM import_batch").fetchall() == [(id,)]
