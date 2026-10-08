from __future__ import annotations

import asyncio
from contextlib import nullcontext
from hashlib import sha256
from uuid import UUID

import httpx
import psycopg
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from flo.api.imports import get_import_storage, router
from flo.kernel.errors import install_problem_details
from flo.kernel.idempotency.middleware import IdempotencyMiddleware
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.imports.multipart import MAX_FILE_SIZE, read_upload
from flo.modules.imports.parsers import parse_value
from flo.modules.imports.service import XLSX_TYPE, ImportService
from flo.modules.imports.xlsx_reader import write_xlsx
from tests.authz.conftest import ROOT, load_migration
from tests.authz.test_effective_access import build_app, grant_permissions
from tests.authz.test_resolver import insert_identity

CSV = b"code,amount,on_date\r\nA,1234.56,2026-10-08\r\n"


class MemoryStorage:
    def __init__(self):
        self.objects = {}

    def put(self, key, data, *, content_type):
        assert str(UUID(key)) == key
        self.objects[key] = data

    def get(self, key):
        return self.objects[key]

    def delete(self, key):
        del self.objects[key]


@pytest.fixture
def storage():
    return MemoryStorage()


def app_for(db, storage, viewer=None):
    app = build_app(db, viewer or db.actor_id)
    app.include_router(router)
    app.dependency_overrides[get_import_storage] = lambda: storage

    # The exemption must hold even with a key, before buffering or opening a connection.
    def forbidden_connection():
        raise AssertionError("upload entered idempotency middleware")

    app.add_middleware(IdempotencyMiddleware, connection_factory=forbidden_connection)
    return app


def request(db, storage, method, path="", *, data=None, file=None, viewer=None, key=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(db, storage, viewer)),
            base_url="https://testserver",
        ) as client:
            return await client.request(
                method,
                "/api/v1/imports" + path,
                data={"template": "test_fixture"} if file else None,
                files={"file": file} if file else None,
                json=data,
                headers={"Idempotency-Key": key} if key else None,
            )

    return asyncio.run(send())


def admin(db):
    grant_permissions(db, db.actor_id, "import.run", "import.read")


def upload(db, storage, payload=CSV, filename="data.csv", content_type="text/csv", key=None):
    return request(db, storage, "POST", file=(filename, payload, content_type), key=key)


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_valid_upload(imports_db, storage, format):
    admin(imports_db)
    data = (
        CSV
        if format == "csv"
        else write_xlsx(
            [["code", "amount", "on_date"], ["A", "1234.56", "2026-10-08"]], "version=1"
        )
    )
    response = upload(
        imports_db, storage, data, "data." + format, "text/csv" if format == "csv" else XLSX_TYPE
    )
    assert response.status_code == 201, response.text
    batch = response.json()
    assert batch["headers"] == ["code", "amount", "on_date"]
    assert batch["row_count"] == 1
    assert batch["status"] == "uploaded"
    assert batch["file_sha256"] == sha256(data).hexdigest()
    assert storage.objects == {batch["id"]: data}
    with tenant_transaction(imports_db.connection, Scope(imports_db.org_a)):
        row = imports_db.connection.execute(
            "SELECT file_sha256, file_size FROM import_batch WHERE id=%s", (batch["id"],)
        ).fetchone()
        assert row == (sha256(data).hexdigest(), len(data))
        columns = imports_db.connection.execute(
            "SELECT data_type FROM information_schema.columns WHERE table_name='import_batch'"
        ).fetchall()
        assert ("bytea",) not in columns
        assert (
            imports_db.connection.execute(
                "SELECT count(*) FROM audit_log WHERE action='import.upload'"
            ).fetchone()[0]
            == 1
        )
    assert request(imports_db, storage, "GET", "/" + batch["id"]).json() == batch


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_template_download_round_trip(imports_db, storage, format):
    admin(imports_db)
    catalog = request(imports_db, storage, "GET", "/templates").json()
    assert catalog[0]["version"] == 1
    assert catalog[0]["columns"][0] == {
        "name": "code",
        "type": "text",
        "required": True,
        "example": "EXAMPLE",
        "accepted_codes_url": None,
    }
    downloaded = request(
        imports_db, storage, "GET", "/templates/test_fixture/file?format=" + format
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["X-Template-Version"] == "1"
    if format == "csv":
        assert b"version=1" in downloaded.content
    else:
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert b"version=1" in archive.read("docProps/core.xml")
    result = upload(
        imports_db,
        storage,
        downloaded.content,
        "template." + format,
        "text/csv" if format == "csv" else XLSX_TYPE,
    )
    assert result.status_code == 201, result.text
    assert result.json()["row_count"] == 1


def test_identical_uploads_create_independent_batches(imports_db, storage):
    admin(imports_db)
    first = upload(imports_db, storage, key="same-key").json()
    second = upload(imports_db, storage, key="same-key").json()
    assert first["id"] != second["id"]
    assert first["file_sha256"] == second["file_sha256"]
    assert len(storage.objects) == 2
    for batch in (first, second):
        response = request(
            imports_db,
            storage,
            "PUT",
            "/" + batch["id"] + "/mapping",
            data={"mapping": {"code": "code"}},
        )
        assert response.status_code == 200
    # Saving one mapping leaves the other independent.
    assert request(imports_db, storage, "GET", "/" + second["id"]).json()["mapping"] == {
        "code": "code"
    }


def test_renamed_extra_and_missing_source_columns_can_be_mapped(imports_db, storage):
    admin(imports_db)
    response = upload(imports_db, storage, b"External,Cost,Extra\nA,12.34,unused\n")
    assert response.status_code == 201
    id = response.json()["id"]
    missing = request(
        imports_db, storage, "PUT", f"/{id}/mapping", data={"mapping": {"amount": "Cost"}}
    )
    assert missing.status_code == 422
    assert missing.json()["errors"][0]["field"] == "mapping.code"
    good = {"code": "External", "amount": "Cost"}
    saved = request(imports_db, storage, "PUT", f"/{id}/mapping", data={"mapping": good})
    assert saved.status_code == 200
    assert saved.json()["mapping"] == good
    assert request(imports_db, storage, "GET", f"/{id}").json()["mapping"] == good


@pytest.mark.parametrize(
    "mapping",
    [
        {"code": "unknown"},
        {"code": "External", "unknown": "Cost"},
        {"code": "External", "amount": "Cost"},
    ],
)
def test_invalid_mapping_is_rejected(imports_db, storage, mapping):
    admin(imports_db)
    id = upload(imports_db, storage, b"External,Cost\nA,1e3\n").json()["id"]
    response = request(imports_db, storage, "PUT", f"/{id}/mapping", data={"mapping": mapping})
    assert response.status_code == 422
    assert request(imports_db, storage, "GET", f"/{id}").json()["mapping"] == {}


@pytest.mark.parametrize(
    "data,field",
    [
        (b"", "headers"),
        (b"code,code\nA,A\n", "headers"),
        (b"code,amount\nA,1e3\n", "rows[2].amount"),
        (b"code,on_date\nA,08/10/2026\n", "rows[2].on_date"),
        (b"code\nA,extra\n", "rows[2]"),
        (b"code\n" + b"A\n" * 50001, "file"),
        (b"code\n\xff\n", "file"),
    ],
)
def test_structure_rejects_planted_violations(imports_db, storage, data, field):
    admin(imports_db)
    response = upload(imports_db, storage, data)
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["field"] == field
    assert not storage.objects


@pytest.mark.parametrize(
    "filename,ctype,status",
    [
        ("data.exe", "text/csv", 415),
        ("data.csv", "application/octet-stream", 415),
        ("data.xlsm", XLSX_TYPE, 422),
        ("data.xlsx", "text/csv", 415),
    ],
)
def test_wrong_format_rejected(imports_db, storage, filename, ctype, status):
    admin(imports_db)
    response = upload(imports_db, storage, filename=filename, content_type=ctype)
    assert response.status_code == status
    assert not storage.objects


def test_file_cap_10_mib_plus_one(imports_db, storage):
    admin(imports_db)
    response = upload(imports_db, storage, b"x" * (MAX_FILE_SIZE + 1))
    assert response.status_code == 413
    assert response.json()["type"].endswith("/payload-too-large")
    assert not storage.objects


def test_oversized_stream_stops_before_reading_the_rest():
    app = FastAPI()

    # Exercise the exact route path through the production exemption, including a key.
    @app.post("/api/v1/imports")
    async def endpoint(request: Request):
        await read_upload(request)

    app.add_middleware(IdempotencyMiddleware, connection_factory=lambda: nullcontext(None))
    install_problem_details(app)
    consumed = 0

    async def stream():
        nonlocal consumed
        yield (
            b'--cap\r\nContent-Disposition: form-data; name="file"; filename="x.csv"\r\n'
            b"Content-Type: text/csv\r\n\r\n"
        )
        for _ in range(30):
            consumed += 1
            yield b"x" * (1024 * 1024)
        raise AssertionError("request drained beyond cap")

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            return await client.post(
                "/api/v1/imports",
                content=stream(),
                headers={
                    "Content-Type": "multipart/form-data; boundary=cap",
                    "Idempotency-Key": "ignored",
                },
            )

    response = asyncio.run(send())
    assert response.status_code == 413
    assert consumed == 11


def foreign_batch(db, storage):
    admin(db)
    from flo.modules.imports.multipart import Upload

    return (
        ImportService(db.connection, Scope(db.org_b), db.actor_id, storage)
        .upload(Upload("test_fixture", "data.csv", "text/csv", CSV, sha256(CSV).hexdigest()))
        .id
    )


def test_get_foreign_batch(imports_db, storage):
    id = foreign_batch(imports_db, storage)
    assert request(imports_db, storage, "GET", f"/{id}").status_code == 404


def test_put_foreign_batch(imports_db, storage):
    id = foreign_batch(imports_db, storage)
    assert (
        request(
            imports_db, storage, "PUT", f"/{id}/mapping", data={"mapping": {"code": "code"}}
        ).status_code
        == 404
    )


def test_upload_uses_session_tenant(imports_db, storage):
    admin(imports_db)
    response = upload(imports_db, storage)
    assert response.status_code == 201
    with tenant_transaction(imports_db.connection, Scope(imports_db.org_a)):
        assert (
            imports_db.connection.execute(
                "SELECT org_id FROM import_batch WHERE id=%s", (response.json()["id"],)
            ).fetchone()[0]
            == imports_db.org_a
        )
    assert ImportService(imports_db.connection, Scope(imports_db.org_b)).repo.scope.org_id != (
        imports_db.org_a
    )
    with pytest.raises(Exception) as error:
        ImportService(imports_db.connection, Scope(imports_db.org_b)).get(
            UUID(response.json()["id"])
        )
    assert error.value.code.value == "not-found"


def test_template_collection_and_download_are_guarded(imports_db, storage):
    # Templates are code shared across tenants, never tenant data.
    assert request(imports_db, storage, "GET", "/templates").status_code == 403
    assert request(imports_db, storage, "GET", "/templates/test_fixture/file").status_code == 403
    admin(imports_db)
    assert (
        request(imports_db, storage, "GET", "/templates/foreign_template/file").status_code == 404
    )


@pytest.mark.parametrize(
    "permissions", [(), ("import.read",), ("import.run",), ("import.run", "import.read")]
)
def test_permission_roles(imports_db, storage, permissions):
    if permissions:
        grant_permissions(imports_db, imports_db.actor_id, *permissions)
    response = upload(imports_db, storage)
    assert response.status_code == (201 if "import.run" in permissions else 403)
    catalog = request(imports_db, storage, "GET", "/templates")
    assert catalog.status_code == (200 if "import.read" in permissions else 403)


def test_record_permission_outcomes(imports_db, storage):
    admin(imports_db)
    id = upload(imports_db, storage).json()["id"]
    viewer = insert_identity(imports_db, "no-grant@example.test")
    for method, path, body in [
        ("GET", f"/{id}", None),
        ("PUT", f"/{id}/mapping", {"mapping": {"code": "code"}}),
    ]:
        assert (
            request(imports_db, storage, method, path, data=body, viewer=viewer).status_code == 404
        )
    grant_permissions(imports_db, viewer, "audit.read")
    for method, path, body in [
        ("GET", f"/{id}", None),
        ("PUT", f"/{id}/mapping", {"mapping": {"code": "code"}}),
    ]:
        assert (
            request(imports_db, storage, method, path, data=body, viewer=viewer).status_code == 403
        )


@pytest.mark.parametrize(
    "kind,value",
    [
        ("decimal", "1,234.56"),
        ("decimal", "NaN"),
        ("decimal", "1e2"),
        ("decimal", " 1"),
        ("decimal", "١"),
        ("integer", "1.0"),
        ("date", "20261008"),
        ("date", "2026-02-30"),
        ("date", "2026-10-08T00:00:00"),
    ],
)
def test_parsers_reject_noncanonical_values(kind, value):
    with pytest.raises(ValueError):
        parse_value(kind, value)


def test_parsers_return_exact_native_types():
    from datetime import date
    from decimal import Decimal

    assert parse_value("decimal", "1234.56") == Decimal("1234.56")
    assert parse_value("integer", "-123") == -123
    assert parse_value("date", "2026-10-08") == date(2026, 10, 8)
    for kind in ("text", "code", "enum"):
        assert parse_value(kind, "A") == "A"


def test_payload_too_large_http_handler():
    app = FastAPI()

    @app.get("/too-large")
    def endpoint():
        raise HTTPException(413, detail="unsafe server path")

    install_problem_details(app)
    response = TestClient(app).get("/too-large")
    assert response.status_code == 413
    assert response.json()["type"].endswith("/payload-too-large")
    assert "unsafe" not in response.text
    assert response.json()["recovery"]


def test_migration_up_down_preserves_preexisting_data(org_database):
    db = org_database
    c = db.connection
    before = c.execute("SELECT id,name FROM organization ORDER BY id").fetchall()
    migration = load_migration(ROOT / "migrations/20261008_0027_import_batch.py", "round_trip")
    migration.upgrade(c)
    try:
        assert c.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname='import_batch'"
        ).fetchone() == (True, True)
        for status in (
            "uploaded",
            "validating",
            "validated",
            "failed_validation",
            "committing",
            "committed",
            "failed",
            "cancelled",
        ):
            with tenant_transaction(c, Scope(db.org_a)):
                c.execute(
                    "INSERT INTO import_batch(id,org_id,template,template_version,uploader_id,"
                    "file_sha256,status) VALUES(gen_random_uuid(),%s,'test_fixture',1,%s,%s,%s)",
                    (db.org_a, db.actor_id, "a" * 64, status),
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            with tenant_transaction(c, Scope(db.org_a)):
                c.execute(
                    "INSERT INTO import_batch(id,org_id,template,template_version,uploader_id,"
                    "file_sha256,status) VALUES(gen_random_uuid(),%s,'test_fixture',1,%s,%s,'bad')",
                    (db.org_a, db.actor_id, "a" * 64),
                )
    finally:
        migration.downgrade(c)
    assert c.execute("SELECT id,name FROM organization ORDER BY id").fetchall() == before
    assert c.execute("SELECT to_regclass('import_batch')").fetchone() == (None,)


def test_import_rls_rejects_foreign_reads_and_writes(imports_db, storage):
    from uuid import uuid4

    from psycopg import sql

    db = imports_db
    foreign = foreign_batch(db, storage)
    role = "imports_rls_" + uuid4().hex
    c = db.connection
    c.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        c.execute(sql.SQL("GRANT SELECT,INSERT ON import_batch TO {}").format(sql.Identifier(role)))
        c.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(c, Scope(db.org_a)):
            assert (
                c.execute("SELECT id FROM import_batch WHERE id=%s", (foreign,)).fetchone() is None
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_transaction(c, Scope(db.org_a)):
                c.execute(
                    "INSERT INTO import_batch(id,org_id,template,template_version,uploader_id,"
                    "file_sha256) VALUES(gen_random_uuid(),%s,'test_fixture',1,%s,%s)",
                    (db.org_b, db.actor_id, "a" * 64),
                )
    finally:
        c.execute("RESET ROLE")
        c.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.parametrize(
    "body,ctype,status",
    [
        (b"", "text/plain", 415),
        (b"", "multipart/form-data", 415),
        (b"--x\r\n", "multipart/form-data; boundary=x", 422),
        (
            b'--x\r\nContent-Disposition: form-data; name="bad"\r\n\r\ndata\r\n--x--',
            "multipart/form-data; boundary=x",
            422,
        ),
        (
            b'--x\r\nContent-Disposition: form-data; name="file"; filename="x.csv"'
            b"\r\n\r\ndata\r\n--x--",
            "multipart/form-data; boundary=x",
            422,
        ),
    ],
)
def test_malformed_multipart_rejected(body, ctype, status):
    app = FastAPI()

    @app.post("/upload")
    async def endpoint(request: Request):
        await read_upload(request)

    install_problem_details(app)
    result = TestClient(app).post("/upload", content=body, headers={"Content-Type": ctype})
    assert result.status_code == status


def test_multipart_chunk_boundaries_and_file_first():
    data = b"code\nA\r\n--smallXfalse\n"
    body = (
        b'--small\r\nContent-Disposition: form-data; name="file"; filename="x.csv"\r\n'
        b"Content-Type: text/csv\r\n\r\n"
        + data
        + b'\r\n--small\r\nContent-Disposition: form-data; name="template"\r\n\r\n'
        b"test_fixture\r\n--small--\r\n"
    )
    offset = 0

    async def receive():
        nonlocal offset
        fragment = body[offset : offset + 1]
        offset += 1
        return {"type": "http.request", "body": fragment, "more_body": offset < len(body)}

    scope = {"type": "http", "headers": [(b"content-type", b"multipart/form-data; boundary=small")]}
    payload = asyncio.run(read_upload(Request(scope, receive)))
    assert payload.data == data
    assert payload.checksum == sha256(data).hexdigest()
    assert payload.template == "test_fixture"


@pytest.mark.parametrize(
    "body,boundary",
    [
        (b"", "b" * 201),
        (b"wrong-start", "b"),
        (b"--b\r\n" + b"x" * 16385, "b"),
        (b"--b\r\n" + b"x" * 16385 + b"\r\n\r\n", "b"),
        (
            b'--b\r\nContent-Disposition: form-data; name="template"\r\n\r\n'
            + b"x" * 1025
            + b"\r\n--b--",
            "b",
        ),
        (
            b'--b\r\nContent-Disposition: form-data; name="template"\r\n\r\nx\r\n--b\r\n'
            b'Content-Disposition: form-data; name="template"\r\n\r\nx\r\n--b--',
            "b",
        ),
        (b'--b\r\nContent-Disposition: form-data; name="template"\r\n\r\nx\r\n--b--TRAILING', "b"),
    ],
)
def test_multipart_guards_reject_violations(body, boundary):
    from flo.kernel.errors import ProblemError

    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "headers": [(b"content-type", ("multipart/form-data; boundary=" + boundary).encode())],
    }
    with pytest.raises(ProblemError):
        asyncio.run(read_upload(Request(scope, receive)))


def test_template_registry_guards():
    from flo.modules.imports.schemas import Column, Template
    from flo.modules.imports.templates import REGISTRY, approved_custom_fields, register

    assert approved_custom_fields() == ()
    with pytest.raises(ValueError, match="already registered"):
        register(REGISTRY["test_fixture"])
    for template in (
        Template(
            name="bad",
            version=1,
            columns=(Column(name="a", type="text"), Column(name="a", type="text")),
        ),
        Template(name="bad", version=1, columns=(), key_columns=("absent",)),
    ):
        with pytest.raises(ValueError, match="invalid template"):
            register(template)
    assert "bad" not in REGISTRY


def test_exact_file_size_cap_is_allowed(imports_db, storage):
    admin(imports_db)
    data = b"code\n" + b"A" * (MAX_FILE_SIZE - 6) + b"\n"
    response = upload(imports_db, storage, data)
    assert response.status_code == 201, response.text
    assert response.json()["file_size"] == MAX_FILE_SIZE


def test_storage_object_removed_if_database_write_fails(imports_db, storage, monkeypatch):
    from flo.modules.imports.multipart import Upload

    service = ImportService(
        imports_db.connection, Scope(imports_db.org_a), imports_db.actor_id, storage
    )

    def fail(*args, **kwargs):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(service.repo, "execute", fail)
    with pytest.raises(RuntimeError):
        service.upload(Upload("test_fixture", "x.csv", "text/csv", CSV, sha256(CSV).hexdigest()))
    assert storage.objects == {}
    with tenant_transaction(imports_db.connection, Scope(imports_db.org_a)):
        assert imports_db.connection.execute("SELECT count(*) FROM import_batch").fetchone() == (0,)


@pytest.mark.parametrize(
    "status",
    [
        "validating",
        "validated",
        "failed_validation",
        "committing",
        "committed",
        "failed",
        "cancelled",
    ],
)
def test_mapping_rejects_non_uploaded_batch(imports_db, storage, status):
    admin(imports_db)
    batch = upload(imports_db, storage).json()
    with tenant_transaction(imports_db.connection, Scope(imports_db.org_a)):
        imports_db.connection.execute(
            "UPDATE import_batch SET status=%s WHERE id=%s", (status, batch["id"])
        )
    response = request(
        imports_db,
        storage,
        "PUT",
        "/" + batch["id"] + "/mapping",
        data={"mapping": {"code": "code"}},
    )
    assert response.status_code == 409, response.text
    assert response.json()["type"] == "https://xlr8flo.app/errors/conflict"
    assert response.json()["checks"]["problem"] == "batch_not_editable"
    saved = request(imports_db, storage, "GET", "/" + batch["id"]).json()
    assert saved["mapping"] == batch["mapping"]
    with tenant_transaction(imports_db.connection, Scope(imports_db.org_a)):
        assert (
            imports_db.connection.execute(
                "SELECT count(*) FROM audit_log WHERE action='import.mapping'"
            ).fetchone()[0]
            == 0
        )
