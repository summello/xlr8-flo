"""The award's Award Evidence payload and the approver's evaluator read (E12-S17, SRC-013)."""

import asyncio
from decimal import Decimal
from uuid import uuid4

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals import engine
from flo.modules.approvals.service import get_instance
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from flo.modules.sourcing.approval import AwardAdapter
from flo.modules.sourcing.evaluation import weighted
from flo.modules.sourcing.models import RfqRepository
from tests.isolation.test_real_sessions import client, login
from tests.requisitions.test_header import unit
from tests.sourcing.test_award import (
    awards,
    decide_award,
    pick,
    prepare,
    rfq_line_of,
    scene,
    submit,
)
from tests.sourcing.test_award import bootstrap_db as bootstrap_db  # noqa: F401
from tests.sourcing.test_award import empty_database as empty_database  # noqa: F401
from tests.sourcing.test_award import header_db as header_db  # noqa: F401
from tests.sourcing.test_award import lifecycle_db as lifecycle_db  # noqa: F401
from tests.sourcing.test_award import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_bids import second_user
from tests.sourcing.test_evaluation import evaluate, finish
from tests.sourcing.test_evaluation import scene as eval_scene
from tests.sourcing.test_rfq import send, sql
from tests.vendors.test_vendors import regrant


def instance_evidence(db, award_id):
    instance_id = sql(db, "SELECT id FROM approval_instance WHERE document_id=%s", (award_id,))[0][
        0
    ]
    return get_instance(db[0], Scope(db[1]), instance_id).evidence


def digest(db, award_id):
    with tenant_transaction(db[0], Scope(db[1])):
        return engine.material_digest(AwardAdapter().snapshot(db[0], Scope(db[1]), award_id))


def test_the_instance_carries_the_award_evidence_and_matches_the_award_read(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, _, [a, b] = scene(db, amount="1000", qty="10", prices=("80", "90"))
    line = rfq_line_of(db, rfq)
    prepare(db, rfq, pick(db, rfq, a, "6"), pick(db, rfq, b, "4", justification="Faster"))
    sql(
        db,
        "UPDATE award_line SET not_lowest=true, justification='Faster' WHERE vendor_id=%s "
        "AND rfq_line_id=%s RETURNING 1",
        (b, line),
    )
    award = submit(db, rfq)
    read = awards(db).read(rfq.id)
    evidence = instance_evidence(db, award.id)
    assert evidence is not None
    assert evidence["rfq_id"] == str(rfq.id) and evidence["basis"] == "manual"
    assert evidence["currency"] == "USD"
    assert evidence["requisitions"] == [
        {
            "requisition_id": str(c.requisition_id),
            "requisition": c.requisition,
            "scope": c.scope,
            "award": c.award,
            "over_scope_by": c.over_scope_by,
            "release": c.release,
            "return_project": c.main_project,
        }
        for c in read.checks
    ]
    assert "funds" not in str(evidence["requisitions"]) and "over_funds_by" not in str(evidence)
    totals = {t["vendor_id"]: t for t in evidence["totals_by_vendor"]}
    assert set(totals) == {str(a), str(b)}
    assert all(t["vendor_name"] in {"A", "B"} for t in totals.values())
    assert sum(Decimal(t["total"]) for t in totals.values()) == Decimal(read.total)
    assert sum(Decimal(t["budget_total"]) for t in totals.values()) == Decimal(read.budget_total)
    assert [
        (n["line_no"], n["vendor_name"], n["justification"]) for n in evidence["not_lowest"]
    ] == [(1, "B", "Faster")]
    assert evidence["tie_overrides"] == [] and evidence["rationale"] == "Best value"


def test_budget_total_differs_from_total_when_tax_does_not_consume_budget(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db, prices=("80", "99"), tax="40", freight="20")
    award = prepare(db, rfq, pick(db, rfq, a, "5"))
    org = OrgService(db[0], Scope(db[1]), db[3])
    org.set_setting("tax.consumes_budget", SettingPut(value=False))
    org.set_setting("freight.consumes_budget", SettingPut(value=False))
    [t] = awards(db).evidence(award.id)["totals_by_vendor"]
    read = awards(db).read(rfq.id)
    assert (t["total"], t["budget_total"]) == ("430.00", "400.00")
    assert (t["total"], t["budget_total"]) == (read.total, read.budget_total)


def test_the_material_digest_is_unchanged_by_approval_and_scores_stay_frozen(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, b] = scene(db, amount="1000", qty="10", prices=("90", "99"))
    for n, (name, weight) in enumerate((("Quality", 60), ("Price", 40)), 1):
        sql(
            db,
            "INSERT INTO rfq_criterion(id,org_id,rfq_id,position,name,weight) "
            "VALUES(%s,%s,%s,%s,%s,%s) RETURNING 1",
            (uuid4(), db[1], rfq.id, n, name, weight),
        )
    evaluate(db, rfq, db[3])
    finish(db, rfq, [a, b], db[3], marks=(7, 3))
    prepare(db, rfq, pick(db, rfq, a, "10"))  # b was scored but is not awarded
    award = submit(db, rfq)
    before = digest(db, award.id)
    frozen = sql(db, "SELECT evaluator_results FROM award WHERE id=%s", (award.id,))[0][0]
    assert frozen["weighted"] and set(frozen["weighted"]) == {str(a), str(b)}
    # a live change to the scoring moves what a recompute would say, never the frozen figures
    sql(db, "UPDATE rfq_criterion SET weight=100-weight WHERE rfq_id=%s RETURNING 1", (rfq.id,))
    live = weighted(RfqRepository(db[0], Scope(db[1])), rfq.id)
    assert (
        live is not None and {str(k): format(v, "f") for k, v in live.items()} != frozen["weighted"]
    )
    evidence = awards(db).evidence(award.id)
    assert evidence["evaluator_results"] == frozen
    assert digest(db, award.id) == before
    names = evidence["vendor_names"]
    assert names[str(b)] == "B" and names[str(a)] == "A"  # the unawarded bidder is named
    decide_award(db, award.id)
    assert digest(db, award.id) == before  # approval released funds; nothing in the digest moved


def test_evaluators_with_declarations_reach_edit_and_approve_holders_never_view_only(
    lifecycle_db,
):
    db, _ = lifecycle_db
    rfq, _ = eval_scene(db)
    evaluate(db, rfq, db[3])
    cases = [
        (["rfq.view"], False),
        (["rfq.view", "rfq.edit"], True),
        (["rfq.view", "rfq.approve"], True),
    ]

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for grant, sees in cases:
                regrant(db, grant)
                response = await send(browser, "GET", f"/rfqs/{rfq.id}/evaluation")
                assert response.status_code == 200, response.text
                got = response.json()["evaluators"]
                assert (got is not None) is sees, grant
                if sees:
                    assert got[0]["declaration"] == "clear"

    asyncio.run(scenario())


def test_the_evaluation_read_conceals_a_foreign_tenants_rfq(lifecycle_db):
    db, _ = lifecycle_db
    foreign_unit = unit(db, "FBU", org=db[2])
    foreign = uuid4()
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign, db[2], foreign_unit.id, second_user(db)),
        )
    regrant(db, ["rfq.view", "rfq.approve"])

    async def read(rfq_id):
        async with client(app) as browser:
            await login(browser)
            return (await send(browser, "GET", f"/rfqs/{rfq_id}/evaluation")).status_code

    assert asyncio.run(read(foreign)) == 404
