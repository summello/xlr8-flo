"""Two simultaneous issues of one PO commit exactly once (E13-S05, real Postgres)."""

from tests.purchasing.test_issue import approve, ledger, run
from tests.purchasing.test_purchase_orders import _race, create, ready
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import sql


def test_two_simultaneous_issues_commit_once_and_return_the_same_result(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)

    def work(conn, _):
        return run(db, po, connection=conn).status

    assert _race(db, work) == ["issued", "issued"]  # plant 1: validate before the lock -> two
    assert len([e for e in ledger(db) if e[0] == "commitment"]) == 1
    assert sql(db, "SELECT issued_qty FROM requisition_line")[0][0] == 4
    assert sql(db, "SELECT count(*) FROM audit_log WHERE action='purchase_order.issue'") == [(1,)]
