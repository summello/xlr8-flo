"""Apply the repository's linear migration chain transactionally."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import psycopg
from psycopg import sql

from flo.kernel.config import Settings

_MIGRATION_LOCK_KEY = 8_581_146_503_008_001
_CREATE_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migration (
    revision text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationConnection(Protocol):
    """Connection surface exposed to revision modules."""

    def execute(self, query: str) -> object: ...


MigrationFunction = Callable[[MigrationConnection], None]


@dataclass(frozen=True, slots=True)
class Revision:
    """A validated migration revision loaded from disk."""

    revision: str
    down_revision: str | None
    path: Path
    upgrade: MigrationFunction
    downgrade: MigrationFunction


class MigrationValidationError(Exception):
    """The files on disk do not form one safe linear chain."""


class AppliedRevisionError(Exception):
    """The database contains a revision absent from this image."""


class RevisionApplyError(Exception):
    """A revision failed without retaining its potentially sensitive exception."""

    def __init__(self, revision: str, exception_type: str) -> None:
        super().__init__(revision, exception_type)
        self.revision = revision
        self.exception_type = exception_type


def _load_module(path: Path, index: int) -> ModuleType:
    module_name = f"_flo_migration_{index}_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise MigrationValidationError(f"could not load migration file {path.name}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise MigrationValidationError(
            f"migration file {path.name} failed to load ({type(exc).__name__})"
        ) from None
    return module


def _revision_from_module(path: Path, module: ModuleType) -> Revision:
    revision = getattr(module, "revision", None)
    down_revision = getattr(module, "down_revision", None)
    upgrade = getattr(module, "upgrade", None)
    downgrade = getattr(module, "downgrade", None)
    if not isinstance(revision, str) or not revision:
        raise MigrationValidationError(f"migration file {path.name} has no valid revision")
    if down_revision is not None and not isinstance(down_revision, str):
        raise MigrationValidationError(f"revision {revision} has an invalid down_revision")
    if not callable(upgrade):
        raise MigrationValidationError(f"revision {revision} has no upgrade function")
    if not callable(downgrade):
        raise MigrationValidationError(f"revision {revision} has no downgrade function")
    return Revision(
        revision=revision,
        down_revision=down_revision,
        path=path,
        upgrade=cast(MigrationFunction, upgrade),
        downgrade=cast(MigrationFunction, downgrade),
    )


def discover_migrations(migrations_dir: Path) -> tuple[Revision, ...]:
    """Load and validate migrations, returning them in chain order."""

    paths = sorted(
        path
        for path in migrations_dir.glob("*.py")
        if not path.name.startswith("_")
    )
    if not paths:
        raise MigrationValidationError("no migration revisions found")

    by_revision: dict[str, Revision] = {}
    file_order: list[str] = []
    for index, path in enumerate(paths):
        item = _revision_from_module(path, _load_module(path, index))
        if item.revision in by_revision:
            raise MigrationValidationError(f"duplicate revision {item.revision}")
        by_revision[item.revision] = item
        file_order.append(item.revision)

    for item in by_revision.values():
        if item.down_revision is not None and item.down_revision not in by_revision:
            raise MigrationValidationError(
                f"revision {item.revision} has unknown down_revision {item.down_revision}"
            )

    referenced = {
        item.down_revision
        for item in by_revision.values()
        if item.down_revision is not None
    }
    heads = sorted(set(by_revision) - referenced)
    if len(heads) > 1:
        raise MigrationValidationError(f"multiple migration heads: {', '.join(heads)}")
    if not heads:
        raise MigrationValidationError(
            f"migration chain has no head; revision {file_order[0]} is part of a cycle"
        )

    reverse_chain: list[Revision] = []
    seen: set[str] = set()
    cursor: str | None = heads[0]
    while cursor is not None:
        if cursor in seen:
            raise MigrationValidationError(f"migration chain cycles at revision {cursor}")
        seen.add(cursor)
        item = by_revision[cursor]
        reverse_chain.append(item)
        cursor = item.down_revision
    if len(seen) != len(by_revision):
        missing = next(revision for revision in file_order if revision not in seen)
        raise MigrationValidationError(f"migration chain does not include revision {missing}")

    chain = tuple(reversed(reverse_chain))
    chain_order = [item.revision for item in chain]
    if file_order != chain_order:
        mismatch = next(
            actual
            for actual, expected in zip(file_order, chain_order, strict=True)
            if actual != expected
        )
        raise MigrationValidationError(
            f"filename order disagrees with chain at revision {mismatch}"
        )
    return chain


def _applied_revisions(
    connection: psycopg.Connection[tuple[object, ...]],
) -> set[str]:
    rows = connection.execute("SELECT revision FROM schema_migration").fetchall()
    return {cast(str, row[0]) for row in rows}


def _ensure_known(applied: set[str], known: set[str]) -> None:
    unknown = sorted(applied - known)
    if unknown:
        raise AppliedRevisionError(f"database contains unknown revision {unknown[0]}")


def apply_migrations(
    migrations: Sequence[Revision],
    database_url: str,
) -> tuple[str, ...]:
    """Apply every pending revision and return the revisions applied by this runner."""

    known = {item.revision for item in migrations}
    applied_here: list[str] = []
    connection_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(connection_url, prepare_threshold=None) as connection:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
            connection.execute(_CREATE_VERSION_TABLE)
            _ensure_known(_applied_revisions(connection), known)

        for item in migrations:
            try:
                with connection.transaction():
                    connection.execute(
                        "SELECT pg_advisory_xact_lock(%s)",
                        (_MIGRATION_LOCK_KEY,),
                    )
                    applied = _applied_revisions(connection)
                    _ensure_known(applied, known)
                    if item.revision in applied:
                        continue
                    item.upgrade(cast(MigrationConnection, connection))
                    connection.execute(
                        sql.SQL("INSERT INTO schema_migration (revision) VALUES ({})").format(
                            sql.Placeholder()
                        ),
                        (item.revision,),
                    )
                    applied_here.append(item.revision)
            except AppliedRevisionError:
                raise
            except Exception as exc:
                raise RevisionApplyError(item.revision, type(exc).__name__) from None
    return tuple(applied_here)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the chain and optionally apply it to the configured database."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the migration chain without connecting to the database",
    )
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        migrations = discover_migrations(settings.migrations_dir)
        if args.check:
            return 0
        if settings.database_url is None:
            raise MigrationValidationError("DATABASE_URL is required")
        apply_migrations(migrations, settings.database_url.get_secret_value())
    except MigrationValidationError as exc:
        print(f"migration check failed: {exc}", file=sys.stderr)
        return 1
    except AppliedRevisionError as exc:
        print(f"migration refused: {exc}", file=sys.stderr)
        return 1
    except RevisionApplyError as exc:
        print(
            f"migration {exc.revision} failed: {exc.exception_type}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"migration runner failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
