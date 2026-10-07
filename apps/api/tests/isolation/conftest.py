from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[4]
MIGRATION_PATH = ROOT / "migrations" / "20260824_0001_rls.py"


@dataclass(frozen=True)
class TenantDatabase:
    connection: psycopg.Connection[dict[str, object]]
    table: str
    owner: str
    org_a: UUID
    org_b: UUID
    record_a: UUID
    record_b: UUID


@pytest.fixture
def rls_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rls_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@pytest.fixture
def tenant_database() -> Iterator[TenantDatabase]:
    configured_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    database_url = configured_url or "postgresql://flo:flo-local@127.0.0.1:5432/flo_test"
    database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        connection = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
    except psycopg.Error as exc:
        if configured_url:
            pytest.fail(f"configured Postgres is unavailable: {type(exc).__name__}")
        pytest.skip("local Postgres is unavailable; run the repository stack")
        raise  # unreachable: pytest.fail and pytest.skip both raise

    suffix = uuid4().hex[:12]
    table = f"tenancy_record_{suffix}"
    owner = f"tenancy_owner_{suffix}"
    org_a, org_b = uuid4(), uuid4()
    record_a, record_b = uuid4(), uuid4()
    connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(owner)))
    connection.execute(
        sql.SQL(
            "CREATE TABLE public.{} ("
            "id uuid PRIMARY KEY, org_id uuid NOT NULL, name text NOT NULL)"
        ).format(sql.Identifier(table))
    )
    connection.execute(
        sql.SQL(
            "INSERT INTO public.{} (id, org_id, name) VALUES "
            "(%s, %s, 'Tenant A record'), (%s, %s, 'Tenant B record')"
        ).format(sql.Identifier(table)),
        (record_a, org_a, record_b, org_b),
    )
    connection.execute(
        sql.SQL("ALTER TABLE public.{} OWNER TO {}").format(
            sql.Identifier(table), sql.Identifier(owner)
        )
    )
    database = TenantDatabase(
        connection=connection,
        table=table,
        owner=owner,
        org_a=org_a,
        org_b=org_b,
        record_a=record_a,
        record_b=record_b,
    )
    try:
        yield database
    finally:
        connection.execute("RESET ROLE")
        connection.execute(
            sql.SQL("DROP TABLE IF EXISTS public.{}").format(sql.Identifier(table))
        )
        connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(owner)))
        connection.close()


def assume_application_role(database: TenantDatabase) -> None:
    database.connection.execute(
        sql.SQL("SET ROLE {}").format(sql.Identifier(database.owner))
    )
