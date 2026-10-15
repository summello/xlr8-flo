"""Frozen adapter material, direct outcomes and scoped reservation reads."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import Outcome, OutcomeContext
from flo.modules.budget.service import reservation_remaining
from flo.modules.requisitions.service import RequisitionAdapter, RequisitionService
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lifecycle import funded
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lines import lines_db as lines_db


@pytest.mark.parametrize(
    "value,mode,status,remaining",
    [
        (Outcome.REJECTED, "permanent", "rejected", 0),
        (Outcome.REJECTED, "return_to_draft", "draft", 0),
        (Outcome.RETURNED, None, "draft", 60),
        (Outcome.WITHDRAWN, None, "withdrawn", 0),
    ],
)
def test_direct_adapter_outcome(lifecycle_db, value, mode, status, remaining):
    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    with tenant_transaction(db[0], Scope(db[1])):
        RequisitionAdapter().on_outcome(
            db[0],
            Scope(db[1]),
            row.id,
            value,
            OutcomeContext(db[3], None, None, lambda p, t: True, mode),
        )
    assert RequisitionService(db[0], Scope(db[1])).get(row.id).status == status
    assert reservation_remaining(
        db[0], Scope(db[1]), row.reservation.entry_id
    ).remaining == Decimal(remaining)


def test_material_summaries_placeholder_and_foreign_reservation(lifecycle_db):
    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    adapter = RequisitionAdapter()
    with tenant_transaction(db[0], Scope(db[1])):
        snapshot = adapter.snapshot(db[0], Scope(db[1]), row.id)
        assert adapter.fields() == {"amount": "money"}
        assert snapshot.material == {
            "amount": "60.0000",
            "project_id": str(row.project_id),
            "ledger_account_code": "L",
            "catalogue_items": "-",
            "line_count": "1",
        }
        summary = adapter.summaries(db[0], Scope(db[1]), [row.id])[row.id]
        assert summary.note is None and summary.href == f"/requisitions/{row.id}"
        with pytest.raises(ProblemError) as error:
            adapter.on_outcome(
                db[0],
                Scope(db[1]),
                row.id,
                Outcome.APPROVED,
                OutcomeContext(db[3], None, uuid4(), lambda p, t: True),
            )
        assert error.value.code == ErrorCode.VALIDATION_FAILED
        assert error.value.errors[0].field == "assign_project_id"
    with pytest.raises(ProblemError) as error:
        reservation_remaining(db[0], Scope(db[2]), row.reservation.entry_id)
    assert error.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(ProblemError):
        reservation_remaining(db[0], Scope(db[1]), -1)


def test_status_event_rls_owner_and_system_attribution(lifecycle_db):
    import psycopg
    from psycopg import sql

    from flo.modules.requisitions.service import events

    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    conn = db[0]
    with tenant_transaction(conn, Scope(db[1])):
        conn.execute(
            "INSERT INTO requisition_status_event(org_id,requisition_id,from_status,to_status,"
            "actor_id,funding_round) VALUES(%s,%s,'draft','draft',%s,0)",
            (db[1], row.id, UUID(int=0)),
        )
    assert events(conn, Scope(db[1]), row.id)[0].system
    assert events(conn, Scope(db[1]), row.id)[0].actor_email is None
    role = "requisition_event_probe_" + uuid4().hex
    conn.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        conn.execute(
            sql.SQL("GRANT SELECT,INSERT ON requisition_status_event TO {}").format(
                sql.Identifier(role)
            )
        )
        conn.execute(
            sql.SQL("ALTER TABLE requisition_status_event OWNER TO {}").format(sql.Identifier(role))
        )
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(conn, Scope(db[1])):
            assert len(conn.execute("SELECT id FROM requisition_status_event").fetchall()) == 2
        with tenant_transaction(conn, Scope(db[2])):
            assert conn.execute("SELECT id FROM requisition_status_event").fetchall() == []
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            tenant_transaction(conn, Scope(db[1])),
        ):
            conn.execute(
                "INSERT INTO requisition_status_event(id,org_id,requisition_id,from_status,"
                "to_status,funding_round) VALUES(99999,%s,%s,'draft','draft',0)",
                (db[2], row.id),
            )
    finally:
        conn.execute("RESET ROLE")
        conn.execute(
            sql.SQL("ALTER TABLE requisition_status_event OWNER TO {}").format(
                sql.Identifier(conn.info.user)
            )
        )
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_outcome_requires_pending_status(lifecycle_db):
    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    adapter = RequisitionAdapter()
    context = OutcomeContext(db[3], None, None, lambda p, t: True)
    with tenant_transaction(db[0], Scope(db[1])):
        adapter.on_outcome(db[0], Scope(db[1]), row.id, Outcome.RETURNED, context)
        with pytest.raises(ProblemError) as error:
            adapter.on_outcome(db[0], Scope(db[1]), row.id, Outcome.REJECTED, context)
        assert error.value.checks["problem"] == "invalid_transition"
        assert reservation_remaining(db[0], Scope(db[1]), row.reservation.entry_id).remaining == 60
