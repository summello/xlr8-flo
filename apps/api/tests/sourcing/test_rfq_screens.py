"""The RFQ list, history and capability flags behind the RFQ screens (E12-S11: SRC-004/015)."""

import asyncio
from uuid import uuid4

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.sourcing.lifecycle import ExtendDeadline
from flo.modules.sourcing.service import _TONES
from tests.isolation.test_real_sessions import client, login
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_header import unit
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import send
from tests.sourcing.test_rfq_lifecycle import life, publish, ready
from tests.vendors.test_vendors import regrant


def test_a_closed_rfq_carries_the_info_tone():
    assert _TONES["closed"] == "info"  # MASTER §2.3 wins over the first draft of the provider


def session(steps):
    """Log in once (a second login meets the MFA challenge), then run `steps(fetch)`."""

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            return await steps(lambda suffix: send(browser, "GET", suffix))

    return asyncio.run(scenario())


def test_the_list_shows_only_units_where_rfq_view_is_held_and_counts_match(lifecycle_db):
    db, _ = lifecycle_db
    mine, _, _ = publish(db)
    other, _, _ = ready(db)  # a draft in another unit
    foreign_unit = unit(db, "FBU", org=db[2])
    owner = db[0].execute("SELECT id FROM identity WHERE email='second@example.test'").fetchone()[0]
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (uuid4(), db[2], foreign_unit.id, owner),
        )
    paths = (
        "/rfqs",
        "/rfqs?tab=drafts",
        f"/rfqs?q={mine.number[:6].lower()}",
        "/rfqs?q=zzz",
        "/rfqs?q=umps",
        "/rfqs?page_size=1",
        "/rfqs?cursor=nope",
    )

    async def steps(fetch):
        regrant(db, ["rfq.view"], AuthorizationTarget(ScopeType.BU, mine.unit_id))
        scoped = await fetch("/rfqs")
        regrant(db, ["rfq.view"])
        everything = [await fetch(path) for path in paths]
        cursor = everything[5].json()["next_cursor"]
        return scoped, everything, await fetch(f"/rfqs?page_size=1&cursor={cursor}")

    scoped, (every, drafts, prefix, none, substring, first, bad), second = session(steps)
    assert scoped.status_code == 200, scoped.text
    assert [r["id"] for r in scoped.json()["rows"]] == [str(mine.id)]
    assert scoped.json()["counts"] == {
        "drafts": 0,
        "open": 1,
        "evaluating": 0,
        "awarded": 0,
        "all": 1,
    }
    row = scoped.json()["rows"][0]
    assert row["requisition_numbers"] and row["invitee_count"] == 1
    assert {r["id"] for r in every.json()["rows"]} == {str(mine.id), str(other.id)}
    assert every.json()["counts"]["drafts"] == every.json()["counts"]["open"] == 1
    assert [r["id"] for r in drafts.json()["rows"]] == [str(other.id)]
    assert len(prefix.json()["rows"]) == 2 and none.json()["rows"] == []
    assert substring.json()["rows"] == []  # q is a number or title prefix, not a substring
    assert len(first.json()["rows"]) == 1 and first.json()["next_cursor"]
    assert {first.json()["rows"][0]["id"], second.json()["rows"][0]["id"]} == {
        str(mine.id),
        str(other.id),
    }
    assert second.json()["next_cursor"] is None
    assert bad.status_code == 422


def test_screens_routes_conceal_foreign_tenants(header_db):
    db = header_db
    foreign_unit = unit(db, "FBU", org=db[2])
    foreign_rfq = uuid4()
    owner = db[0].execute("SELECT id FROM identity WHERE email='second@example.test'").fetchone()[0]
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign_rfq, db[2], foreign_unit.id, owner),
        )

    async def steps(fetch):
        return await fetch(f"/rfqs/{foreign_rfq}/history"), await fetch("/rfqs")

    history, listed = session(steps)
    assert history.status_code == 404
    assert listed.status_code == 200 and listed.json()["rows"] == []


def test_history_lists_the_rfq_events_newest_first_and_flags_follow_the_grants(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _ = publish(db)
    life(db).extend(
        rfq.id,
        ExtendDeadline(deadline_at=rfq.deadline_at.replace(year=2099), reason="Vendors asked"),
    )

    async def steps(fetch):
        history, full = await fetch(f"/rfqs/{rfq.id}/history"), await fetch(f"/rfqs/{rfq.id}")
        regrant(db, ["rfq.view"])
        return history.json(), full.json(), (await fetch(f"/rfqs/{rfq.id}")).json()

    history, detail, viewer = session(steps)
    assert [e["action"] for e in history][:2] == ["rfq.extend", "rfq.publish"]
    assert history[0]["reason"] == "Vendors asked" and history[0]["actor_email"]
    assert set(history[0]) == {"action", "at", "actor_email", "actor_name", "reason"}
    assert detail["can_edit"] is True and detail["can_approve"] is True
    assert viewer["can_edit"] is False and viewer["can_approve"] is False
