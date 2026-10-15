"""APR-011/ACC-007 pinned real instances and server search/paging."""

from tests.approvals.test_engine import begin, published
from tests.approvals.test_engine import setup as setup
from tests.approvals.test_workflow_definitions import draft, svc
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db


def test_in_flight_counts_only_running_non_published_versions(setup):
    db = setup[0]
    row, first = published(setup)
    instance = begin(setup)
    assert svc(db).list(None, None).rows[0].in_flight == 0
    d = draft(db, row, first.graph)
    svc(db).publish(row.id, d.version, True)
    listed = svc(db).list(None, None).rows[0]
    assert listed.in_flight == 1
    assert listed.published_version == 2 and listed.draft_version is None
    assert listed.versions == []
    lineage = svc(db).get(row.id).versions
    assert lineage[0].in_flight == 1 and lineage[1].in_flight == 0
    assert listed.edited_at and listed.edited_by_name.endswith("@example.test")
    assert listed.applies_to.startswith("Capital Requisitions · ")
    assert svc(db).list(None, None, "REVIEW").rows[0].id == row.id
    assert svc(db).list(None, None, "missing").rows == []
    db[0].execute("UPDATE approval_instance SET status='withdrawn' WHERE id=%s", (instance.id,))
    assert svc(db).list(None, None).rows[0].in_flight == 0
    assert svc(db).get_version(row.id, 1).id == first.id


def test_search_cursor_is_bound_to_its_filter(setup):
    import base64
    import json

    import pytest

    from flo.kernel.errors import ProblemError

    row, _ = published(setup)
    cursor = base64.urlsafe_b64encode(
        json.dumps(
            {
                "created_at": row.created_at.isoformat(),
                "id": str(row.id),
                "document_type": None,
                "search": "Review",
            }
        ).encode()
    ).decode()
    with pytest.raises(ProblemError) as exc:
        svc(setup[0]).list(None, cursor, "Different")
    assert exc.value.errors[0].field == "/cursor"


def test_missing_instance_table_reports_partial_counts_and_preserves_versions(setup):
    import pytest

    db = setup[0]
    row, _ = published(setup)

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback), db[0].transaction():
        db[0].execute("DROP TABLE approval_instance CASCADE")
        read = svc(db).get(row.id)
        assert read.in_flight is None
        assert read.published_version == 1 and read.versions[0].in_flight is None
        assert svc(db).list(None, None).rows[0].id == row.id
        raise Rollback
    assert svc(db).get(row.id).in_flight == 0
