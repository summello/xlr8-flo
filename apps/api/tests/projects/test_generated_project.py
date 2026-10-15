"""Requisition-generated projects are born active and carry the marker (E09-S06)."""

from datetime import date
from uuid import uuid4

import pytest

from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.projects.service import create_generated_project, generated_for
from flo.modules.requisitions.schemas import RequisitionCreate
from flo.modules.requisitions.service import RequisitionService
from tests.projects.conftest import body, service, settings, unit
from tests.projects.conftest import project_db as project_db


def generate(db, parent, requisition, **changes):
    values = dict(
        settings=settings(db),
        actor_id=db.actor_id,
        parent_id=parent.id,
        bu_id=parent.bu_id,
        name="Generated",
        description="Created from REQ on approval.",
        department_code="D",
        ledger_account_code="L",
        currency="USD",
        owner_id=db.actor_id,
        planned_end=date(2030, 1, 1),
        generated_by_requisition_id=requisition.id,
        reason="Generated from requisition REQ",
    )
    with tenant_transaction(db.connection, Scope(db.org_a)):
        return create_generated_project(db.connection, Scope(db.org_a), **(values | changes))


def source(db, parent):
    return RequisitionService(db.connection, Scope(db.org_a), db.actor_id, settings(db)).create(
        RequisitionCreate(title="Source", project_id=parent.id)
    )


def test_generated_project_is_active_marked_audited_and_found(project_db):
    db = project_db
    parent = service(db).create(body(unit(db)))
    requisition = source(db, parent)
    child = generate(db, parent, requisition)
    assert child.status == "active" and child.parent_id == parent.id
    assert child.generated_by_requisition_id == requisition.id
    assert child.owner_id == db.actor_id and child.planned_end == date(2030, 1, 1)
    found = generated_for(db.connection, Scope(db.org_a), requisition.id)
    assert found is not None and found.id == child.id
    assert generated_for(db.connection, Scope(db.org_a), uuid4()) is None
    assert generated_for(db.connection, Scope(db.org_b), requisition.id) is None
    row = db.connection.execute(
        "SELECT reason,after FROM audit_log WHERE target_id=%s AND action='project.create'",
        (child.id,),
    ).fetchone()
    assert row[0] == "Generated from requisition REQ"
    assert row[1]["source"] == "requisition" and row[1]["requisition_id"] == str(requisition.id)
    assert row[1]["status"] == "active"


def test_one_generated_project_per_requisition_and_plain_create_stays_draft(project_db):
    db = project_db
    parent = service(db).create(body(unit(db)))
    assert parent.status == "draft" and parent.generated_by_requisition_id is None
    requisition = source(db, parent)
    generate(db, parent, requisition)
    with pytest.raises(Exception, match="project_generated_requisition"):
        generate(db, parent, requisition)
