"""WF-001: a downstream record is built only on the current, approved requisition (E09-S09)."""

from uuid import uuid4

import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.service import supersede
from flo.modules.requisitions.service import assert_current_upstream
from tests.requisitions.test_amendments import amend
from tests.requisitions.test_approval_effects import approve, linked
from tests.requisitions.test_approval_effects import state as state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_header import project, unit
from tests.requisitions.test_lifecycle import command, sql
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lines import lines_db as lines_db


def approved(db):
    row = linked(db, project(db, unit(db, uuid4().hex)))
    approve(db, row)
    return state(db, row.id)


def guard(db, row, version=None):
    assert_current_upstream(
        db[0], Scope(db[1]), row.id, row.version if version is None else version
    )


def refused(db, row, version=None):
    with pytest.raises(ProblemError) as error:
        guard(db, row, version)
    assert error.value.code == ErrorCode.CONFLICT
    assert error.value.checks["problem"] == "stale_version"


def test_approved_unchanged_requisition_at_its_version_passes(lifecycle_db):
    db, _ = lifecycle_db
    guard(db, approved(db))


def test_older_version_is_refused(lifecycle_db):
    db, _ = lifecycle_db
    row = approved(db)
    refused(db, row, row.version - 1)


def test_pending_amendment_is_refused(lifecycle_db):
    db, _ = lifecycle_db
    row = approved(db)
    amend(db, row, "100")  # material increase: stays pending
    assert state(db, row.id).amendment.status == "pending"
    refused(db, state(db, row.id))


def test_pending_amendment_alone_is_refused_even_when_the_approval_is_current(lifecycle_db):
    db, _ = lifecycle_db
    row = approved(db)
    guard(db, row)
    # Point the requisition at a revision without touching its approval or version, so the
    # amendment check is the only thing that can refuse it.
    sql(
        db,
        "UPDATE requisition SET pending_amendment_id=(SELECT id FROM requisition_revision "
        "WHERE requisition_id=%s LIMIT 1) WHERE id=%s RETURNING 1",
        (row.id, row.id),
    )
    refused(db, row)


def test_draft_and_approval_pending_are_refused(lifecycle_db):
    db, _ = lifecycle_db
    from tests.requisitions.test_submission_check import complete

    row, _ = complete(db, project(db, unit(db, uuid4().hex)), price="60")
    refused(db, state(db, row.id))
    submitted = linked(db, project(db, unit(db, uuid4().hex)))
    assert state(db, submitted.id).status == "approval_pending"
    refused(db, submitted)


@pytest.mark.parametrize("to", ["withdrawn", "cancelled"])
def test_terminal_requisition_is_refused(lifecycle_db, to):
    db, _ = lifecycle_db
    row = approved(db)
    command(db, row, to, reason="The board withdrew the scope entirely")
    refused(db, state(db, row.id))


def test_superseded_approval_is_refused(lifecycle_db):
    db, user = lifecycle_db
    row = approved(db)
    supersede(db[0], Scope(db[1]), "requisition", row.id, db[3], "Edited after approval")
    refused(db, row)
