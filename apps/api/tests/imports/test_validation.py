"""Dry-run row issues, streaming CSV, pagination and production guards."""

import asyncio
import builtins
import csv
import io
from hashlib import sha256
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.imports.handlers import HANDLERS, get_handler, register_handler
from flo.modules.imports.multipart import Upload
from flo.modules.imports.schemas import Column
from flo.modules.imports.service import ImportService
from flo.modules.imports.templates import REGISTRY
from tests.isolation.test_real_sessions import client, headers, login


async def send(browser, method, id, suffix, body=None, key=None):
    return await browser.request(
        method,
        f"/api/v1/imports/{id}/{suffix}",
        json=body,
        headers=headers(browser, key or uuid4().hex),
    )


async def upload(browser, rows, header=("key", "value", "kind")):
    output = io.StringIO(newline="")
    csv.writer(output).writerows([header, *rows])
    response = await browser.post(
        "/api/v1/imports",
        data={"template": "probe"},
        files={"file": ("probe.csv", output.getvalue().encode(), "text/csv")},
        headers=headers(browser, uuid4().hex),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_all_issues_dry_run_mapping_csv_and_revalidate(probe):
    conn, org, _, _, _, _ = probe
    # S01 rejects mapped malformed types at upload; map an unmapped optional column
    # in the persisted fixture to exercise S02's independent parser as well.
    REGISTRY["probe"] = REGISTRY["probe"].model_copy(
        update={"columns": REGISTRY["probe"].columns + (Column(name="number", type="integer"),)}
    )
    rows = [[f"K{i}", "ok", "", ""] for i in range(100)]
    rows[0] = ["", "", "bad-code", ""]
    rows[1][2] = "bad-enum"
    rows[2][2] = "missing"
    rows[3][2] = "business"
    rows[4][0] = "K5"
    rows[6][3] = "1e2"
    rows[7][1] = "warn"
    rows[8] = ['=HYPERLINK("http://x")', "ok", "bad-code", ""]

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, rows, ("key", "value", "kind", "Unmapped"))
            with tenant_transaction(conn, Scope(org)):
                conn.execute(
                    "UPDATE import_batch SET mapping=mapping || %s::jsonb WHERE id=%s",
                    ('{"number":"Unmapped"}', id),
                )
            before = conn.execute("SELECT count(*) FROM import_probe").fetchone()
            response = await send(browser, "POST", id, "validate", key="validate-once")
            assert response.status_code == 200, response.text
            batch = response.json()
            assert batch["status"] == "failed_validation"
            assert batch["counts"] == {
                "rows": 100,
                "create": 93,
                "update": 0,
                "skip": 0,
                "warning": 1,
                "error": 7,
            }
            assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == before
            assert (
                await send(browser, "POST", id, "validate", key="validate-once")
            ).json() == batch
            errors = (await send(browser, "GET", id, "preview?kind=error")).json()["rows"]
            codes = {issue["code"] for row in errors for issue in row["issues"]}
            assert codes == {
                "required_cell",
                "invalid_value",
                "duplicate_key",
                "invalid_code",
                "invalid_enum",
                "reference_missing",
                "business_rule",
            }
            assert [row["row_no"] for row in errors] == [2, 3, 4, 5, 7, 8, 10]
            csv_response = await send(browser, "GET", id, "errors.csv")
            assert csv_response.status_code == 200
            assert csv_response.headers["content-type"] == "text/csv; charset=utf-8"
            assert (
                csv_response.headers["content-disposition"]
                == f'attachment; filename="import-{id}-errors.csv"'
            )
            records = list(csv.DictReader(io.StringIO(csv_response.text)))
            assert {item["code"] for item in records} == codes | {"owner_changed"}
            assert (
                next(item for item in records if item["row_no"] == "10")["original_value"]
                == "bad-code"
            )
            again = await send(browser, "POST", id, "validate")
            assert again.status_code == 200
            with tenant_transaction(conn, Scope(org)):
                assert conn.execute(
                    "SELECT count(*) FROM import_row WHERE batch_id=%s", (id,)
                ).fetchone() == (100,)

    asyncio.run(scenario())


def test_csv_injection(probe):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            dangerous = ['=HYPERLINK("http://x")', "+SUM(A1)", "-1", "@call", "\tcell", "\rcell"]
            id = await upload(browser, [[value, "warn", ""] for value in dangerous])
            # Required values produce an issue at the key cell, preserving originals.
            from flo.modules.imports.handlers import Issue, RowPlan

            handler = HANDLERS["probe"]
            original = handler.plan_row

            def plan(context, number, values):
                result = original(context, number, values)
                return RowPlan(
                    "error", (Issue("key", "test_cell", "Unsafe input."),), result.preview, None
                )

            handler.plan_row = plan
            await send(browser, "POST", id, "validate")
            response = await send(browser, "GET", id, "errors.csv")
            assert [
                row["original_value"] for row in csv.DictReader(io.StringIO(response.text))
            ] == ["'" + value for value in dangerous]

    asyncio.run(scenario())


def test_page_5000_rows_and_inline_limit(probe):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [[str(i), "ok", ""] for i in range(5000)])
            response = await send(browser, "POST", id, "validate")
            assert response.status_code == 200, response.text
            numbers = []
            suffix = "preview?page_size=50"
            while True:
                page = (await send(browser, "GET", id, suffix)).json()
                numbers.extend(row["row_no"] for row in page["rows"])
                if not page["next_cursor"]:
                    break
                suffix = "preview?page_size=50&cursor=" + page["next_cursor"]
            assert numbers == list(range(2, 5002))
            assert (await send(browser, "GET", id, "preview?page_size=51")).status_code == 422
            assert (await send(browser, "GET", id, "preview?cursor=invalid")).status_code == 422
            big = await upload(browser, [[str(i), "ok", ""] for i in range(5001)])
            rejected = await send(browser, "POST", big, "validate")
            assert rejected.status_code == 422
            assert rejected.json()["checks"]["problem"] == "file_too_large_for_inline"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("POST", "validate", None),
        ("GET", "preview", None),
        ("GET", "errors.csv", None),
        ("POST", "commit", {"mode": "atomic"}),
    ],
)
def test_foreign_batch_routes(probe, method, suffix, body):
    conn, _, foreign, actor, storage, _ = probe
    data = b"key,value,kind\nA,ok,\n"
    id = (
        ImportService(conn, Scope(foreign), actor, storage)
        .upload(Upload("probe", "data.csv", "text/csv", data, sha256(data).hexdigest()))
        .id
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert (await send(browser, method, id, suffix, body)).status_code == 404

    asyncio.run(scenario())


def test_handler_registry_guards(probe):
    handler = HANDLERS["probe"]
    with pytest.raises(ValueError, match="already registered"):
        register_handler(handler)
    handler.name = "absent"
    with pytest.raises(ValueError, match="not registered"):
        register_handler(handler)
    with pytest.raises(Exception) as error:
        get_handler("absent")
    assert error.value.code.value == "not-found"


@pytest.mark.parametrize("scope_grant", [True, False])
def test_batch_permission_guards(probe, scope_grant):
    conn, org, _, actor, _, _ = probe

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [["A", "ok", ""]])
            with tenant_transaction(conn, Scope(org)):
                if scope_grant:
                    conn.execute(
                        "DELETE FROM role_permission "
                        "WHERE permission_code IN ('import.read','import.run')"
                    )
                else:
                    conn.execute("DELETE FROM user_role WHERE user_id=%s", (actor,))
            for method, suffix, body in [
                ("POST", "validate", None),
                ("POST", "commit", {"mode": "atomic"}),
                ("GET", "preview", None),
                ("GET", "errors.csv", None),
            ]:
                response = await send(browser, method, id, suffix, body)
                assert response.status_code == (403 if scope_grant else 404), response.text

    asyncio.run(scenario())


def test_all_preview_classifications_and_scratch_passes(probe, monkeypatch):
    conn, org, _, _, _, handler = probe
    conn.execute(
        "INSERT INTO import_probe VALUES(%s,%s,'U','before'),(%s,%s,'S','same')",
        (uuid4(), org, uuid4(), org),
    )
    original = handler.plan_row
    passes = []

    def plan(context, number, values):
        if number == 2:
            assert context.scratch == {}
            passes.append(context.scratch)
        context.scratch.setdefault("keys", []).append(values.get("key"))
        return original(context, number, values)

    monkeypatch.setattr(handler, "plan_row", plan)
    original_apply = handler.apply_row

    def apply(context, row_plan, values):
        if "applied" not in context.scratch:
            assert context.scratch == {}
            passes.append(context.scratch)
            context.scratch["applied"] = []
        context.scratch["applied"].append(values["key"])
        return original_apply(context, row_plan, values)

    monkeypatch.setattr(handler, "apply_row", apply)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(
                browser,
                [["C", "warn", ""], ["U", "after", ""], ["S", "same", ""], ["E", "ok", "bad-code"]],
            )
            await send(browser, "POST", id, "validate")
            for kind, numbers in [
                ("create", [2]),
                ("warning", [2]),
                ("update", [3]),
                ("skip", [4]),
                ("error", [5]),
            ]:
                result = await send(browser, "GET", id, "preview?kind=" + kind)
                assert [row["row_no"] for row in result.json()["rows"]] == numbers
            result = await send(browser, "POST", id, "commit", {"mode": "partial"})
            assert result.status_code == 200, result.text
            assert result.json()["result"]["committed_row_numbers"] == [2, 3]
            assert len(passes) == 3 and len({builtins.id(p) for p in passes}) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("held", ["import.read", "import.run"])
def test_read_run_permissions_are_independent(probe, held):
    conn, org, _, _, _, _ = probe

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [["A", "ok", ""]])
            with tenant_transaction(conn, Scope(org)):
                conn.execute(
                    "DELETE FROM role_permission WHERE permission_code=%s",
                    ("import.run" if held == "import.read" else "import.read",),
                )
            validated = await send(browser, "POST", id, "validate")
            assert validated.status_code == (200 if held == "import.run" else 403)
            for suffix in ("preview", "errors.csv"):
                response = await send(browser, "GET", id, suffix)
                assert response.status_code == (200 if held == "import.read" else 403)
            committed = await send(browser, "POST", id, "commit", {"mode": "atomic"})
            assert committed.status_code == (200 if held == "import.run" else 403)

    asyncio.run(scenario())
