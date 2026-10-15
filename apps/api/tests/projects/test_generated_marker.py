from uuid import uuid4

import pytest

from flo.kernel.errors import ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.projects.service import is_requisition_generated
from flo.modules.requisitions.schemas import RequisitionCreate
from flo.modules.requisitions.service import RequisitionService
from tests.projects.conftest import body, service, settings, unit
from tests.projects.conftest import project_db as project_db


def test_is_requisition_generated_scoped_and_unknown(project_db):
    db = project_db
    bu = unit(db)
    p = service(db).create(body(bu))
    assert not is_requisition_generated(db.connection, Scope(db.org_a), p.id)
    req = RequisitionService(db.connection, Scope(db.org_a), db.actor_id, settings(db)).create(
        RequisitionCreate(title="Source", project_id=p.id)
    )
    with tenant_transaction(db.connection, Scope(db.org_a)):
        db.connection.execute(
            "UPDATE project SET generated_by_requisition_id=%s WHERE id=%s", (req.id, p.id)
        )
    assert is_requisition_generated(db.connection, Scope(db.org_a), p.id)
    for org, id in [(db.org_b, p.id), (db.org_a, uuid4())]:
        with pytest.raises(ProblemError) as error:
            is_requisition_generated(db.connection, Scope(org), id)
        assert error.value.code.value == "not-found"
