"""An issued purchase order stays immutable once closed or cancelled (E13-S26)."""

import psycopg
import pytest

from tests.purchasing.test_changes import bootstrap_db as bootstrap_db  # noqa: F401
from tests.purchasing.test_changes import header_db as header_db  # noqa: F401
from tests.purchasing.test_changes import issued
from tests.purchasing.test_changes import lifecycle_db as lifecycle_db  # noqa: F401
from tests.purchasing.test_changes import lines_db as lines_db  # noqa: F401
from tests.purchasing.test_issue import approve
from tests.purchasing.test_po_lifecycle import move
from tests.purchasing.test_purchase_orders import create, ready
from tests.sourcing.test_rfq import sql

LINE_INSERT = (
    "INSERT INTO po_line SELECT org_id, po_id, 9, award_id, rfq_line_id, vendor_id, "
    "requisition_id, requisition_line_id, description, uom_code, quantity, unit_price, "
    "project_id, department_code, ledger_account_code, price, tax, freight, line_total, "
    "budget_amount, budget_amount_project FROM po_line WHERE po_id=%s RETURNING 1"
)


def refuses(db, statement, args):
    with pytest.raises(psycopg.errors.CheckViolation, match="issued_is_immutable"):
        sql(db, statement, args)


@pytest.mark.parametrize("action", ["close", "cancel"])
def test_an_issued_then_terminal_po_refuses_raw_writes(lifecycle_db, action):
    db, _ = lifecycle_db
    po, _ = issued(db)  # issued through the service, so stamped
    move(db, po, action, reason="done")
    (status, version) = sql(db, "SELECT status, version FROM purchase_order")[0]
    assert status == {"close": "closed", "cancel": "cancelled"}[action]
    for statement in (  # plant: dropping this status from the protected set lets these through
        "UPDATE purchase_order SET terms='x' WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET currency='EUR' WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET status='issued', version=version+1 WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET status='draft' WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET version=version+1 WHERE id=%s RETURNING 1",
        "DELETE FROM purchase_order WHERE id=%s RETURNING 1",
        "UPDATE po_line SET tax=1 WHERE po_id=%s RETURNING 1",
        "DELETE FROM po_line WHERE po_id=%s RETURNING 1",
        LINE_INSERT,
    ):
        refuses(db, statement, (po.id,))
    assert sql(db, "SELECT status, version FROM purchase_order") == [(status, version)]


def test_an_unstamped_issued_po_cannot_become_terminal_until_stamped(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    sql(db, "UPDATE purchase_order SET status='issued', version=version+1 RETURNING 1")
    for target in ("closed", "cancelled"):  # plant: removing the stamped-only rule lets it pass
        refuses(
            db,
            "UPDATE purchase_order SET status=%s, version=version+1 WHERE id=%s RETURNING 1",
            (target, po.id),
        )
    sql(
        db,
        "UPDATE purchase_order SET issued_at=now(), issued_by=%s WHERE id=%s RETURNING 1",
        (db[3], po.id),
    )
    sql(
        db,
        "UPDATE purchase_order SET status='closed', version=version+1 WHERE id=%s RETURNING 1",
        (po.id,),
    )
    refuses(db, "UPDATE purchase_order SET terms='x' WHERE id=%s RETURNING 1", (po.id,))


def test_a_po_cancelled_before_issue_keeps_its_old_freedom_but_not_its_status(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    move(db, po, "cancel", reason="no longer needed")
    assert sql(db, "SELECT status, issued_at FROM purchase_order") == [("cancelled", None)]
    sql(db, "UPDATE purchase_order SET terms='x' WHERE id=%s RETURNING 1", (po.id,))
    sql(db, "UPDATE po_line SET tax=0 WHERE po_id=%s RETURNING 1", (po.id,))
    refuses(  # plant: protecting every cancelled PO (or no TG_OP guard) breaks the above/below
        db,
        "UPDATE purchase_order SET status='draft', version=version+1 WHERE id=%s RETURNING 1",
        (po.id,),
    )
    sql(db, "DELETE FROM po_line WHERE po_id=%s RETURNING 1", (po.id,))
    sql(db, "DELETE FROM purchase_order WHERE id=%s RETURNING 1", (po.id,))
    assert sql(db, "SELECT count(*) FROM purchase_order") == [(0,)]
