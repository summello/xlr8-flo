"""Vendor performance: append-only scores, disputes, adjustments, averages (E11-S07)."""

import asyncio
from datetime import date
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.org.schemas import MasterPatch
from flo.modules.vendors.schemas import (
    DisputeCreate,
    DisputeResolve,
    ScoreCreate,
    VendorCreate,
)
from flo.modules.vendors.service import VendorService
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import admin_id
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_header import org_svc
from tests.vendors.test_vendors import body, regrant, send

AUG, SEP = date(2026, 8, 1), date(2026, 9, 1)


def service(db, org=None, actor=None):
    return VendorService(db[0], Scope(org or db[1]), actor or db[3])


def other_actor(db):
    return admin_id(db[0], "second@example.test")


def vendor(db, org=None):
    return (
        service(db, org)
        .create(VendorCreate(trading_name="V", legal_name="V", availability="global"))
        .id
    )


def score(svc, vid, category, value, period, ref, rationale="Because"):
    return svc.add_score(
        vid,
        ScoreCreate(
            category=category,
            score=value,
            period=period,
            source_ref=ref,
            rationale=rationale,
        ),
    )


def problem(excinfo):
    return excinfo.value.checks["problem"]


def worked_example(db):
    vid = vendor(db)
    svc = service(db)
    score(svc, vid, "quality", 5, AUG, "PO-A")
    score(svc, vid, "quality", 4, SEP, "PO-B")
    third = score(svc, vid, "pricing", 2, SEP, "PO-C")
    score(svc, vid, "pricing", 3, AUG, "PO-A")
    score(svc, vid, "pricing", 2, SEP, "PO-B")
    score(svc, vid, "delivery", 4, AUG, "PO-A")
    return vid, svc, third


def averages(perf):
    return (
        {c.category_code: (c.average, c.count) for c in perf.categories},
        perf.overall,
        perf.count,
    )


def test_averages_are_hand_computed_decimal_strings(header_db):
    vid, svc, _ = worked_example(header_db)
    assert averages(svc.performance(vid, None, None)) == (
        {"DELIVERY": ("4.0", 1), "PRICING": ("2.3", 3), "QUALITY": ("4.5", 2)},
        "3.6",
        6,
    )
    # A month window keeps only that period; a category with no rows in it is absent.
    assert averages(svc.performance(vid, SEP, None)) == (
        {"PRICING": ("2.0", 2), "QUALITY": ("4.0", 1)},
        "3.0",
        3,
    )
    assert averages(svc.performance(vid, None, date(2026, 7, 31))) == ({}, None, 0)


def test_upheld_dispute_adds_adjustment_and_average_uses_it_only(header_db):
    db = header_db
    vid, svc, third = worked_example(db)
    dispute = svc.dispute_score(vid, third.id, DisputeCreate(reason="It was on time"))
    judge = service(db, actor=other_actor(db))
    resolved = judge.resolve_dispute(
        vid,
        third.id,
        dispute.id,
        DisputeResolve(
            resolution="upheld", note="Agreed", adjusted_score=5, adjusted_rationale="Delivered"
        ),
    )
    assert resolved.resolution == "upheld" and resolved.resolved_by_email == "second@example.test"
    # pricing: (3 + 2 + 5) / 3 -> 3.3; the original 2 no longer counts.
    assert averages(svc.performance(vid, None, None)) == (
        {"DELIVERY": ("4.0", 1), "PRICING": ("3.3", 3), "QUALITY": ("4.5", 2)},
        "3.9",
        6,
    )
    rows = {r.id: r for r in svc.list_scores(vid, None, None, None, None).rows}
    assert len(rows) == 7  # the original stays
    adjustment = rows[rows[third.id].superseded_by]
    assert (adjustment.supersedes, adjustment.score, adjustment.period) == (third.id, 5, SEP)
    assert adjustment.source_ref == "PO-C" and adjustment.rationale == "Delivered"
    assert [d.resolution for d in rows[third.id].disputes] == ["upheld"]
    # The superseded original can no longer be disputed; the adjustment can.
    with pytest.raises(ProblemError) as raised:
        svc.dispute_score(vid, third.id, DisputeCreate(reason="again"))
    assert problem(raised) == "score_superseded"
    svc.dispute_score(vid, adjustment.id, DisputeCreate(reason="Still wrong"))
    actions = {
        r[0]
        for r in db[0].execute("SELECT action FROM audit_log WHERE action LIKE 'vendor.score.%'")
    }
    assert actions == {"vendor.score.add", "vendor.score.dispute", "vendor.score.resolve"}


def test_dismissed_dispute_changes_nothing(header_db):
    vid, svc, third = worked_example(header_db)
    before = averages(svc.performance(vid, None, None))
    dispute = svc.dispute_score(vid, third.id, DisputeCreate(reason="Wrong"))
    judge = service(header_db, actor=other_actor(header_db))
    done = judge.resolve_dispute(
        vid, third.id, dispute.id, DisputeResolve(resolution="dismissed", note="No evidence")
    )
    assert done.resolution == "dismissed"
    assert averages(svc.performance(vid, None, None)) == before
    assert len(svc.list_scores(vid, None, None, None, None).rows) == 6
    # Dismissed is final for that dispute, and the score can be disputed again.
    with pytest.raises(ProblemError) as raised:
        judge.resolve_dispute(
            vid, third.id, dispute.id, DisputeResolve(resolution="dismissed", note="Again")
        )
    assert problem(raised) == "already_resolved"
    svc.dispute_score(vid, third.id, DisputeCreate(reason="New evidence"))


def test_scoring_rules_duplicate_category_and_dispute_guards(header_db):
    db = header_db
    vid = vendor(db)
    svc = service(db)
    first = score(svc, vid, "quality", 4, date(2026, 8, 17), "PO-1")
    assert first.period == AUG and first.category_code == "QUALITY"  # normalised to the month
    with pytest.raises(ProblemError) as raised:
        score(svc, vid, "Quality", 3, AUG, "PO-1")
    assert problem(raised) == "already_scored"
    score(svc, vid, "quality", 3, AUG, "PO-2")  # another order, same month
    score(svc, vid, "quality", 3, SEP, "PO-1")  # same order, another month
    with pytest.raises(ProblemError) as raised:
        score(svc, vid, "taste", 3, AUG, "PO-1")
    assert raised.value.errors[0].field == "category"
    # A configured category works once created.
    from datetime import date as d

    from flo.modules.org.schemas import MasterCreate

    org_svc(db).create_master(
        "vendor_score_category",
        MasterCreate(code="safety", name="Safety", effective_from=d(2000, 1, 1)),
    )
    assert score(svc, vid, "safety", 5, AUG, "PO-1").category_code == "SAFETY"

    dispute = svc.dispute_score(vid, first.id, DisputeCreate(reason="Wrong"))
    with pytest.raises(ProblemError) as raised:
        svc.dispute_score(vid, first.id, DisputeCreate(reason="Twice"))
    assert problem(raised) == "dispute_open"
    with pytest.raises(ProblemError) as raised:
        svc.resolve_dispute(
            vid, first.id, dispute.id, DisputeResolve(resolution="dismissed", note="Mine")
        )
    assert problem(raised) == "separation_of_duty"
    judge = service(db, actor=other_actor(db))
    for kwargs, field in [
        ({}, "adjusted_score"),
        ({"adjusted_score": 3}, "adjusted_rationale"),
    ]:
        with pytest.raises(ProblemError) as raised:
            judge.resolve_dispute(
                vid, first.id, dispute.id, DisputeResolve(resolution="upheld", note="Ok", **kwargs)
            )
        assert raised.value.errors[0].field == field
    with pytest.raises(ProblemError) as raised:
        judge.resolve_dispute(
            vid,
            first.id,
            dispute.id,
            DisputeResolve(resolution="dismissed", note="Ok", adjusted_score=2),
        )
    assert raised.value.errors[0].field == "adjusted_score"
    # Nothing above was resolved or adjusted.
    assert len(svc.list_scores(vid, None, None, None, None).rows) == 4
    rows = {r.id: r for r in svc.list_scores(vid, None, None, None, None).rows}
    assert rows[first.id].disputes[0].resolution is None


def test_core_categories_cannot_be_deactivated(header_db):
    db = header_db
    svc = service(db)
    score(svc, vendor(db), "service", 4, AUG, "PO-1")
    org = org_svc(db)
    records = org.list_master("vendor_score_category").rows
    assert {r.code for r in records} == {"QUALITY", "PRICING", "DELIVERY", "SERVICE"}
    for record in records:
        with pytest.raises(ProblemError) as raised:
            org.deactivate_master("vendor_score_category", record.id)
        assert problem(raised) == "core_category"
    assert org.update_master("vendor_score_category", records[0].id, MasterPatch(name="Renamed"))


def test_core_categories_cannot_be_created_unusable_and_seeding_repairs_them(header_db):
    from flo.modules.org.schemas import MasterCreate

    db = header_db
    org = org_svc(db)
    for extra in ({"active": False}, {"effective_to": date(2001, 1, 1)}):
        with pytest.raises(ProblemError) as raised:
            org.create_master(
                "vendor_score_category",
                MasterCreate(code="quality", name="Q", effective_from=date(2000, 1, 1), **extra),
            )
        assert (
            raised.value.code == ErrorCode.VALIDATION_FAILED and problem(raised) == "core_category"
        )
    # An unusable core record that got in anyway (a row written before the guard) is repaired.
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "INSERT INTO master_record (id, org_id, kind, code, name, attributes, "
            "effective_from, effective_to, active) VALUES (%s, %s, 'vendor_score_category', "
            "'QUALITY', 'Q', '{}'::jsonb, DATE '2000-01-01', DATE '2001-01-01', false)",
            (uuid4(), db[1]),
        )
    assert score(service(db), vendor(db), "quality", 4, AUG, "PO-1").category_code == "QUALITY"
    quality = next(r for r in org.list_master("vendor_score_category").rows if r.code == "QUALITY")
    assert quality.active and quality.effective_to is None
    with pytest.raises(ProblemError) as raised:
        org.deactivate_master("vendor_score_category", quality.id)
    assert raised.value.code == ErrorCode.VALIDATION_FAILED and problem(raised) == "core_category"


def test_pagination_and_filters(header_db):
    vid = vendor(header_db)
    svc = service(header_db)
    for month in range(1, 6):
        score(svc, vid, "pricing", 3, date(2026, month, 1), "PO-1")
    score(svc, vid, "quality", 3, date(2026, 5, 1), "PO-1")
    first = svc.list_scores(vid, None, None, None, None, limit=4)
    second = svc.list_scores(vid, None, None, None, first.next_cursor, limit=4)
    seen = [r.id for r in first.rows + second.rows]
    assert len(seen) == len(set(seen)) == 6 and second.next_cursor is None
    assert len(svc.list_scores(vid, "pricing", None, None, None).rows) == 5
    assert len(svc.list_scores(vid, None, date(2026, 4, 1), date(2026, 5, 1), None).rows) == 3
    with pytest.raises(ProblemError):
        svc.list_scores(vid, None, None, None, "garbage")


def test_raw_update_delete_and_second_resolution_raise(header_db):
    db = header_db
    vid, svc, third = worked_example(db)
    dispute = svc.dispute_score(vid, third.id, DisputeCreate(reason="Wrong"))
    service(db, actor=other_actor(db)).resolve_dispute(
        vid, third.id, dispute.id, DisputeResolve(resolution="dismissed", note="No")
    )
    for statement in (
        "UPDATE vendor_score SET score = 1",
        "DELETE FROM vendor_score",
        "TRUNCATE vendor_score, vendor_score_dispute",
        "UPDATE vendor_score_dispute SET resolution = 'upheld'",  # second resolution
        "UPDATE vendor_score_dispute SET reason = 'edited'",
        "DELETE FROM vendor_score_dispute",
    ):
        with (
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
            tenant_transaction(db[0], Scope(db[1])),
        ):
            db[0].execute(statement)


def test_score_routes_conceal_foreign_tenants(header_db):
    db = header_db
    theirs = vendor(db, db[2])
    their_score = score(service(db, db[2]), theirs, "quality", 4, AUG, "PO-1")
    their_dispute = service(db, db[2]).dispute_score(
        theirs, their_score.id, DisputeCreate(reason="x")
    )
    mine = vendor(db)
    stray = uuid4()
    resolve = {"resolution": "dismissed", "note": "n"}
    fresh = {"category": "quality", "score": 3, "period": "2026-08-01"}
    fresh |= {"source_ref": "PO-9", "rationale": "r"}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, suffix, data in [
                ("GET", f"/{theirs}/scores", None),
                ("POST", f"/{theirs}/scores", fresh),
                ("POST", f"/{theirs}/scores/{their_score.id}/disputes", {"reason": "r"}),
                (
                    "POST",
                    f"/{theirs}/scores/{their_score.id}/disputes/{their_dispute.id}/resolve",
                    resolve,
                ),
                ("GET", f"/{theirs}/performance", None),
                # a foreign score or dispute id under my own vendor
                ("POST", f"/{mine}/scores/{their_score.id}/disputes", {"reason": "r"}),
                (
                    "POST",
                    f"/{mine}/scores/{their_score.id}/disputes/{their_dispute.id}/resolve",
                    resolve,
                ),
                ("POST", f"/{mine}/scores/{stray}/disputes/{stray}/resolve", resolve),
            ]:
                response = await send(browser, method, suffix, data)
                assert response.status_code == 404, (method, suffix, response.text)

    asyncio.run(scenario())


def test_roles_purchasing_scores_sourcing_cannot_and_routes_work(header_db):
    db = header_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            vid = (await send(browser, "POST", data=body())).json()["id"]
            path = f"/{vid}/scores"
            payload = {"category": "quality", "score": 4, "period": "2026-08-15"}
            payload |= {"source_ref": "PO-1", "rationale": "On time"}
            # sourcing specialist: reads vendors, cannot score
            regrant(db, ["vendor.view", "rfq.view"])
            assert (await send(browser, "POST", path, payload)).status_code == 403
            regrant(db, ["purchase_order.view"])
            assert (await send(browser, "POST", path, payload)).status_code == 403
            # purchasing user: purchase_order.view and vendor.view
            regrant(db, ["purchase_order.view", "vendor.view"])
            made = await send(browser, "POST", path, payload)
            assert made.status_code == 201, made.text
            row = made.json()
            assert row["period"] == "2026-08-01" and row["scored_by_email"] == "admin@example.test"
            assert (await send(browser, "POST", path, payload)).json()["checks"]["problem"] == (
                "already_scored"
            )
            blank = await send(browser, "POST", path, payload | {"rationale": " ", "score": 9})
            assert blank.status_code == 422
            fields = {e["field"] for e in blank.json()["errors"]}
            assert {"rationale", "score"} <= fields
            listed = (await send(browser, "GET", path + "?category=quality")).json()
            assert [r["id"] for r in listed["rows"]] == [row["id"]]
            perf = (await send(browser, "GET", f"/{vid}/performance?from=2026-08-01")).json()
            assert perf["overall"] == "4.0" and perf["categories"][0]["average"] == "4.0"
            # dispute needs vendor.edit; resolving needs vendor.approve
            dispute_path = f"{path}/{row['id']}/disputes"
            assert (await send(browser, "POST", dispute_path, {"reason": "r"})).status_code == 403
            regrant(db, ["vendor.edit"])
            raised = await send(browser, "POST", dispute_path, {"reason": "Too low"})
            assert raised.status_code == 201, raised.text
            resolve = f"{dispute_path}/{raised.json()['id']}/resolve"
            decision = {"resolution": "dismissed", "note": "No"}
            assert (await send(browser, "POST", resolve, decision)).status_code == 403
            regrant(db, ["vendor.approve"])
            own = await send(browser, "POST", resolve, decision)
            assert own.status_code == 409 and own.json()["checks"]["problem"] == (
                "separation_of_duty"
            )

    asyncio.run(scenario())


def test_vendor_performance_migration_roundtrip(empty_database):  # noqa: F811
    from flo.kernel import migrate
    from flo.kernel.logging import correlation_context
    from tests.kernel.test_migrate import MIGRATIONS
    from tests.org.test_bootstrap import create

    revisions = migrate.discover_migrations(MIGRATIONS)
    migrate.apply_migrations(
        [r for r in revisions if r.revision <= "20261014_0060"], empty_database
    )
    mig = next(r for r in revisions if r.revision == "20261014_0061")
    with (
        psycopg.connect(empty_database, autocommit=True) as conn,
        correlation_context("performance-migration"),
    ):
        tenant = create(conn)
        actor = admin_id(conn)
        scope = Scope(tenant.org_id)
        vendor_id = uuid4()
        with tenant_transaction(conn, scope):
            conn.execute(
                "INSERT INTO vendor(id,org_id,number,trading_name,legal_name,availability,"
                "created_by) VALUES(%s,%s,'V-000001','T','L','global',%s)",
                (vendor_id, tenant.org_id, actor),
            )
        mig.upgrade(conn)
        original, adjustment = uuid4(), uuid4()
        insert = (
            "INSERT INTO vendor_score(id,org_id,vendor_id,category_code,score,period,source_type,"
            "source_ref,rationale,scored_by,supersedes) "
            "VALUES(%s,%s,%s,'QUALITY',%s,%s,'po','PO-1','r',%s,%s)"
        )
        with tenant_transaction(conn, scope):
            conn.execute(insert, (original, tenant.org_id, vendor_id, 3, AUG, actor, None))
            conn.execute(insert, (adjustment, tenant.org_id, vendor_id, 5, AUG, actor, original))
        for args, error in [
            (
                (uuid4(), tenant.org_id, vendor_id, 6, AUG, actor, None),
                psycopg.errors.CheckViolation,
            ),
            (
                (uuid4(), tenant.org_id, vendor_id, 3, date(2026, 8, 2), actor, None),
                psycopg.errors.CheckViolation,
            ),
            (  # one adjustment per original
                (uuid4(), tenant.org_id, vendor_id, 4, AUG, actor, original),
                psycopg.errors.UniqueViolation,
            ),
        ]:
            with pytest.raises(error), tenant_transaction(conn, scope):
                conn.execute(insert, args)
        mig.downgrade(conn)
        for table in ("vendor_score", "vendor_score_dispute"):
            assert conn.execute("SELECT to_regclass(%s)", (table,)).fetchone() == (None,)
        assert conn.execute(
            "SELECT count(*) FROM pg_proc WHERE proname = 'vendor_score_dispute_guard'"
        ).fetchone() == (0,)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor").fetchone() == (1,)
        mig.upgrade(conn)
        with tenant_transaction(conn, scope):
            assert conn.execute("SELECT count(*) FROM vendor_score").fetchone() == (0,)
