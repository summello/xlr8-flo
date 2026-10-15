"""Registered requisitions participate in project closure with live scoped counts."""

import pytest

from flo.kernel.errors import ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.projects.schemas import TransitionCreate
from flo.modules.projects.service import ProjectService
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_header import project
from tests.requisitions.test_lifecycle import command, funded
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lines import lines_db as lines_db
from tests.requisitions.test_submission_check import complete


def test_pending_blocks_closure_draft_does_not(lifecycle_db):
    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    svc = ProjectService(db[0], Scope(db[1]), db[3])
    p = svc.get(row.project_id)
    with pytest.raises(ProblemError) as error:
        svc.transition(p.id, TransitionCreate(to="completed"), str(p.version), lambda p: True)
    assert error.value.checks["problem"] == "closing_blocked"
    assert "1 open documents" in error.value.detail
    command(db, row, "withdrawn")
    assert (
        svc.transition(
            p.id, TransitionCreate(to="completed"), str(p.version), lambda p: True
        ).status
        == "completed"
    )
    from uuid import uuid4

    from tests.requisitions.test_header import unit

    p = project(db, unit(db, uuid4().hex))
    complete(db, p)
    assert (
        svc.transition(
            p.id, TransitionCreate(to="completed"), str(p.version), lambda p: True
        ).status
        == "completed"
    )
