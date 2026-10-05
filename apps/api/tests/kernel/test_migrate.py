from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
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
