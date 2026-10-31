"""Awards: split, justification, scope and funds, release of the unused reservation (E12-S08)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import DecisionWrite, Outcome, OutcomeContext
from flo.modules.approvals.service import decide, get_instance
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from flo.modules.requisitions.links import links
from flo.modules.sourcing.approval import AwardAdapter
from flo.modules.sourcing.awards import (
    AwardCreate,
    AwardLineIn,
    AwardLinesPut,
    AwardPatch,
    AwardService,
    Quote,
    amounts,
)
from flo.modules.sourcing.schemas import RfqContentPatch
from tests.approvals.test_engine import graph
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.isolation.test_real_sessions import client, headers, login
from tests.requisitions.test_approval_effects import approve as approve_requisition
from tests.requisitions.test_approval_effects import drift, grant, linked, reviewer, state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import connection_url, project, unit
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_comparison import deadline_ago, quoted
from tests.sourcing.test_rfq import draft_rfq, entry, refused, send, sql
from tests.sourcing.test_rfq_content import invite, vendor
from tests.sourcing.test_rfq_lifecycle import life
from tests.vendors.test_vendors import regrant


def awards(db, actor=None, connection=None):
    return AwardService(connection or db[0], Scope(db[1]), actor or db[3], Settings())


def publish_workflow(db):
    workflow = definition(db, document_type="award")
    draft(db, workflow, graph())
    svc(db).publish(workflow.id, 1, True)
    grant(db, "rfq.approve")


def requisition(db, amount="1000", qty="10"):
    """An approved, project-linked requisition: one line of `qty` units worth `amount`, the
    whole amount reserved on its generated sub-project. Returns (row, line id)."""
    row = linked(
        db, project(db, unit(db, uuid4().hex)), price=amount, available=str(int(amount) * 3)
    )
    approve_requisition(db, row)
    line = sql(db, "SELECT id FROM requisition_line WHERE requisition_id=%s", (row.id,))[0][0]
    sql(db, "UPDATE requisition_line SET quantity=%s WHERE id=%s RETURNING 1", (qty, line))
    return state(db, row.id), line


def rfq_for(db, row, line, qty, names=("A", "B"), prices=("80", "90"), **bid):
    """A published RFQ for `qty` units of the line, a bid per vendor, evaluation open."""
    rfq = draft_rfq(db, entry(row, line, qty))
    life(db).update_content(
        rfq.id,
        RfqContentPatch(
            specifications="Spec",
            delivery_location="Dock",
            delivery_terms="DAP",
            terms="Net 30",
            deadline_at=datetime.now(UTC) + timedelta(days=3),
        ),
        str(rfq.edit_revision),
    )
    for name in names:
        invite(db, rfq, vendor(db, name))
    with correlation_context("publish"):
        rfq = life(db).publish(rfq.id)
    vids = [i.vendor_id for i in rfq.invitees]
    for vid, price in zip(vids, prices, strict=True):
        quoted(db, rfq, vid, "USD", price=price, **bid)
    deadline_ago(db, rfq, 1)
    sql(db, "UPDATE rfq SET status='under_evaluation' WHERE id=%s RETURNING 1", (rfq.id,))
    return rfq, vids


def scene(
    db, amount="1000", qty="10", names=("A", "B"), prices=("80", "90"), *, workflow=True, **bid
):
    if workflow:
        publish_workflow(db)
    row, line = requisition(db, amount, qty)
    rfq, vids = rfq_for(db, row, line, qty, names, prices, **bid)
    return rfq, row, line, vids


def rfq_line_of(db, rfq):
    return sql(db, "SELECT id FROM rfq_line WHERE rfq_id=%s ORDER BY line_no", (rfq.id,))[0][0]


def pick(db, rfq, vendor_id, qty, **extra):
    return AwardLineIn(rfq_line_id=rfq_line_of(db, rfq), vendor_id=vendor_id, quantity=qty, **extra)


def prepare(db, rfq, *picks, rationale="Best value"):
    """A draft award with exactly these lines and a rationale."""
    with correlation_context("award"):
        award = awards(db).draft(rfq.id, AwardCreate(basis="manual"))
        award = awards(db).put_lines(rfq.id, AwardLinesPut(lines=list(picks)), str(award.version))
        return awards(db).patch(rfq.id, AwardPatch(rationale=rationale), str(award.version))


def submit(db, rfq):
    with correlation_context("award"):
        return awards(db).submit(rfq.id)


def decide_award(db, award_id, outcome="approve", version=None):
    instance_id = sql(
        db,
        "SELECT id FROM approval_instance WHERE document_type='award' AND document_id=%s "
        "ORDER BY started_at DESC LIMIT 1",
        (award_id,),
    )[0][0]
    instance = get_instance(db[0], Scope(db[1]), instance_id)
    version = version or sql(db, "SELECT version FROM award WHERE id=%s", (award_id,))[0][0]
    with correlation_context("decide"):
        return decide(
            db[0],
            Scope(db[1]),
            task_id=instance.tasks[-1].id,
            actor_id=reviewer(db),
            body=DecisionWrite(
                outcome=outcome,
                document_version=version,
                comment="ok",
                reject_mode="return_to_draft" if outcome == "reject" else None,
            ),
        )


def ledger(db, kinds=("release", "transfer")):
    return sql(
        db,
        "SELECT entry_type::text, project_id, amount, idempotency_key FROM ledger_entry "
        "WHERE entry_type::text = ANY(%s) ORDER BY id",
        (list(kinds),),
    )


def sub_project(db, requisition_id):
    query = "SELECT id FROM project WHERE generated_by_requisition_id=%s"
    return sql(db, query, (requisition_id,))[0][0]


def remaining(db, requisition_id):
    return state(db, requisition_id).reservation.remaining


# -- the figures -------------------------------------------------------------------------


def test_partial_award_prorates_tax_and_freight_by_awarded_over_quoted_quantity():
    # 7 of 10 quoted units at 12.50: price 87.50; tax 30.00 -> 21.00; freight share 9.99 -> 6.993
    quote = Quote(Decimal(10), Decimal("12.50"), Decimal("30.00"), Decimal("9.99"))
    kwargs = {"tax_on": True, "freight_on": True}
    full_v, full_r, budget = amounts(quote, Decimal(7), "USD", Decimal("1.25"), "USD", **kwargs)
    assert full_v == Decimal("115.49")  # quantized once: 87.50 + 21.00 + 6.993 = 115.493
    assert full_r == Decimal("144.36") and budget == full_r  # 115.49 x 1.25 = 144.3625
    # decision 211: tax and freight leave the budget amount when their settings are off
    *_, no_tax = amounts(quote, Decimal(7), "USD", Decimal(1), "USD", tax_on=False, freight_on=True)
    *_, bare = amounts(quote, Decimal(7), "USD", Decimal(1), "USD", tax_on=False, freight_on=False)
    assert no_tax == Decimal("94.49") and bare == Decimal("87.50")


# -- the posting ---------------------------------------------------------------------------


def test_approval_releases_the_unused_reservation_once_and_returns_it_to_the_main_project(
    lifecycle_db,
):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, amount="1000", qty="10", prices=("90", "99"))
    main = row.project_id
    sub = sub_project(db, row.id)
    assert remaining(db, row.id) == "1000.0000"
    base = ledger(db)  # the requisition's own funding postings
    award = prepare(db, rfq, pick(db, rfq, a, "10"))  # 10 x 90 = 900 against a scope of 1,000
    assert award.checks[0].scope == "1000.00" and award.checks[0].award == "900.00"
    award = submit(db, rfq)
    assert award.status == "approval_pending" and ledger(db) == base
    decide_award(db, award.id)
    assert awards(db).read(rfq.id).status == "approved"
    entries = ledger(db)[len(base) :]
    assert [(e[0], e[1], e[2]) for e in entries] == [
        ("release", sub, Decimal("-100.0000")),
        ("transfer", sub, Decimal("-100.0000")),
        ("transfer", main, Decimal("100.0000")),
    ]
    assert remaining(db, row.id) == "900.0000"
    assert drift(db) == []
    assert state(db, row.id).status == "open_for_purchase"  # fully awarded (decision 206)
    assert sql(db, "SELECT status FROM rfq WHERE id=%s", (rfq.id,)) == [("awarded",)]
    # a retried decision, straight at the adapter, posts nothing new (FIN-011)
    context = OutcomeContext(db[3], None, None, lambda *_: True)
    AwardAdapter().on_outcome(db[0], Scope(db[1]), award.id, Outcome.APPROVED, context)
    assert ledger(db)[len(base) :] == entries and sql(
        db, "SELECT awarded_qty FROM requisition_line"
    ) == [(Decimal("10.0000"),)]


def problem(call, code=ErrorCode.CONFLICT):
    return refused(call, code).checks["problem"]


# -- split, justification, ties ------------------------------------------------------------


def test_one_line_goes_to_two_vendors_by_quantity_and_over_the_rfq_quantity_is_422(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, b] = scene(db)
    line = rfq_line_of(db, rfq)
    award = prepare(db, rfq, pick(db, rfq, a, "6"), pick(db, rfq, b, "4"))
    assert sorted((ln.vendor_id, ln.quantity) for ln in award.lines) == sorted(
        [(a, "6.0000"), (b, "4.0000")]
    )
    assert award.checks[0].award == "840.00" and award.checks[0].scope == "1000.00"  # 6x80 + 4x90
    put = AwardLinesPut(lines=[pick(db, rfq, a, "6"), pick(db, rfq, b, "5")])
    error = refused(
        lambda: awards(db).put_lines(rfq.id, put, str(award.version)), ErrorCode.VALIDATION_FAILED
    )
    assert [e.field for e in error.errors] == [f"lines/{line}"] and "11" in error.errors[0].message
    stranger = AwardLinesPut(lines=[pick(db, rfq, uuid4(), "1")])
    error = refused(
        lambda: awards(db).put_lines(rfq.id, stranger, str(award.version)),
        ErrorCode.VALIDATION_FAILED,
    )
    assert error.errors[0].field.startswith(f"lines/{line}/")  # the vendor never quoted it
    assert awards(db).read(rfq.id).version == award.version  # nothing was stored


def test_a_stale_or_missing_if_match_never_changes_the_draft(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db)
    award = prepare(db, rfq, pick(db, rfq, a, "10"))
    put = AwardLinesPut(lines=[pick(db, rfq, a, "5")])
    assert problem(lambda: awards(db).put_lines(rfq.id, put, str(award.version - 1))) == (
        "stale_version"
    )
    assert problem(lambda: awards(db).put_lines(rfq.id, put, None), ErrorCode.BAD_REQUEST) == (
        "if_match_required"
    )
    assert awards(db).read(rfq.id).lines[0].quantity == "10.0000"


def test_a_non_lowest_line_needs_a_justification_when_the_setting_is_on_and_not_when_off(
    lifecycle_db,
):
    db, _ = lifecycle_db
    rfq, _, _, [a, b] = scene(db)  # a quotes 80, b quotes 90: b is not the lowest
    award = prepare(db, rfq, pick(db, rfq, b, "10"))
    assert award.lines[0].not_lowest is True
    line = rfq_line_of(db, rfq)
    error = refused(lambda: submit(db, rfq), ErrorCode.VALIDATION_FAILED)
    assert [e.field for e in error.errors] == [f"lines/{line}/{b}"]
    assert awards(db).read(rfq.id).status == "draft"
    with correlation_context("award"):
        put = AwardLinesPut(lines=[pick(db, rfq, b, "10", justification="Local stock")])
        awards(db).put_lines(rfq.id, put, str(award.version))
    assert submit(db, rfq).status == "approval_pending"


def test_the_justification_is_not_required_when_the_org_turns_the_setting_off(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [_a, b] = scene(db)
    key = "sourcing.require_non_lowest_justification"
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(key, SettingPut(value=False))
    prepare(db, rfq, pick(db, rfq, b, "10"))
    assert submit(db, rfq).status == "approval_pending"


def test_only_the_earliest_of_tied_vendors_is_awarded_without_a_reason(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [first, second] = scene(db, prices=("80", "80"))
    line = rfq_line_of(db, rfq)
    award = prepare(db, rfq, pick(db, rfq, second, "10"))
    assert award.lines[0].not_lowest is False  # tied at the lowest, so no justification
    error = refused(lambda: submit(db, rfq), ErrorCode.VALIDATION_FAILED)
    assert [e.field for e in error.errors] == [f"lines/{line}/{second}"]
    with correlation_context("award"):
        put = AwardLinesPut(lines=[pick(db, rfq, second, "10", tie_override_reason="Nearer")])
        awards(db).put_lines(rfq.id, put, str(award.version))
        award = awards(db).read(rfq.id)
    assert submit(db, rfq).status == "approval_pending"
    # the earliest needs none
    rfq2, _, _, [one, _two] = scene(db, prices=("70", "70"), workflow=False)
    prepare(db, rfq2, pick(db, rfq2, one, "10"))
    assert submit(db, rfq2).status == "approval_pending"
    assert first != second


# -- the guards ------------------------------------------------------------------------


def test_submit_without_a_published_award_workflow_is_a_409_and_writes_nothing(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db, workflow=False)
    prepare(db, rfq, pick(db, rfq, a, "10"))
    base = ledger(db, ("release", "transfer", "reservation"))
    assert problem(lambda: submit(db, rfq)) == "workflow_not_set_up"
    assert awards(db).read(rfq.id).status == "draft"
    assert sql(db, "SELECT count(*) FROM approval_instance WHERE document_type='award'") == [(0,)]
    assert ledger(db, ("release", "transfer", "reservation")) == base


def test_over_scope_and_over_funds_are_409_with_the_figures(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, prices=("120", "130"))
    prepare(db, rfq, pick(db, rfq, a, "10"))  # 10 x 120 = 1,200 against 1,000
    error = refused(lambda: submit(db, rfq))
    assert error.checks == {
        "problem": "over_scope",
        "requisition": row.number,
        "scope": "1000.00",
        "funds": "1000.00",
        "award": "1200.00",
        "over_scope_by": "200.00",
        "over_funds_by": "200.00",
    }
    assert awards(db).read(rfq.id).status == "draft"


def test_over_funds_alone_is_a_409_at_submit_and_again_at_approval(lifecycle_db):
    from flo.modules.requisitions.funding import reduce_to

    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, prices=("90", "99"))
    prepare(db, rfq, pick(db, rfq, a, "10"))  # 900: inside the scope of 1,000
    award = submit(db, rfq)
    reduce_to(db[0], Scope(db[1]), row.id, new_amount=Decimal(700), version=99, actor_id=db[3])
    base = ledger(db)
    error = refused(lambda: decide_award(db, award.id))
    assert error.checks["over_funds_by"] == "200.00" and error.checks["over_scope_by"] == "0.00"
    assert awards(db).read(rfq.id).status == "approval_pending" and ledger(db) == base
    assert state(db, row.id).status == "sourced"


def test_the_draft_release_is_what_approval_posts_and_a_full_award_releases_nothing(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, amount="100", qty="1", prices=("80", "90"))
    draft = prepare(db, rfq, pick(db, rfq, a, "1"))  # scope 100, award 80
    [check] = draft.checks
    main = sql(
        db,
        "SELECT p.number FROM project p JOIN requisition r ON r.project_id=p.id WHERE r.id=%s",
        (row.id,),
    )[0][0]
    assert (check.release, check.main_project) == ("20.00", main)
    assert draft.justification_required in (True, False)
    base = ledger(db)
    decide_award(db, submit(db, rfq).id)
    posted = ledger(db)[len(base) :]
    assert [-e[2] for e in posted if e[0] == "release"] == [Decimal(check.release)]
    assert [e[2] for e in posted if e[0] == "transfer" and e[2] > 0] == [Decimal(check.release)]


def test_an_award_at_or_over_scope_shows_a_zero_release(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db, amount="100", qty="1", prices=("100", "110"))
    [check] = prepare(db, rfq, pick(db, rfq, a, "1")).checks
    assert check.release == "0.00"


# -- the outcomes ------------------------------------------------------------------------


def test_a_partly_awarded_requisition_stays_sourced_and_a_full_one_opens_for_purchase(
    lifecycle_db,
):
    db, _ = lifecycle_db
    rfq, row, line, [a, _b] = scene(db)
    prepare(db, rfq, pick(db, rfq, a, "6"))
    decide_award(db, submit(db, rfq).id)
    assert state(db, row.id).status == "sourced"
    assert sql(db, "SELECT awarded_qty FROM requisition_line WHERE id=%s", (line,)) == [
        (Decimal("6.0000"),)
    ]
    # 6 x 80 = 480 against a pro rata scope of 600: 120 came back
    assert remaining(db, row.id) == "880.0000" and drift(db) == []


@pytest.mark.parametrize("outcome", ["reject", "return"])
def test_rejection_and_return_put_the_award_back_to_draft_with_no_ledger_entry(
    lifecycle_db, outcome
):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db)
    prepare(db, rfq, pick(db, rfq, a, "10"))
    award = submit(db, rfq)
    base = ledger(db)
    decide_award(db, award.id, outcome)
    after = awards(db).read(rfq.id)
    assert after.status == "draft" and ledger(db) == base and remaining(db, row.id) == "1000.0000"
    assert sql(db, "SELECT status FROM rfq WHERE id=%s", (rfq.id,)) == [("under_evaluation",)]


def test_a_resubmitted_award_releases_under_a_new_version_key(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db)
    prepare(db, rfq, pick(db, rfq, a, "10"))
    first = submit(db, rfq)
    decide_award(db, first.id, "reject")
    second = submit(db, rfq)
    assert second.version == first.version + 2  # rejected (+1), submitted again (+1)
    decide_award(db, second.id)
    keys = [e[3] for e in ledger(db, ("release",)) if f"award:{second.id}:" in e[3]]
    assert keys == [f"req:{row.id}:release:1:reduce:award:{second.id}:{second.version}"]


def test_two_partial_awards_on_two_rfqs_of_one_requisition_each_release_and_return(
    lifecycle_db,
):
    db, _ = lifecycle_db
    publish_workflow(db)
    row, line = requisition(db, "1000", "10")
    first, [a1, _] = rfq_for(db, row, line, "5", prices=("90", "99"))
    second, [a2, _] = rfq_for(db, row, line, "5", prices=("80", "99"))
    base = ledger(db)
    for rfq, vendor_id in ((first, a1), (second, a2)):
        prepare(db, rfq, pick(db, rfq, vendor_id, "5"))
    one, two = submit(db, first), submit(db, second)
    assert one.version == two.version  # the same award version, on one requisition
    decide_award(db, one.id)
    decide_award(db, two.id)
    posted = ledger(db)[len(base) :]
    # 5 x 90 against 500 frees 50, 5 x 80 frees 100: two releases, two transfers back
    assert sorted(e[2] for e in posted if e[0] == "release") == [
        Decimal("-100.0000"),
        Decimal("-50.0000"),
    ]
    assert sorted(e[2] for e in posted if e[0] == "transfer" and e[2] > 0) == [
        Decimal("50.0000"),
        Decimal("100.0000"),
    ]
    assert remaining(db, row.id) == "850.0000" and drift(db) == []
    assert state(db, row.id).status == "open_for_purchase"


def test_two_awards_for_one_line_approved_at_once_let_exactly_one_win(lifecycle_db):
    db, _ = lifecycle_db
    publish_workflow(db)
    row, line = requisition(db, "1000", "10")
    first, [a1, _] = rfq_for(db, row, line, "10", prices=("90", "99"))
    # the line is fully sourced; reopen it so a second RFQ can carry the same line (rfq_line is
    # unique per RFQ only), then publish that one as well
    sql(db, "UPDATE requisition_line SET sourced_qty=0 WHERE id=%s RETURNING 1", (line,))
    sql(
        db,
        "UPDATE requisition SET status='sourcing_in_progress', version=%s WHERE id=%s RETURNING 1",
        (row.version, row.id),
    )
    second, [a2, _] = rfq_for(db, row, line, "10", prices=("90", "99"))
    base = ledger(db)
    one = prepare(db, first, pick(db, first, a1, "10"))
    two = prepare(db, second, pick(db, second, a2, "10"))
    ids = [submit(db, first).id, submit(db, second).id]
    assert one.id != two.id
    barrier = Barrier(2)

    # db[0] is one shared connection: read everything the threads need here, in this thread
    plans = []
    for award_id in ids:
        instance = sql(db, "SELECT id FROM approval_instance WHERE document_id=%s", (award_id,))
        version = sql(db, "SELECT version FROM award WHERE id=%s", (award_id,))[0][0]
        task = get_instance(db[0], Scope(db[1]), instance[0][0]).tasks[-1].id
        plans.append((task, version))
    actor = reviewer(db)

    def attempt(plan):
        task, version = plan
        with (
            psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
            correlation_context("award-race"),
        ):
            barrier.wait(timeout=10)
            try:
                decide(
                    conn,
                    Scope(db[1]),
                    task_id=task,
                    actor_id=actor,
                    body=DecisionWrite(outcome="approve", document_version=version),
                )
                return "success"
            except ProblemError as exc:
                return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, plans)) == ["over_awarded_quantity", "success"]
    assert len([e for e in ledger(db)[len(base) :] if e[0] == "release"]) == 1
    assert sql(db, "SELECT awarded_qty FROM requisition_line WHERE id=%s", (line,)) == [
        (Decimal("10.0000"),)
    ]
    assert drift(db) == []


# -- withdrawal ----------------------------------------------------------------------------


def test_withdraw_ends_the_approval_returns_the_award_to_draft_and_a_second_is_409(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db)
    prepare(db, rfq, pick(db, rfq, a, "10"))
    submit(db, rfq)
    base = ledger(db)
    with correlation_context("withdraw"):
        done = awards(db).withdraw(rfq.id)
    assert done.status == "draft" and ledger(db) == base
    assert sql(db, "SELECT status FROM approval_instance WHERE document_type='award'") == [
        ("withdrawn",)
    ]
    assert problem(lambda: awards(db).withdraw(rfq.id)) == "invalid_transition"


def test_the_generic_withdraw_route_needs_rfq_edit_on_the_rfqs_unit(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db)
    prepare(db, rfq, pick(db, rfq, a, "10"))
    award = submit(db, rfq)
    instance = sql(db, "SELECT id FROM approval_instance WHERE document_id=%s", (award.id,))[0][0]

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["rfq.view", "rfq.approve"])  # the requester may read, but not edit
            denied = await browser.request(
                "POST",
                f"/api/v1/approvals/instances/{instance}/withdraw",
                json={},
                headers=headers(browser, uuid4().hex),
            )
            assert denied.status_code == 403, denied.text
            assert sql(db, "SELECT status FROM approval_instance WHERE id=%s", (instance,)) == [
                ("running",)
            ]
            assert awards(db).read(rfq.id).status == "approval_pending"
            regrant(db, ["rfq.view", "rfq.edit"])
            allowed = await browser.request(
                "POST",
                f"/api/v1/approvals/instances/{instance}/withdraw",
                json={},
                headers=headers(browser, uuid4().hex),
            )
            assert allowed.status_code == 200, allowed.text

    asyncio.run(scenario())
    assert awards(db).read(rfq.id).status == "draft"


def test_an_award_for_a_requisition_without_a_sub_project_is_422_and_writes_nothing(
    lifecycle_db,
):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db)
    prepare(db, rfq, pick(db, rfq, a, "10"))
    sql(
        db,
        "UPDATE project SET generated_by_requisition_id=NULL WHERE generated_by_requisition_id=%s "
        "RETURNING 1",
        (row.id,),
    )
    error = refused(lambda: submit(db, rfq), ErrorCode.VALIDATION_FAILED)
    assert error.checks == {"problem": "standalone_not_supported", "requisition": row.number}
    assert awards(db).read(rfq.id).status == "draft"
    assert sql(db, "SELECT count(*) FROM approval_instance WHERE document_type='award'") == [(0,)]


# -- tax and freight ----------------------------------------------------------------------


def test_a_partial_award_prorates_tax_and_freight_and_the_settings_shape_the_budget_amount(
    lifecycle_db,
):
    db, _ = lifecycle_db
    # one line, 10 units at 80 = 800; tax 40 and freight 20 on the bid
    rfq, row, _, [a, _b] = scene(db, prices=("80", "99"), tax="40", freight="20")
    award = prepare(db, rfq, pick(db, rfq, a, "5"))  # half: 400 + 20 tax + 10 freight
    [line] = award.lines
    assert line.amount_vendor == "430.00" and line.amount_rfq == "430.00"
    assert line.budget_amount == "430.00"
    org = OrgService(db[0], Scope(db[1]), db[3])
    org.set_setting("tax.consumes_budget", SettingPut(value=False))
    assert awards(db).read(rfq.id).lines[0].budget_amount == "410.00"
    org.set_setting("freight.consumes_budget", SettingPut(value=False))
    again = awards(db).read(rfq.id)
    assert again.lines[0].budget_amount == "400.00" and again.lines[0].amount_rfq == "430.00"
    assert again.checks[0].award == "400.00"  # the guard uses the budget amount


# -- routes ------------------------------------------------------------------------------


def test_the_award_routes_run_through_the_real_stack_with_etags_and_roles(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db)
    line = str(rfq_line_of(db, rfq))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            base = f"/rfqs/{rfq.id}/award"
            created = await send(browser, "POST", base, {"basis": "lowest_cost_supplier"})
            assert created.status_code == 201, created.text
            assert created.json()["lines"][0]["vendor_id"] == str(a)  # the lowest, copied
            etag = created.headers["ETag"]
            missing = await send(browser, "PUT", base + "/lines", {"lines": []})
            assert missing.status_code == 400, missing.text
            body = {"lines": [{"rfq_line_id": line, "vendor_id": str(a), "quantity": "10"}]}
            put = await send(browser, "PUT", base + "/lines", body, {"If-Match": etag})
            assert put.status_code == 200, put.text
            stale = await send(browser, "PATCH", base, {"rationale": "x"}, {"If-Match": etag})
            assert stale.status_code == 409, stale.text
            patch = await send(
                browser, "PATCH", base, {"rationale": "Lowest"}, {"If-Match": put.headers["ETag"]}
            )
            assert patch.status_code == 200 and patch.json()["rationale"] == "Lowest"
            assert (await send(browser, "GET", base)).json()["checks"][0]["award"] == "800.00"
            regrant(db, ["rfq.view", "rfq.edit"])  # no rfq.approve: a hidden Submit is refused
            assert (await send(browser, "POST", base + "/submit")).status_code == 403
            regrant(db, ["rfq.view"])
            assert (await send(browser, "GET", base)).status_code == 200
            assert (await send(browser, "POST", base, {"basis": "manual"})).status_code == 403
            assert (await send(browser, "POST", base + "/withdraw")).status_code == 403
            regrant(db, [])
            assert (await send(browser, "GET", base)).status_code == 403
            regrant(db, ["rfq.view", "rfq.approve"])
            submitted = await send(browser, "POST", base + "/submit")
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["status"] == "approval_pending"

    asyncio.run(scenario())


def test_award_routes_conceal_foreign_tenants(lifecycle_db):
    db, _ = lifecycle_db
    foreign_unit = unit(db, "FBU", org=db[2])
    foreign_rfq = uuid4()
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign_rfq, db[2], foreign_unit.id, db[3]),
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for id in (foreign_rfq, uuid4()):
                base = f"/rfqs/{id}/award"
                for method, suffix, data in [
                    ("POST", "", {"basis": "manual"}),
                    ("GET", "", None),
                    ("PUT", "/lines", {"lines": []}),
                    ("PATCH", "", {"rationale": "x"}),
                    ("POST", "/submit", None),
                    ("POST", "/withdraw", None),
                    ("POST", "/cancel", {"reason": "x"}),
                    ("POST", "/supersede", {"reason": "x"}),
                ]:
                    response = await send(browser, method, base + suffix, data, {"If-Match": '"1"'})
                    assert response.status_code == 404, (method, suffix, response.text)
                listed = await send(browser, "GET", f"/rfqs/{id}/awards")
                assert listed.status_code == 404, listed.text

    asyncio.run(scenario())


def test_a_duplicate_line_and_vendor_is_a_422_not_a_500(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db)
    award = prepare(db, rfq, pick(db, rfq, a, "2"))
    line = rfq_line_of(db, rfq)
    twice = AwardLinesPut(lines=[pick(db, rfq, a, "2"), pick(db, rfq, a, "3")])
    error = refused(
        lambda: awards(db).put_lines(rfq.id, twice, str(award.version)),
        ErrorCode.VALIDATION_FAILED,
    )
    assert [e.field for e in error.errors] == [f"lines/{line}/{a}"]

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            body = {"lines": [{"rfq_line_id": str(line), "vendor_id": str(a), "quantity": "2"}] * 2}
            response = await send(
                browser,
                "PUT",
                f"/rfqs/{rfq.id}/award/lines",
                body,
                {"If-Match": f'"{award.version}"'},
            )
            assert response.status_code == 422, response.text

    asyncio.run(scenario())
    assert awards(db).read(rfq.id).lines[0].quantity == "2.0000"  # nothing was replaced


def test_the_award_is_a_link_node_of_its_requisition_and_hidden_without_rfq_view(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db)
    award = prepare(db, rfq, pick(db, rfq, a, "10"))
    scope = Scope(db[1])
    nodes = links(db[0], scope, row.id, lambda *_: True).nodes
    [node] = [n for n in nodes if n.kind == "award"]
    assert (
        node.key == f"award:{award.id}" and node.state == "Draft" and node.relation == "awarded_by"
    )
    assert node.href == f"/sourcing/rfqs/{rfq.id}"
    hidden = links(db[0], scope, row.id, lambda permission, *_: permission != "rfq.view").nodes
    assert not [n for n in hidden if n.kind == "award"]


def test_the_funds_check_waits_for_the_balance_lock_and_reads_the_reduced_reservation(
    lifecycle_db,
):
    """Lock before validate: a reservation reduced under the balance lock is seen, not raced."""
    from threading import Thread
    from time import sleep

    from flo.modules.requisitions.funding import reduce_to

    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, prices=("90", "99"))
    prepare(db, rfq, pick(db, rfq, a, "10"))  # 900 against a scope of 1,000
    award = submit(db, rfq)
    instance = sql(db, "SELECT id FROM approval_instance WHERE document_id=%s", (award.id,))
    task = get_instance(db[0], Scope(db[1]), instance[0][0]).tasks[-1].id
    version = sql(db, "SELECT version FROM award WHERE id=%s", (award.id,))[0][0]
    actor, outcome = reviewer(db), []

    def approve_now():
        with psycopg.connect(connection_url(db[0]), autocommit=True) as conn:
            try:
                decide(
                    conn,
                    Scope(db[1]),
                    task_id=task,
                    actor_id=actor,
                    body=DecisionWrite(outcome="approve", document_version=version),
                )
                outcome.append("success")
            except ProblemError as exc:
                outcome.append(exc.checks)

    with psycopg.connect(connection_url(db[0])) as holder:
        with tenant_transaction(holder, Scope(db[1])):
            reduce_to(  # takes the balance lock and keeps it until this block commits
                holder, Scope(db[1]), row.id, new_amount=Decimal(700), version=99, actor_id=db[3]
            )
            worker = Thread(target=approve_now)
            worker.start()
            sleep(2)  # the approval is now waiting on the balance lock
            assert outcome == []
        holder.commit()
    worker.join(timeout=30)
    assert outcome[0]["problem"] == "over_scope" and outcome[0]["over_funds_by"] == "200.00"
    assert awards(db).read(rfq.id).status == "approval_pending"
