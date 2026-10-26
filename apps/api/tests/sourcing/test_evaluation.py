"""Evaluation: criteria and weights, conflict declaration first, sealed scores (SRC-011)."""

import asyncio
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from flo.modules.sourcing.evaluation import (
    DEFAULT_KEY,
    CriteriaPut,
    CriterionIn,
    DeclarationIn,
    EvaluationService,
    ScoreIn,
    ScoresPut,
)
from flo.modules.sourcing.schemas import BidSubmit, SealedBidRead
from tests.isolation.test_real_sessions import client, login
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_header import unit
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_bids import bids, complete, key, live, second_user
from tests.sourcing.test_comparison import cmp, deadline_ago, quoted
from tests.sourcing.test_rfq import refused, send, sql
from tests.sourcing.test_rfq_lifecycle import life, ready
from tests.vendors.test_vendors import regrant

DECLARE_FIRST = "declaration_required"


def ev(db, actor=None):
    return EvaluationService(db[0], Scope(db[1]), actor or db[3], Settings())


def scene(db, names=("V",), criteria=(("Quality", 60), ("Price", 40)), *, bids_in=True, past=True):
    """A published RFQ with a current bid per vendor, criteria, and the deadline behind us."""
    rfq, _, _, vids = live(db, names=names)
    if bids_in:
        for vid in vids:
            quoted(db, rfq, vid, "USD")
    for n, (name, weight) in enumerate(criteria, 1):
        sql(
            db,
            "INSERT INTO rfq_criterion(id,org_id,rfq_id,position,name,weight) "
            "VALUES(%s,%s,%s,%s,%s,%s) RETURNING 1",
            (uuid4(), db[1], rfq.id, n, name, weight),
        )
    if past:
        deadline_ago(db, rfq, 1)
    return rfq, vids


def criteria_ids(db, rfq):
    query = "SELECT id FROM rfq_criterion WHERE rfq_id=%s ORDER BY position"
    return [r[0] for r in sql(db, query, (rfq.id,))]


def evaluate(db, rfq, *actors, declare=True):
    for who in actors:
        with correlation_context("evaluator"):
            ev(db).add_evaluator(rfq.id, who, may_view=True)
            if declare:
                ev(db, who).declare(rfq.id, DeclarationIn(conflict=False))


def version(db, rfq, who):
    query = "SELECT version FROM rfq_evaluator WHERE rfq_id=%s AND identity_id=%s"
    return sql(db, query, (rfq.id, who))[0][0]


def put(db, rfq, vids, who, marks=(7, 3), comment=None):
    crit = criteria_ids(db, rfq)
    cells = [
        ScoreIn(vendor_id=v, criterion_id=c, score=m, comment=comment)
        for v in vids
        for c, m in zip(crit, marks, strict=False)
    ]
    with correlation_context("score"):
        return ev(db, who).save_scores(rfq.id, ScoresPut(cells=cells), str(version(db, rfq, who)))


def finish(db, rfq, vids, who, marks=(7, 3)):
    put(db, rfq, vids, who, marks)
    with correlation_context("score"):
        return ev(db, who).submit_scores(rfq.id)


def view(db, rfq, who=None, **flags):
    return ev(db, who).view(
        rfq.id, can_edit=flags.get("can_edit", False), can_approve=flags.get("can_approve", False)
    )


def problem(call, code=ErrorCode.CONFLICT):
    return refused(call, code).checks["problem"]


# -- criteria and the organisation default ----------------------------------------------------


def test_default_criteria_prefill_a_draft_without_writing_and_never_change_saved_rows(
    lifecycle_db,
):
    db, _ = lifecycle_db
    org = OrgService(db[0], Scope(db[1]), db[3])
    default = [{"name": "Quality", "weight": 70}, {"name": "Price", "weight": 30}]
    org.set_setting(DEFAULT_KEY, SettingPut(value=default))
    rfq, _, _ = ready(db)
    first = view(db, rfq)
    assert first.source == "default" and [(c.name, c.weight) for c in first.criteria] == [
        ("Quality", 70),
        ("Price", 30),
    ]
    assert sql(db, "SELECT count(*) FROM rfq_criterion")[0][0] == 0  # nothing written
    mine = CriteriaPut(criteria=[CriterionIn(name="Safety", weight=100)])
    revision = str(life(db).get(rfq.id).edit_revision)
    with correlation_context("criteria"):
        saved = ev(db).put_criteria(rfq.id, mine, revision)
    assert saved.source == "rfq" and view(db, rfq).source == "rfq"
    org.set_setting(DEFAULT_KEY, SettingPut(value=[{"name": "Other", "weight": 100}]))
    assert [c.name for c in view(db, rfq).criteria] == ["Safety"]
    stale = ev(db).put_criteria
    assert problem(lambda: stale(rfq.id, mine, revision)) == "stale_version"


@pytest.mark.parametrize(
    "value",
    [
        [{"name": "A", "weight": 50}, {"name": "B", "weight": 40}],  # totals 90
        [{"name": "A", "weight": 50}, {"name": "A", "weight": 50}],  # duplicate name
        [{"name": "A", "weight": 100, "extra": 1}],
        [{"name": "A", "weight": 0}, {"name": "B", "weight": 100}],
        [],
        "Quality",
    ],
)
def test_a_default_that_is_malformed_or_does_not_total_100_is_refused(lifecycle_db, value):
    db, _ = lifecycle_db
    org = OrgService(db[0], Scope(db[1]), db[3])
    refused(
        lambda: org.set_setting(DEFAULT_KEY, SettingPut(value=value)), ErrorCode.VALIDATION_FAILED
    )


def test_the_default_is_organization_wide_only(lifecycle_db):
    db, _ = lifecycle_db
    org = OrgService(db[0], Scope(db[1]), db[3])
    body = SettingPut(unit_id=unit(db, "BU").id, value=[{"name": "A", "weight": 100}])
    error = refused(lambda: org.set_setting(DEFAULT_KEY, body), ErrorCode.VALIDATION_FAILED)
    assert error.checks["problem"] == "org_only_setting"


def test_publish_needs_weights_totalling_100_and_criteria_are_a_draft_matter(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _ = ready(db)
    short = CriteriaPut(
        criteria=[CriterionIn(name="Q", weight=60), CriterionIn(name="P", weight=30)]
    )
    ev(db).put_criteria(rfq.id, short, str(life(db).get(rfq.id).edit_revision))
    error = refused(lambda: life(db).publish(rfq.id), ErrorCode.VALIDATION_FAILED)
    assert error.checks["problem"] == "weights_total"
    fixed = CriteriaPut(
        criteria=[CriterionIn(name="Q", weight=60), CriterionIn(name="P", weight=40)]
    )
    ev(db).put_criteria(rfq.id, fixed, str(life(db).get(rfq.id).edit_revision))
    with correlation_context("publish"):
        life(db).publish(rfq.id)
    late = ev(db).put_criteria
    revision = str(life(db).get(rfq.id).edit_revision)
    assert problem(lambda: late(rfq.id, fixed, revision)) == "not_draft"


# -- declaration first ------------------------------------------------------------------------


def test_nobody_scores_or_reads_bids_before_declaring_and_a_conflict_recuses(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    evaluate(db, rfq, db[3], declare=False)
    crit = criteria_ids(db, rfq)
    one = ScoresPut(cells=[ScoreIn(vendor_id=vids[0], criterion_id=crit[0], score=5)])
    assert problem(lambda: ev(db).save_scores(rfq.id, one, "1")) == DECLARE_FIRST  # plant 1
    assert problem(lambda: ev(db).submit_scores(rfq.id)) == DECLARE_FIRST
    seen = view(db, rfq)
    assert seen.me.declaration == "none" and seen.vendors == [] and seen.scores == []
    with correlation_context("declare"):
        ev(db).declare(rfq.id, DeclarationIn(conflict=True, detail="Own shares in V"))
    assert problem(lambda: ev(db).save_scores(rfq.id, one, "1")) == "recused"
    assert problem(lambda: view(db, rfq)) == "recused"
    owner = view(db, rfq, second_user(db), can_edit=True)
    [row] = owner.evaluators
    assert row.declaration == "conflict" and row.detail == "Own shares in V"


def test_a_conflict_needs_a_detail_and_only_a_non_evaluator_is_refused(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    evaluate(db, rfq, db[3], declare=False)
    refused(
        lambda: ev(db).declare(rfq.id, DeclarationIn(conflict=True)), ErrorCode.VALIDATION_FAILED
    )
    refused(
        lambda: ev(db, second_user(db)).declare(rfq.id, DeclarationIn(conflict=False)),
        ErrorCode.FORBIDDEN,
    )
    one = ScoresPut(
        cells=[ScoreIn(vendor_id=vids[0], criterion_id=criteria_ids(db, rfq)[0], score=1)]
    )
    refused(lambda: ev(db, second_user(db)).save_scores(rfq.id, one, "1"), ErrorCode.FORBIDDEN)


def test_repeat_declarations_go_one_way_only(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    a, b = db[3], second_user(db)
    evaluate(db, rfq, a, b)
    assert problem(lambda: ev(db, b).declare(rfq.id, DeclarationIn(conflict=False))) == (
        "already_declared"
    )
    finish(db, rfq, vids, a)
    ev(db, a).declare(rfq.id, DeclarationIn(conflict=True, detail="Found later"))  # after submit
    for again in (
        DeclarationIn(conflict=True, detail="x"),
        DeclarationIn(conflict=False),
    ):
        assert problem(lambda: ev(db, a).declare(rfq.id, again)) == "already_declared"
    assert sql(db, "SELECT count(*) FROM rfq_evaluator_declaration WHERE identity_id=%s", (a,)) == [
        (2,)
    ]
    with pytest.raises(psycopg.Error), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE rfq_evaluator_declaration SET conflict = false")


def test_a_recused_evaluators_scores_leave_weighted_and_completion_but_stay_as_rows(
    lifecycle_db,
):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    a, b = db[3], second_user(db)
    evaluate(db, rfq, a, b)
    finish(db, rfq, vids, a, (10, 10))
    finish(db, rfq, vids, b, (4, 6))
    both = view(db, rfq, b).weighted
    assert [w.score for w in both] == ["7.40"]  # mean 7 x 0.6 + mean 8 x 0.4
    ev(db, a).declare(rfq.id, DeclarationIn(conflict=True, detail="Related"))
    assert sql(db, "SELECT count(*) FROM rfq_score WHERE identity_id=%s", (a,))[0][0] == 2
    seen = view(db, rfq, b)  # plant 8: a's 10s must not count
    assert seen.complete and [w.score for w in seen.weighted] == ["4.80"]
    assert {s.identity_id for s in seen.scores} == {b}


def test_with_every_evaluator_recused_scoring_never_completes(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    evaluate(db, rfq, db[3])
    finish(db, rfq, vids, db[3])
    assert view(db, rfq, second_user(db), can_approve=True).complete is True
    ev(db).declare(rfq.id, DeclarationIn(conflict=True, detail="Late conflict"))
    after = view(db, rfq, second_user(db), can_approve=True)
    assert after.complete is False and after.evaluator_count == 0 and after.weighted is None
    assert after.scores == []


# -- sealing and visibility -------------------------------------------------------------------


def test_before_the_deadline_scoring_is_closed_and_nothing_comes_back(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db, past=False)
    evaluate(db, rfq, db[3])
    assert problem(lambda: put(db, rfq, vids, db[3])) == "scoring_not_open"
    sealed = view(db, rfq, can_approve=True)
    assert sealed.vendors == [] and sealed.scores == [] and sealed.weighted is None
    assert sealed.scoring_open is False


def test_an_evaluator_sees_only_their_own_scores_until_everyone_has_submitted(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    a, b, outsider = db[3], second_user(db), db[3]
    evaluate(db, rfq, a, b)
    finish(db, rfq, vids, a)
    assert {s.identity_id for s in view(db, rfq, a).scores} == {
        a
    }  # plant 2: never b's, never early
    assert view(db, rfq, b).scores == []
    assert view(db, rfq, uuid4(), can_approve=True).scores == []  # an approver waits too
    assert view(db, rfq, outsider).weighted is None
    finish(db, rfq, vids, b, (8, 3))
    for who in (a, b):
        assert {s.identity_id for s in view(db, rfq, who).scores} == {a, b}
    assert {s.identity_id for s in view(db, rfq, uuid4(), can_approve=True).scores} == {a, b}
    assert view(db, rfq, uuid4()).scores == []  # a plain viewer never sees them


def test_weighted_score_is_a_hand_computed_decimal_shown_in_the_comparison(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db, criteria=(("Quality", 33), ("Price", 67)))
    a, b = db[3], second_user(db)
    evaluate(db, rfq, a, b)
    finish(db, rfq, vids, a, (7, 3))
    assert cmp(db).comparison(rfq.id).vendors[0].score is None
    finish(db, rfq, vids, b, (8, 3))
    # Q mean 7.5 x 0.33 = 2.475, P mean 3 x 0.67 = 2.01, 4.485 rounds half up
    read = cmp(db).comparison(rfq.id)
    assert read.vendors[0].score == "4.49"
    assert read.markers.highest_score.vendor_id == vids[0]


def test_comments_are_kept_and_a_submitted_score_is_immutable(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    evaluate(db, rfq, db[3])
    put(db, rfq, vids, db[3], comment="Strong references")
    with correlation_context("score"):
        ev(db).submit_scores(rfq.id)
    assert {s.comment for s in view(db, rfq).scores} == {"Strong references"}
    assert problem(lambda: put(db, rfq, vids, db[3])) == "score_submitted"
    with pytest.raises(psycopg.Error), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE rfq_score SET score = 0")
    with pytest.raises(psycopg.Error), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("DELETE FROM rfq_score")


def test_submit_is_all_or_nothing(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    evaluate(db, rfq, db[3])
    crit = criteria_ids(db, rfq)
    ev(db).save_scores(
        rfq.id,
        ScoresPut(cells=[ScoreIn(vendor_id=vids[0], criterion_id=crit[0], score=5)]),
        str(version(db, rfq, db[3])),
    )
    refused(lambda: ev(db).submit_scores(rfq.id), ErrorCode.VALIDATION_FAILED)
    assert sql(db, "SELECT count(*) FROM rfq_score WHERE status='submitted'")[0][0] == 0
    bad = ScoresPut(cells=[ScoreIn(vendor_id=uuid4(), criterion_id=crit[0], score=5)])
    refused(lambda: ev(db).save_scores(rfq.id, bad, "2"), ErrorCode.VALIDATION_FAILED)
    stale = ScoresPut(cells=[ScoreIn(vendor_id=vids[0], criterion_id=crit[0], score=6)])
    assert problem(lambda: ev(db).save_scores(rfq.id, stale, "1")) == "stale_version"


def test_a_late_bid_reopens_only_the_new_vendors_cells(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db, names=("A", "B"), bids_in=False, past=False)
    quoted(db, rfq, vids[0], "USD")
    late = key(db, rfq, vids[1], "USD")
    late = complete(db, rfq, late)
    deadline_ago(db, rfq, 1)
    a, b = db[3], second_user(db)
    evaluate(db, rfq, a, b)
    for who in (a, b):
        finish(db, rfq, vids[:1], who)
    assert view(db, rfq, a).complete is True
    with correlation_context("late"):
        bids(db).submit(
            rfq.id, late.id, BidSubmit(accept_late_reason="Courier"), str(late.row_version), True
        )
    reopened = view(db, rfq, a)
    assert reopened.complete is False and reopened.weighted is None  # plant 6
    assert {s.identity_id for s in reopened.scores} == {a}  # sealed again
    submitted_before = sql(db, "SELECT count(*) FROM rfq_score WHERE status='submitted'")[0][0]
    assert submitted_before == 4
    for who in (a, b):
        finish(db, rfq, vids[1:], who)  # only the new vendor's cells are saved and submitted
    refused(lambda: put(db, rfq, vids[:1], a), ErrorCode.CONFLICT)  # submitted cells stay put
    done = view(db, rfq, a)
    assert done.complete and {w.vendor_id for w in done.weighted} == set(vids)
    assert sql(db, "SELECT count(*) FROM rfq_score WHERE status='submitted'")[0][0] == 8


@pytest.mark.parametrize(("criteria", "bids_in"), [((), True), ((("Q", 100),), False)])
def test_an_empty_set_cannot_be_scored_or_complete(lifecycle_db, criteria, bids_in):
    db, _ = lifecycle_db
    rfq, vids = scene(db, criteria=criteria, bids_in=bids_in)
    evaluate(db, rfq, db[3])
    crit = criteria_ids(db, rfq)
    cell = [ScoreIn(vendor_id=uuid4(), criterion_id=crit[0] if crit else uuid4(), score=5)]
    assert problem(lambda: ev(db).save_scores(rfq.id, ScoresPut(cells=cell), "1")) == (
        "nothing_to_score"
    )
    assert problem(lambda: ev(db).submit_scores(rfq.id)) == "nothing_to_score"
    assert sql(db, "SELECT count(*) FROM rfq_score")[0][0] == 0
    assert view(db, rfq).complete is False


@pytest.mark.parametrize("status", ["awarded", "cancelled"])
def test_a_terminal_rfq_refuses_scores_and_evaluator_changes_but_reads(lifecycle_db, status):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    evaluate(db, rfq, db[3])
    put(db, rfq, vids, db[3])
    sql(db, "UPDATE rfq SET status=%s WHERE id=%s RETURNING 1", (status, rfq.id))
    assert problem(lambda: put(db, rfq, vids, db[3])) == "rfq_terminal"  # plant 7
    assert problem(lambda: ev(db).submit_scores(rfq.id)) == "rfq_terminal"
    other = second_user(db)
    assert problem(lambda: ev(db).add_evaluator(rfq.id, other, may_view=True)) == "rfq_terminal"
    remove = ev(db).remove_evaluator
    assert problem(lambda: remove(rfq.id, db[3], "1")) == "rfq_terminal"
    assert len(view(db, rfq).scores) == 2 and view(db, rfq).status == status


# -- evaluators -------------------------------------------------------------------------------


def test_an_evaluator_must_be_able_to_view_the_rfq_and_is_added_once(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _ = scene(db)
    refused(
        lambda: ev(db).add_evaluator(rfq.id, uuid4(), may_view=False), ErrorCode.VALIDATION_FAILED
    )
    evaluate(db, rfq, db[3], declare=False)
    assert (
        problem(lambda: ev(db).add_evaluator(rfq.id, db[3], may_view=True)) == "already_evaluator"
    )
    assert len(sql(db, "SELECT 1 FROM audit_log WHERE action='rfq.evaluator.add'")) == 1


def test_removal_is_refused_after_a_submitted_score_and_drops_drafts_otherwise(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    a, b = db[3], second_user(db)
    evaluate(db, rfq, a, b)
    finish(db, rfq, vids, b)
    put(db, rfq, vids, a)
    assert view(db, rfq, b).complete is False  # waiting on a's draft
    remove = ev(db).remove_evaluator
    assert (
        problem(lambda: remove(rfq.id, b, str(version(db, rfq, b)))) == "evaluator_has_scores"
    )  # plant 5
    assert sql(db, "SELECT count(*) FROM rfq_score WHERE identity_id=%s", (b,))[0][0] == 2
    with correlation_context("remove"):
        remove(rfq.id, a, str(version(db, rfq, a)))
    assert sql(db, "SELECT count(*) FROM rfq_score WHERE identity_id=%s", (a,))[0][0] == 0
    [row] = sql(db, "SELECT after FROM audit_log WHERE action='rfq.evaluator.remove'")
    assert row[0]["drafts_dropped"] == 2
    assert view(db, rfq, b).complete is True


# -- the bid-content gate on the four reads -----------------------------------------------------


def test_bid_content_stays_behind_the_declaration_for_an_evaluator_only(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    bid_id = sql(db, "SELECT id FROM bid WHERE rfq_id=%s", (rfq.id,))[0][0]
    evaluate(db, rfq, db[3], declare=False)

    async def read(browser, method, suffix):
        return (await send(browser, method, suffix)).status_code

    async def reads(browser, grant, expect):
        regrant(db, grant)
        got = {
            "comparison": await read(browser, "GET", f"/rfqs/{rfq.id}/comparison"),
            "refresh": await read(browser, "POST", f"/rfqs/{rfq.id}/comparison/refresh-rate"),
            "list": await read(browser, "GET", f"/rfqs/{rfq.id}/bids"),
            "bid": await read(browser, "GET", f"/rfqs/{rfq.id}/bids/{bid_id}"),
        }
        assert got == expect, (grant, got)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            gated = {"comparison": 409, "refresh": 409, "list": 409, "bid": 409}
            await reads(browser, ["rfq.view", "rfq.edit"], gated)
            await reads(browser, ["rfq.view", "rfq.approve"], {**gated, "refresh": 403})
            # view only: the gate removes access, never widens it
            await reads(browser, ["rfq.view"], {**gated, "refresh": 403, "list": 403})
            with correlation_context("declare"):
                ev(db).declare(rfq.id, DeclarationIn(conflict=True, detail="Related"))
            await reads(browser, ["rfq.view", "rfq.edit"], gated)  # recused, permanently
            sql(db, "DELETE FROM rfq_score WHERE rfq_id=%s RETURNING 1", (rfq.id,))
            sql(db, "DELETE FROM rfq_evaluator WHERE rfq_id=%s RETURNING 1", (rfq.id,))
            open_ = {"comparison": 200, "refresh": 200, "list": 200, "bid": 200}
            await reads(browser, ["rfq.view", "rfq.edit"], open_)  # not an evaluator: as today
            await reads(browser, ["rfq.view"], {**open_, "refresh": 403, "list": 403})

    asyncio.run(scenario())


def test_the_gate_is_a_no_op_for_the_sealed_responses(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _ = scene(db, past=False)
    evaluate(db, rfq, db[3], declare=False)
    assert cmp(db).comparison(rfq.id).sealed is True
    assert isinstance(bids(db).list_bids(rfq.id, all_bids=True)[0], SealedBidRead)


# -- routes -----------------------------------------------------------------------------------


def test_the_evaluation_routes_end_to_end_with_the_concurrency_headers(lifecycle_db):
    db, _ = lifecycle_db
    rfq, vids = scene(db)
    crit = criteria_ids(db, rfq)
    base = f"/rfqs/{rfq.id}/evaluation"
    cells = [{"vendor_id": str(v), "criterion_id": str(c), "score": 6} for v in vids for c in crit]

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            added = await send(browser, "POST", f"{base}/evaluators", {"identity_id": str(db[3])})
            assert added.status_code == 201, added.text
            declared = await send(browser, "POST", f"{base}/declaration", {"conflict": False})
            assert declared.status_code == 201, declared.text
            first = await send(browser, "GET", base)
            assert first.status_code == 200 and first.headers["etag"] == '"1"', first.text
            missing = await send(browser, "PUT", f"{base}/scores", {"cells": cells})
            assert missing.status_code == 400
            saved = await send(
                browser, "PUT", f"{base}/scores", {"cells": cells}, {"If-Match": '"1"'}
            )
            assert saved.status_code == 200 and saved.headers["etag"] == '"2"', saved.text
            stale = await send(
                browser, "PUT", f"{base}/scores", {"cells": cells}, {"If-Match": '"1"'}
            )
            assert stale.status_code == 409
            done = await send(browser, "POST", f"{base}/scores/submit")
            assert done.status_code == 200 and done.json()["submitted"] is True, done.text
            read = await send(browser, "GET", base)
            body = read.json()
            assert body["complete"] is True and body["weighted"][0]["score"] == "6.00"
            assert len(body["scores"]) == 2 and body["evaluators"][0]["submitted"] is True
            gone = await send(
                browser, "DELETE", f"{base}/evaluators/{db[3]}", None, {"If-Match": '"3"'}
            )
            assert gone.status_code == 409  # a submitted score

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("grant", "read", "edit", "score", "add"),
    [
        (["rfq.view"], 200, 403, 403, 403),  # a viewer reads, never edits and never scores
        (["rfq.view", "rfq.edit"], 200, 409, 403, 201),  # an editor is not an evaluator
        (["rfq.edit"], 403, 409, 403, 422),  # cannot view: not an eligible evaluator
    ],
)
def test_authorization_per_role_over_the_evaluation_routes(
    lifecycle_db, grant, read, edit, score, add
):
    db, _ = lifecycle_db
    rfq, _ = scene(db)
    regrant(db, grant)
    base = f"/rfqs/{rfq.id}/evaluation"
    cell = {"vendor_id": str(uuid4()), "criterion_id": str(uuid4()), "score": 1}
    criteria = {"criteria": [{"name": "Q", "weight": 100}]}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            got = [
                (await send(browser, "GET", base)).status_code,
                (
                    await send(browser, "PUT", f"{base}/criteria", criteria, {"If-Match": '"1"'})
                ).status_code,
                (
                    await send(
                        browser, "PUT", f"{base}/scores", {"cells": [cell]}, {"If-Match": '"1"'}
                    )
                ).status_code,
                (
                    await send(browser, "POST", f"{base}/evaluators", {"identity_id": str(db[3])})
                ).status_code,
            ]
            return got

    assert asyncio.run(scenario()) == [read, edit, score, add]  # edit: 409, the RFQ is published


def test_evaluation_routes_conceal_foreign_tenants(lifecycle_db):
    db, _ = lifecycle_db
    foreign_unit = unit(db, "FBU", org=db[2])
    foreign = uuid4()
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign, db[2], foreign_unit.id, second_user(db)),
        )
    cell = {"vendor_id": str(uuid4()), "criterion_id": str(uuid4()), "score": 1}
    calls = [
        ("GET", "", None),
        ("PUT", "/criteria", {"criteria": [{"name": "Q", "weight": 100}]}),
        ("POST", "/evaluators", {"identity_id": str(db[3])}),
        ("DELETE", f"/evaluators/{db[3]}", None),
        ("POST", "/declaration", {"conflict": False}),
        ("PUT", "/scores", {"cells": [cell]}),
        ("POST", "/scores/submit", None),
    ]

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for rfq_id in (foreign, uuid4()):
                for method, suffix, data in calls:
                    response = await send(
                        browser,
                        method,
                        f"/rfqs/{rfq_id}/evaluation{suffix}",
                        data,
                        {"If-Match": '"1"'},
                    )
                    assert response.status_code == 404, (method, suffix, response.text)

    asyncio.run(scenario())
