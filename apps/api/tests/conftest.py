from __future__ import annotations

import os
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def pytest_configure(config: pytest.Config) -> None:
    # Fixtures drop and rebuild schema objects. Without an explicit database they used to fall
    # back to the shared flo_test, which a parallel lane then found half-dropped (13 Oct 2026).
    if not (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")):
        pytest.exit(
            "TEST_DATABASE_URL is not set. Use your own database: "
            'export TEST_DATABASE_URL="$(apps/api/.venv/bin/python agents/scripts/flo db reset)"',
            returncode=2,
        )


MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"


def template_name(directory: Path, database: str) -> str:
    digest = sha256()
    for path in sorted(directory.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"flo_tpl_{digest.hexdigest()[:12]}_{database}"


@pytest.fixture(scope="session")
def migrated_template() -> tuple[str, str]:
    from flo.kernel.migrate import apply_migrations, discover_migrations

    url = (os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    params = conninfo_to_dict(url)
    suffix = "_" + params["dbname"]
    name = template_name(MIGRATIONS, params["dbname"])
    # Postgres truncates identifiers at 63 bytes; never silently share another lane's template.
    if len(name.encode()) > 63:
        pytest.fail("test database name is too long for a worktree-scoped template")
    with psycopg.connect(url, autocommit=True) as admin:
        existing = admin.execute(
            "SELECT datname, shobj_description(oid, 'pg_database') FROM pg_database"
        ).fetchall()
        complete = False
        for database, comment in existing:
            if database.startswith("flo_tpl_") and database[20:] == suffix:
                if database == name and comment == "complete":
                    complete = True
                else:
                    admin.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
                    )
        if not complete:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            params["dbname"] = name
            apply_migrations(discover_migrations(MIGRATIONS), make_conninfo(**params))
            admin.execute(
                sql.SQL("COMMENT ON DATABASE {} IS 'complete'").format(sql.Identifier(name))
            )
    return url, name


@pytest.fixture
def migrated_database(migrated_template: tuple[str, str]) -> Iterator[str]:
    url, template = migrated_template
    name = f"flo_clone_{uuid4().hex}"
    params = conninfo_to_dict(url)
    params["dbname"] = name
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(template)
            )
        )
    try:
        yield make_conninfo(**params)
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
