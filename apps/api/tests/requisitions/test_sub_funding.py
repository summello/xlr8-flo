"""Sub-project funding: an approved requisition's money moves main -> sub (E09-S16).

REQ-002, ACC-001, WF-003, FIN-011, D-M2-19, D-M2-22.
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.requisitions import funding
from flo.modules.requisitions.schemas import RequisitionTransitionCreate
from flo.modules.requisitions.service import transition
from tests.requisitions.test_amendments import amend, approve, approved
from tests.requisitions.test_approval_effects import drift, fund, standalone, state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_header import project, unit
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lifecycle import sql
from tests.requisitions.test_lines import lines_db as lines_db


def balance(db, project_id):
    return sql(
        db, "SELECT allocated,reserved FROM project_balance WHERE project_id=%s", (project_id,)
    )[0]


def sub_of(db, row):
    return sql(db, "SELECT id FROM project WHERE generated_by_requisition_id=%s", (row.id,))[0][0]


def cancel(db, row):
    transition(
        db[0],
        Scope(db[1]),
        row.id,
        RequisitionTransitionCreate(to="cancelled", reason="Project was cancelled today"),
        str(state(db, row.id).version),
        db[3],
        lambda permission: True,
    )


def test_approval_moves_allocation_and_reservation_to_the_sub_project(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db, available="100")  # 60 reserved on main at submit
    sub = sub_of(db, row)
    assert balance(db, p.id) == (Decimal(40), Decimal(0))
    assert balance(db, sub) == (Decimal(60), Decimal(60))
    legs = sql(
        db,
        "SELECT project_id,amount FROM ledger_entry WHERE entry_type='transfer' AND "
        "transfer_group_id IS NOT NULL AND project_id IN (%s,%s)",
        (p.id, sub),
    )
    assert sorted(legs) == sorted([(p.id, Decimal(-60)), (sub, Decimal(60))])
    # Both legs carry the requisition's provenance, so requisition-keyed queries find them.
    assert (
        sql(
            db,
            "SELECT source_type,source_id FROM ledger_entry WHERE entry_type='transfer'",
        )
        == [("requisition", row.id)] * 2
    )
    assert state(db, row.id).reservation.remaining == "60.0000"
    assert sql(
        db,
        "SELECT project_id FROM ledger_entry WHERE id=(SELECT reservation_entry_id "
        "FROM requisition WHERE id=%s)",
        (row.id,),
    ) == [(sub,)]
    assert drift(db) == []


def test_replaying_the_funding_key_posts_nothing_twice(lifecycle_db):
    """FIN-011: the same key reuses the group."""
    db, _ = lifecycle_db
    p, row = approved(db, available="100")
    sub = sub_of(db, row)
    count = "SELECT count(*) FROM ledger_entry"
    before = sql(db, count)
    funding.move_funds(
        db[0],
        Scope(db[1]),
        sql_row(db, row),
        source_id=p.id,
        target_id=sub,
        amount=Decimal(60),
        key=f"req:{row.id}:fund:1",
        reason=f"Requisition {sql_row(db, row)['number']} approved, funds moved to sub-project",
        actor_id=db[3],
    )
    assert sql(db, count) == before


def sql_row(db, row):
    from flo.modules.requisitions.lifecycle import read_row
    from flo.modules.requisitions.models import RequisitionRepository

    return read_row(RequisitionRepository(db[0], Scope(db[1])), row.id)


def test_stop_after_approval_returns_funds_once_and_abandons_the_sub_project(lifecycle_db):
    """D-M2-22."""
    db, _ = lifecycle_db
    p, row = approved(db, available="100")
    sub = sub_of(db, row)
    cancel(db, row)
    assert balance(db, p.id) == (Decimal(100), Decimal(0))
    assert balance(db, sub) == (Decimal(0), Decimal(0))
    assert sql(db, "SELECT status FROM project WHERE id=%s", (sub,)) == [("abandoned",)]
    assert drift(db) == []
    entries = sql(db, "SELECT count(*) FROM ledger_entry")
    with pytest.raises(ProblemError) as error:
        cancel(db, row)
    assert error.value.checks["problem"] == "invalid_transition"
    assert sql(db, "SELECT count(*) FROM ledger_entry") == entries


def test_amendment_increase_and_decrease_move_the_difference(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db, available="110")
    sub = sub_of(db, row)
    assert balance(db, p.id) == (Decimal(50), Decimal(0))
    amend(db, row, "100")
    approve(db, row)
    assert balance(db, p.id) == (Decimal(10), Decimal(0))
    assert balance(db, sub) == (Decimal(100), Decimal(100))
    assert drift(db) == []
    amend(db, state(db, row.id), "70")
    approve(db, row)
    assert balance(db, p.id) == (Decimal(40), Decimal(0))
    assert balance(db, sub) == (Decimal(70), Decimal(70))
    assert drift(db) == []


def test_amendment_increase_beyond_main_funds_writes_nothing(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db, available="110")
    amend(db, row, "100")
    sql(
        db,
        "UPDATE project_balance SET allocated=allocated-30 WHERE project_id=%s RETURNING 1",
        (p.id,),
    )
    entries = sql(db, "SELECT count(*) FROM ledger_entry")
    with pytest.raises(ProblemError) as error:
        approve(db, row)
    assert error.value.code == ErrorCode.INSUFFICIENT_BUDGET
    assert sql(db, "SELECT count(*) FROM ledger_entry") == entries
    assert state(db, row.id).reservation.remaining == "60.0000"


def test_assigned_project_request_funds_a_sub_project_too(lifecycle_db):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    fund(db, p, "100")
    row = standalone(db)
    from tests.requisitions.test_approval_effects import approve as approve_assigned

    approve_assigned(db, row, p.id)
    sub = sub_of(db, row)
    assert balance(db, p.id) == (Decimal(40), Decimal(0))
    assert balance(db, sub) == (Decimal(60), Decimal(60))
    assert drift(db) == []


def test_keyed_final_approval_of_a_project_linked_requisition_replays(lifecycle_db):
    """FIN-011: one key, two POSTs of the final decision, one funding."""
    import asyncio

    from flo.api.health import app
    from flo.modules.approvals.service import get_instance
    from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
    from flo.modules.org.schemas import SettingPut
    from flo.modules.org.service import OrgService
    from tests.requisitions.test_approval_effects import linked
    from tests.requisitions.test_header import client, headers, login

    db, _ = lifecycle_db
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(
        "approval.separation_of_duty", SettingPut(value=False)
    )
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    auth.grant_role(
        db[3], auth.seed_baseline_roles()["approver"].id, AuthorizationTarget.organization(db[1])
    )
    p = project(db, unit(db, uuid4().hex))
    row = linked(db, p)
    current = state(db, row.id)
    task = get_instance(db[0], Scope(db[1]), current.approval.instance_id).tasks[-1].id
    key = uuid4().hex

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            return [
                await browser.post(
                    f"/api/v1/approvals/tasks/{task}/decisions",
                    json={"outcome": "approve", "document_version": current.version},
                    headers=headers(browser, key),
                )
                for _ in range(2)
            ]

    first, second = asyncio.run(scenario())
    assert first.status_code == 200, first.text
    assert second.status_code == 200 and second.json() == first.json()
    sub = sub_of(db, row)
    assert sql(
        db,
        "SELECT count(DISTINCT transfer_group_id),count(*) FROM ledger_entry "
        "WHERE entry_type='transfer'",
    ) == [(1, 2)]
    assert sql(
        db,
        "SELECT count(*) FROM ledger_entry WHERE entry_type='reservation' AND project_id=%s",
        (sub,),
    ) == [(1,)]
    assert balance(db, p.id) == (Decimal(40), Decimal(0))
    assert balance(db, sub) == (Decimal(60), Decimal(60))
    assert drift(db) == []
