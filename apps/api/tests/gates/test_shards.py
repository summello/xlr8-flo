"""Shards preserve the full suite and cloned databases preserve the migration head."""

import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from flo.kernel.migrate import discover_migrations
from tests.conftest import MIGRATIONS, template_name
from tests.conftest import migrated_template as template_fixture

ROOT = Path(__file__).resolve().parents[4]


def test_shards_cover_every_file_exactly_once():
    shards = []
    for number in range(1, 5):
        result = subprocess.run(
            [sys.executable, str(ROOT / "apps/api/scripts/test_shard.py"), str(number), "4"],
            check=True,
            capture_output=True,
            text=True,
        )
        shards.append(result.stdout.splitlines())
    expected = sorted(
        str(path.relative_to(ROOT)) for path in (ROOT / "apps/api/tests").rglob("test_*.py")
    )
    selected = [path for shard in shards for path in shard]
    assert sorted(selected) == expected
    assert len(selected) == len(set(selected))
    assert "apps/api/tests/requisitions/test_exactly_once_property.py" in shards[0]


def test_cloned_database_has_every_migration(migrated_database):
    with psycopg.connect(migrated_database) as conn:
        recorded = [
            row[0]
            for row in conn.execute("SELECT revision FROM schema_migration ORDER BY revision")
        ]
    expected = [revision.revision for revision in discover_migrations(MIGRATIONS)]
    assert recorded == expected
    assert recorded[-1] == expected[-1]


def test_template_name_changes_with_migration_contents_and_names(tmp_path):
    revision = tmp_path / "first.py"
    revision.write_text("first")
    original = template_name(tmp_path, "lane_a")
    assert original == template_name(tmp_path, "lane_a")
    assert original != template_name(tmp_path, "lane_b")
    revision.write_text("second")
    changed = template_name(tmp_path, "lane_a")
    assert original != changed
    revision.rename(tmp_path / "second.py")
    assert changed != template_name(tmp_path, "lane_a")


def test_template_reuses_complete_and_rebuilds_incomplete(migrated_template):
    url, name = migrated_template
    stale = "flo_tpl_000000000000_" + conninfo_to_dict(url)["dbname"]
    other = "flo_tpl_000000000000_other_" + conninfo_to_dict(url)["dbname"]
    with psycopg.connect(url, autocommit=True) as admin:
        oid = admin.execute("SELECT oid FROM pg_database WHERE datname = %s", (name,)).fetchone()
        assert template_fixture.__wrapped__() == migrated_template
        assert (
            admin.execute("SELECT oid FROM pg_database WHERE datname = %s", (name,)).fetchone()
            == oid
        )
        admin.execute(sql.SQL("COMMENT ON DATABASE {} IS NULL").format(sql.Identifier(name)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(stale)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(other)))
        try:
            assert template_fixture.__wrapped__() == migrated_template
            rebuilt = admin.execute(
                "SELECT oid, shobj_description(oid, 'pg_database') FROM pg_database "
                "WHERE datname = %s",
                (name,),
            ).fetchone()
            assert rebuilt[0] != oid[0]
            assert rebuilt[1] == "complete"
            assert (
                admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (stale,)).fetchone()
                is None
            )
            assert (
                admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (other,)).fetchone()
                is not None
            )
        finally:
            for database in (stale, other):
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database)
                    )
                )


def test_template_rejects_identifier_truncation(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://localhost/" + "a" * 63)
    with pytest.raises(pytest.fail.Exception, match="too long"):
        template_fixture.__wrapped__()


def test_required_checks_reject_failed_or_missing_shards(tmp_path):
    source = (ROOT / ".github/workflows/ci.yml").read_text()
    backend = source.split("\n  backend:\n", 1)[1].split("\n  frontend-static:", 1)[0]
    frontend = source.split("\n  frontend:\n", 1)[1].split("\n  security:", 1)[0]
    assert "needs: [detect, backend-shard]" in backend
    assert "needs: [detect, frontend-static, frontend-e2e]" in frontend
    for job in (backend, frontend):
        assert "if: always()" in job
        assert "continue-on-error" not in job
    assert "RESULT: ${{ needs.backend-shard.result }}" in backend
    assert "STATIC_RESULT: ${{ needs.frontend-static.result }}" in frontend
    assert "E2E_RESULT: ${{ needs.frontend-e2e.result }}" in frontend
    backend_command = next(
        line.strip()[5:] for line in backend.splitlines() if line.strip().startswith("run: test ")
    )
    frontend_command = next(
        line.strip()[5:] for line in frontend.splitlines() if line.strip().startswith("run: test ")
    )
    for result in ("success", "failure", "cancelled", "skipped"):
        run = subprocess.run(["sh", "-c", backend_command], cwd=tmp_path, env={"RESULT": result})
        assert (run.returncode == 0) == (result == "success")
        for variable in ("STATIC_RESULT", "E2E_RESULT"):
            env = {"STATIC_RESULT": "success", "E2E_RESULT": "success", variable: result}
            run = subprocess.run(["sh", "-c", frontend_command], cwd=tmp_path, env=env)
            assert (run.returncode == 0) == (result == "success")


def test_combined_coverage_rejects_below_eighty_percent(tmp_path):
    import os

    source = (ROOT / ".github/workflows/ci.yml").read_text()
    backend = source.split("\n  backend:\n", 1)[1].split("\n  frontend-static:", 1)[0]
    assert "coverage combine\n          coverage report --fail-under=80" in backend
    program = tmp_path / "program.py"
    for execute_branch in (True, False):
        program.write_text(
            f"execute_branch = {execute_branch}\n"
            "if execute_branch:\n    a = 1\n    b = 2\n    c = 3\n"
        )
        env = {**os.environ, "COVERAGE_FILE": str(tmp_path / ".coverage.1")}
        subprocess.run(
            [sys.executable, "-m", "coverage", "run", str(program)],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
        )
        env.pop("COVERAGE_FILE")
        subprocess.run(
            [sys.executable, "-m", "coverage", "combine"],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
        )
        report = subprocess.run(
            [sys.executable, "-m", "coverage", "report", "--fail-under=80"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert (report.returncode == 0) == execute_branch, report.stdout + report.stderr


@pytest.mark.parametrize(("shard", "total"), [(0, 4), (5, 4), (1, 0)])
def test_shard_rejects_invalid_numbers(shard, total):
    result = subprocess.run(
        [sys.executable, str(ROOT / "apps/api/scripts/test_shard.py"), str(shard), str(total)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "require 1 <= shard <= total" in result.stderr
