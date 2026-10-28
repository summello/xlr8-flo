"""PO lifecycle: one transition function, the only status writer, and manual close (E13-S03)."""

import asyncio
import re
from pathlib import Path

from flo.api.health import app
from flo.kernel.errors import ErrorCode
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.purchasing.lifecycle import TRANSITIONS, transition
from tests.isolation.test_real_sessions import client, login
from tests.purchasing.test_purchase_orders import create, pos, ready, send
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lifecycle import sql
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import refused
from tests.vendors.test_vendors import regrant

STATUSES = (
    "draft approval_pending approved issued partially_delivered delivered accepted closed "
    "cancelled superseded deleted"
).split()
VALID = {  # written out here, not read from the table under test
    ("draft", "submit"): "approval_pending",
    ("approval_pending", "approve"): "approved",
    ("approval_pending", "reject"): "draft",
    ("approval_pending", "withdraw"): "draft",
    ("approved", "issue"): "issued",
    ("issued", "supersede"): "superseded",
    ("draft", "cancel"): "cancelled",
    ("approval_pending", "cancel"): "cancelled",
    ("approved", "cancel"): "cancelled",
    ("issued", "cancel"): "cancelled",
    ("issued", "close"): "closed",
    ("draft", "delete"): "deleted",
}
ACTIONS = sorted({action for _, action in VALID})
SRC = Path(__file__).parents[2] / "src"


def one(db):
    rfq, _, a, _, award_id, _ = ready(db)
    return create(db, rfq, a, award_id, "2")


def setstatus(db, po, status):
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE purchase_order SET status=%s WHERE id=%s", (status, po.id))


def move(db, po, action, version=None, reason=None):
    with correlation_context("po"), tenant_transaction(db[0], Scope(db[1])):
        return transition(
            db[0], Scope(db[1]), po.id, action, db[3],
            version or sql(db, "SELECT version FROM purchase_order WHERE id=%s", (po.id,))[0][0],
            reason,
        )  # fmt: skip


def test_every_status_and_action_pair_moves_or_is_refused(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    for status in STATUSES:
        for action in ACTIONS:
            setstatus(db, po, status)
            before = sql(db, "SELECT version FROM purchase_order WHERE id=%s", (po.id,))[0][0]
            target = VALID.get((status, action))
            if status == "deleted":  # a deleted PO is not found at all
                refused(lambda: move(db, po, action), ErrorCode.NOT_FOUND)
            elif target is None:
                error = refused(lambda: move(db, po, action))
                assert error.checks["problem"] == "invalid_transition" or error.detail
                assert sql(
                    db, "SELECT status, version FROM purchase_order WHERE id=%s", (po.id,)
                ) == [(status, before)]
            else:
                assert move(db, po, action) == target
                assert sql(
                    db, "SELECT status, version FROM purchase_order WHERE id=%s", (po.id,)
                ) == [(target, before + 1)]


def test_the_table_is_exactly_the_packet_table():
    assert TRANSITIONS == VALID


def test_transition_takes_the_packet_keyword_contract(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    setstatus(db, po, "approval_pending")
    with correlation_context("po"), tenant_transaction(db[0], Scope(db[1])):
        target = transition(db[0], Scope(db[1]), po.id, "approve", db[3], expected_version=1)
    assert target == "approved"


def test_the_status_enum_is_in_the_openapi_schema_and_matches_the_check():
    schemas = app.openapi()["components"]["schemas"]
    assert schemas["PoRead"]["properties"]["status"] == {"$ref": "#/components/schemas/PoStatus"}
    assert set(schemas["PoStatus"]["enum"]) == set(STATUSES)


def test_the_m3_actions_never_reach_the_delivery_statuses():
    assert not {"partially_delivered", "delivered", "accepted"} & set(TRANSITIONS.values())
    assert not {s for s, _ in TRANSITIONS} & {"partially_delivered", "delivered", "accepted"}


def test_a_stale_version_refuses_and_leaves_the_status(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    setstatus(db, po, "issued")
    error = refused(lambda: move(db, po, "close", version=99))
    assert error.detail and sql(db, "SELECT status FROM purchase_order WHERE id=%s", (po.id,)) == [
        ("issued",)
    ]


def test_only_lifecycle_writes_purchase_order_status():
    pattern = re.compile(r"UPDATE\s+purchase_order\s+SET[^\"]*\bstatus\b", re.I | re.S)
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if p.name != "lifecycle.py" and pattern.search(p.read_text())
    ]
    assert offenders == []


def test_close_needs_a_reason_the_permission_and_the_current_version(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    setstatus(db, po, "issued")
    url = f"/{po.id}/transitions"
    close = {"action": "close", "reason": "Fully delivered off-system"}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            ok = {"If-Match": '"1"'}
            assert (await send(browser, "POST", url, close)).status_code == 400  # no If-Match
            assert (await send(browser, "POST", url, close | {"reason": ""}, ok)).status_code == 422
            assert (await send(browser, "POST", url, {"action": "close"}, ok)).status_code == 422
            assert (
                await send(browser, "POST", url, close | {"action": "cancel"}, ok)
            ).status_code == 422
            stale = await send(browser, "POST", url, close, {"If-Match": '"7"'})
            assert stale.status_code == 409, stale.text
            assert sql(db, "SELECT status FROM purchase_order WHERE id=%s", (po.id,)) == [
                ("issued",)
            ]
            regrant(db, ["purchase_order.view", "purchase_order.create"])  # no close
            assert (await send(browser, "POST", url, close, ok)).status_code == 403
            regrant(db, ["purchase_order.view", "purchase_order.close"])
            done = await send(browser, "POST", url, close, ok)
            assert done.status_code == 200, done.text
            assert done.json()["status"] == "closed" and done.headers["ETag"] == '"2"'
            again = await send(browser, "POST", url, close, {"If-Match": '"2"'})
            assert again.status_code == 409  # closed has no close

    asyncio.run(scenario())
    assert sql(
        db,
        "SELECT count(*) FROM audit_log WHERE action='purchase_order.close' AND reason=%s",
        (close["reason"],),
    ) == [(1,)]


def test_draft_delete_goes_through_the_transition_and_audits_once(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    with correlation_context("po"):
        pos(db).delete(po.id, '"1"')
    assert sql(db, "SELECT status, version FROM purchase_order WHERE id=%s", (po.id,)) == [
        ("deleted", 2)
    ]
    assert sql(db, "SELECT count(*) FROM audit_log WHERE action='purchase_order.delete'") == [(1,)]
