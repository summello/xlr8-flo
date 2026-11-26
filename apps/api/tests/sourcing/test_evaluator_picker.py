"""E12-S20: the evaluator picker route and the inactive-identity refusal (SRC-011)."""

import asyncio
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from tests.isolation.test_real_sessions import client, login
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_header import unit
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_evaluation import ev, scene
from tests.sourcing.test_rfq import refused, send, sql
from tests.vendors.test_changes import make_actor
from tests.vendors.test_vendors import regrant


def named(db, permissions, name):
    who = make_actor(db, permissions)
    sql(db, "UPDATE identity SET display_name=%s WHERE id=%s RETURNING 1", (name, who))
    return who


def test_a_deactivated_identity_holding_rfq_view_is_not_eligible(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _ = scene(db)
    who = named(db, ["rfq.view"], "Gone Person")
    sql(
        db,
        "UPDATE identity SET status='deactivated', deactivated_at=now() WHERE id=%s RETURNING 1",
        (who,),
    )
    refused(lambda: ev(db).add_evaluator(rfq.id, who, may_view=True), ErrorCode.VALIDATION_FAILED)
    assert sql(db, "SELECT 1 FROM rfq_evaluator WHERE identity_id=%s", (who,)) == []


def test_the_picker_lists_active_rfq_view_holders_not_yet_on_the_rfq(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _ = scene(db)
    ana = named(db, ["rfq.view"], "Ana Picker")
    ben = named(db, ["rfq.view"], "Ben Picker")
    named(db, ["rfq.view"], "Cal Picker")  # deactivated below
    named(db, ["requisition.view"], "Dee Picker")  # no rfq.view
    sql(
        db,
        "UPDATE identity SET status='deactivated', deactivated_at=now() "
        "WHERE display_name='Cal Picker' RETURNING 1",
    )
    ev(db).add_evaluator(rfq.id, ben, may_view=True)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            base = f"/rfqs/{rfq.id}/evaluation/evaluators/eligible"
            ok = await send(browser, "GET", f"{base}?q=Picker")
            short = await send(browser, "GET", f"{base}?q=P")
            paged = await send(browser, "GET", f"{base}?q=Picker&limit=1")
            return ok, short, paged

    ok, short, paged = asyncio.run(scenario())
    assert [r["identity_id"] for r in ok.json()["rows"]] == [str(ana)], ok.text
    assert set(ok.json()["rows"][0]) == {"identity_id", "display_name", "email"}
    assert short.status_code == 422
    assert paged.json()["has_more"] is False


@pytest.mark.parametrize(
    ("grant", "status"), [(["rfq.view", "rfq.edit"], 200), (["rfq.view"], 403), (["rfq.edit"], 200)]
)
def test_the_picker_needs_rfq_edit(lifecycle_db, grant, status):
    db, _ = lifecycle_db
    rfq, _ = scene(db)
    regrant(db, grant)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            return await send(browser, "GET", f"/rfqs/{rfq.id}/evaluation/evaluators/eligible?q=ab")

    assert asyncio.run(scenario()).status_code == status


def test_the_picker_conceals_a_foreign_or_unknown_rfq(lifecycle_db):
    db, _ = lifecycle_db
    foreign = uuid4()
    foreign_unit = unit(db, "FBU", org=db[2])
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign, db[2], foreign_unit.id, db[3]),
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            return [
                (
                    await send(browser, "GET", f"/rfqs/{i}/evaluation/evaluators/eligible?q=ab")
                ).status_code
                for i in (foreign, uuid4())
            ]

    assert asyncio.run(scenario()) == [404, 404]


def test_the_picker_finds_holders_beyond_the_resolver_cap(lifecycle_db, monkeypatch):
    from flo.modules.identity import resolver

    db, _ = lifecycle_db
    rfq, _ = scene(db)
    for name in ("Pat A", "Pat B", "Pat C"):
        named(db, ["rfq.view"], name)
    monkeypatch.setattr(resolver, "HOLDER_CAP", 2)  # the approvals cap, shrunk

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            return await send(
                browser, "GET", f"/rfqs/{rfq.id}/evaluation/evaluators/eligible?q=Pat&limit=5"
            )

    names = [r["display_name"] for r in asyncio.run(scenario()).json()["rows"]]
    assert names == ["Pat A", "Pat B", "Pat C"]
