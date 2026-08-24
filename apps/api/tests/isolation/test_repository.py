from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from psycopg import sql

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.tenancy.context import Scope, current_scope
from flo.kernel.tenancy.middleware import install_tenant_context
from flo.kernel.tenancy.rls import tenant_transaction

from .conftest import TenantDatabase, assume_application_role


@dataclass(frozen=True)
class TenantRecord:
    id: UUID
    name: str


class TenantRecordRepo(ScopedRepo[TenantRecord]):
    def __init__(
        self,
        connection: psycopg.Connection[dict[str, object]],
        scope: Scope,
        table: str,
    ) -> None:
        super().__init__(connection, scope)
        self._connection = connection
        self._table = table

    @staticmethod
    def _record(row: dict[str, object]) -> TenantRecord:
        return TenantRecord(id=UUID(str(row["id"])), name=str(row["name"]))

    def get(self, record_id: UUID) -> TenantRecord | None:
        with tenant_transaction(self._connection, self.scope):
            row = self._connection.execute(
                sql.SQL(
                    "SELECT id, name FROM public.{} "
                    "WHERE id = %(id)s AND org_id = %(org_id)s"
                ).format(sql.Identifier(self._table)),
                self.scoped_params({"id": record_id}),
            ).fetchone()
        return None if row is None else self._record(row)

    def list(self) -> list[TenantRecord]:
        return self._matching("")

    def search(self, query: str) -> list[TenantRecord]:
        return self._matching(query)

    def export(self) -> list[TenantRecord]:
        return self.list()

    def _matching(self, query: str) -> list[TenantRecord]:
        with tenant_transaction(self._connection, self.scope):
            rows = self._connection.execute(
                sql.SQL(
                    "SELECT id, name FROM public.{} "
                    "WHERE org_id = %(org_id)s AND name ILIKE %(query)s ORDER BY name"
                ).format(sql.Identifier(self._table)),
                self.scoped_params({"query": f"%{query}%"}),
            ).fetchall()
        return [self._record(row) for row in rows]

    def rename(self, record_id: UUID, name: str) -> TenantRecord | None:
        with tenant_transaction(self._connection, self.scope):
            row = self._connection.execute(
                sql.SQL(
                    "UPDATE public.{} SET name = %(name)s "
                    "WHERE id = %(id)s AND org_id = %(org_id)s RETURNING id, name"
                ).format(sql.Identifier(self._table)),
                self.scoped_params({"id": record_id, "name": name}),
            ).fetchone()
        return None if row is None else self._record(row)

    def delete(self, record_id: UUID) -> bool:
        with tenant_transaction(self._connection, self.scope):
            row = self._connection.execute(
                sql.SQL(
                    "DELETE FROM public.{} "
                    "WHERE id = %(id)s AND org_id = %(org_id)s RETURNING id"
                ).format(sql.Identifier(self._table)),
                self.scoped_params({"id": record_id}),
            ).fetchone()
        return row is not None


@dataclass(frozen=True)
class AuthenticatedSession:
    org_id: UUID


def _serialize(record: TenantRecord) -> dict[str, str]:
    return {"id": str(record.id), "name": record.name}


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="record not found")


def _test_app(database: TenantDatabase) -> FastAPI:
    sessions = {
        "tenant-a-session": AuthenticatedSession(database.org_a),
        "tenant-b-session": AuthenticatedSession(database.org_b),
    }
    app = FastAPI()

    def resolve_session(request: Request) -> AuthenticatedSession | None:
        return sessions.get(request.cookies.get("flo_session", ""))

    def repo() -> TenantRecordRepo:
        return TenantRecordRepo(database.connection, current_scope(), database.table)

    install_tenant_context(app, resolve_session)

    @app.get("/records")
    def list_records() -> list[dict[str, str]]:
        return [_serialize(record) for record in repo().list()]

    @app.get("/records/search")
    def search_records(q: str) -> list[dict[str, str]]:
        return [_serialize(record) for record in repo().search(q)]

    @app.get("/records/export")
    def export_records() -> list[dict[str, str]]:
        return [_serialize(record) for record in repo().export()]

    @app.get("/records/{record_id}")
    def get_record(record_id: UUID) -> dict[str, str]:
        record = repo().get(record_id)
        if record is None:
            raise _not_found()
        return _serialize(record)

    @app.patch("/records/{record_id}")
    def rename_record(record_id: UUID, body: dict[str, str]) -> dict[str, str]:
        record = repo().rename(record_id, body["name"])
        if record is None:
            raise _not_found()
        return _serialize(record)

    @app.delete("/records/{record_id}", status_code=204)
    def delete_record(record_id: UUID) -> None:
        if not repo().delete(record_id):
            raise _not_found()

    return app


def test_foreign_ids_are_404_and_all_collection_and_write_paths_are_scoped(
    tenant_database: TenantDatabase, rls_migration: ModuleType
) -> None:
    rls_migration.upgrade(tenant_database.connection)
    assume_application_role(tenant_database)
    app = _test_app(tenant_database)
    tenant_a_cookie = {"flo_session": "tenant-a-session"}
    tenant_b_cookie = {"flo_session": "tenant-b-session"}

    with TestClient(app) as client:
        assert client.get(
            f"/records/{tenant_database.record_b}", cookies=tenant_a_cookie
        ).status_code == 404
        assert client.get("/records", cookies=tenant_a_cookie).json() == [
            {"id": str(tenant_database.record_a), "name": "Tenant A record"}
        ]
        assert client.get(
            "/records/search", params={"q": "Tenant"}, cookies=tenant_a_cookie
        ).json() == [{"id": str(tenant_database.record_a), "name": "Tenant A record"}]
        assert client.get("/records/export", cookies=tenant_a_cookie).json() == [
            {"id": str(tenant_database.record_a), "name": "Tenant A record"}
        ]
        # The mutating calls are bound first: an assert body is stripped under
        # -O, which would silently turn these tamper attempts into no-ops.
        tampered = client.patch(
            f"/records/{tenant_database.record_b}",
            json={"name": "tampered"},
            cookies=tenant_a_cookie,
        )
        assert tampered.status_code == 404
        deleted = client.delete(
            f"/records/{tenant_database.record_b}", cookies=tenant_a_cookie
        )
        assert deleted.status_code == 404
        assert client.get(
            f"/records/{tenant_database.record_b}", cookies=tenant_b_cookie
        ).json() == {"id": str(tenant_database.record_b), "name": "Tenant B record"}


def test_repository_scope_still_hides_foreign_rows_when_rls_is_disabled(
    tenant_database: TenantDatabase, rls_migration: ModuleType
) -> None:
    rls_migration.upgrade(tenant_database.connection)
    assume_application_role(tenant_database)
    tenant_database.connection.execute(
        sql.SQL("ALTER TABLE public.{} DISABLE ROW LEVEL SECURITY").format(
            sql.Identifier(tenant_database.table)
        )
    )
    repository = TenantRecordRepo(
        tenant_database.connection,
        Scope(tenant_database.org_a),
        tenant_database.table,
    )

    assert repository.get(tenant_database.record_b) is None
    assert repository.search("Tenant") == [
        TenantRecord(tenant_database.record_a, "Tenant A record")
    ]
    renamed = repository.rename(tenant_database.record_b, "tampered")
    assert renamed is None
    removed = repository.delete(tenant_database.record_b)
    assert removed is False
