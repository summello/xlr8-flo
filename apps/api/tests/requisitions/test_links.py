"""The linked-record graph: computed per provider, filtered per node (E09-S09, REQ-009)."""

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from flo.api.collaboration import get_collaboration_storage
from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
from flo.modules.requisitions import links as links_module
from flo.modules.requisitions.links import links
from flo.modules.requisitions.schemas import LinkAccess, LinkedNode, LinkedRecord
from tests.collaboration.test_attachments import document, upload
from tests.imports.test_upload import MemoryStorage
from tests.isolation.test_real_sessions import headers
from tests.requisitions.test_approval_effects import approve, linked
from tests.requisitions.test_approval_effects import state as state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import client, draft, login, project, send, unit
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lifecycle import command
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lines import lines_db as lines_db

ORDER = (
    "project",
    "generated_project",
    "revision",
    "approval",
    "ledger_entry",
    "attachment",
    "note",
)


def approved(db):
    p = project(db, unit(db, uuid4().hex))
    row = linked(db, p)
    approve(db, row)
    return p, state(db, row.id)


def allow_all(permission, scope_type, target_id):
    return True


def graph(db, row, check=allow_all):
    return links(db[0], Scope(db[1]), row.id, check)


def test_graph_after_submit_approve_attachment_and_note(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db)
    app.dependency_overrides[get_collaboration_storage] = MemoryStorage

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            note = await browser.post(
                document(row) + "/notes",
                json={"body": "Checked with finance", "visibility": "internal"},
                headers=headers(browser, uuid4().hex),
            )
            assert note.status_code == 201, note.text
            assert (await upload(browser, document(row) + "/attachments")).status_code == 201
            response = await send(browser, "GET", f"/{row.id}/links")
            assert response.status_code == 200, response.text
            return response.json()

    body = asyncio.run(scenario())
    nodes = body["nodes"]
    assert body["partial"] is False
    assert {n["kind"] for n in nodes} == set(ORDER)
    # Ordered by time, then by the stated kind order.
    assert nodes == sorted(nodes, key=lambda n: (n["at"], ORDER.index(n["kind"]), n["key"]))
    by_kind = {n["kind"]: n for n in nodes}
    assert by_kind["project"]["id"] == str(p.id) and by_kind["project"]["relation"] == "funds"
    assert by_kind["generated_project"]["relation"] == "generated"
    assert by_kind["generated_project"]["state"] == "Active"
    assert by_kind["revision"]["relation"] == "revision_of"
    assert by_kind["approval"]["relation"] == "approved_by"
    assert by_kind["approval"]["href"] == "/approvals"
    assert by_kind["approval"]["label"].startswith("Approval · Approved · ")
    assert "APR-" not in by_kind["approval"]["label"]
    ledger = by_kind["ledger_entry"]
    assert ledger["relation"] == "reserves" and ledger["currency"] == "USD"
    assert ledger["amount"] == "60.0000"
    assert ledger["label"].endswith("Reservation 60.00 USD")
    # The live reservation sits on the generated sub-project (E09-S16).
    sub_id = by_kind["generated_project"]["id"]
    assert ledger["href"] == f"/projects/{sub_id}/dashboard?bucket=all&entry={ledger['id']}#ledger"
    assert by_kind["attachment"]["relation"] == "attached"
    assert by_kind["note"]["relation"] == "noted"
    assert {(e["from"], e["to"], e["relation"]) for e in body["edges"]} == {
        (f"requisition:{row.id}", n["key"], n["relation"]) for n in nodes
    }


def test_withdraw_after_approval_shows_release_and_abandoned_sub_project(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)
    command(db, row, "withdrawn")
    body = graph(db, row)
    relations = {n.relation for n in body.nodes if n.kind == "ledger_entry"}
    assert relations == {"reserves", "releases", "transfer"}
    release = next(n for n in body.nodes if n.relation == "releases")
    assert release.label.endswith("Release (60.00) USD") and release.amount == "-60.0000"
    sub = next(n for n in body.nodes if n.kind == "generated_project")
    assert (sub.state, sub.state_tone) == ("Abandoned", "danger")


def test_unreadable_nodes_vanish_without_a_trace(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)
    full = graph(db, row)
    sub = next(n for n in full.nodes if n.kind == "generated_project")

    no_ledger = graph(db, row, lambda permission, *_: permission != "ledger.read")
    assert not [n for n in no_ledger.nodes if n.kind == "ledger_entry"]
    assert not [e for e in no_ledger.edges if e.to.startswith("ledger_entry:")]
    assert "ledger" not in no_ledger.model_dump_json().lower()
    assert len(no_ledger.nodes) < len(full.nodes)

    no_sub = graph(db, row, lambda permission, scope, target: str(target) != sub.id)
    assert [n.kind for n in no_sub.nodes if n.kind == "generated_project"] == []
    assert [n for n in no_sub.nodes if n.kind == "project"]
    assert sub.key not in no_sub.model_dump_json()


def test_view_only_caller_over_http_sees_no_project_or_ledger_nodes(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                )
                auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
                role = auth.create_role("viewer", "View only")
                auth.grant_permission(role.id, "requisition.view")
                auth.grant_role(db[3], role.id, AuthorizationTarget.organization(db[1]))
            response = await send(browser, "GET", f"/{row.id}/links")
            assert response.status_code == 200, response.text
            return response.json()

    kinds = {n["kind"] for n in asyncio.run(scenario())["nodes"]}
    assert kinds == {"revision", "approval"}


def rfq_node(at, **changes):
    return {
        "key": "rfq:1",
        "kind": "rfq",
        "id": "1",
        "label": "RFQ 1",
        "state": "Open",
        "state_tone": "info",
        "href": "/rfqs/1",
        "at": at,
        "relation": "sourced_by",
    } | changes


def test_a_provider_written_against_the_public_type_appears_and_is_authorized(
    lifecycle_db, monkeypatch
):
    db, _ = lifecycle_db
    _, row = approved(db)
    access = LinkAccess(permission="rfq.view", scope_type="project", target_id=row.project_id)

    def rfq(conn, scope, requisition_id, read_check):
        return [LinkedNode(**rfq_node(row.updated_at), access=access)]

    monkeypatch.setitem(links_module._PROVIDERS, "rfq", rfq)
    body = graph(db, row)
    node = next(n for n in body.nodes if n.kind == "rfq")
    assert node.relation == "sourced_by" and not body.partial
    assert "access" not in node.model_dump()
    assert any(e.to == "rfq:1" and e.relation == "sourced_by" for e in body.edges)
    hidden = graph(db, row, lambda permission, *_: permission != "rfq.view")
    assert not [n for n in hidden.nodes if n.kind == "rfq"]


def test_a_provider_node_without_access_is_omitted_not_shown_or_a_500(
    lifecycle_db, monkeypatch, caplog
):
    db, _ = lifecycle_db
    _, row = approved(db)

    def careless(conn, scope, requisition_id, read_check):
        return [LinkedRecord(**rfq_node(row.updated_at))]

    monkeypatch.setitem(links_module._PROVIDERS, "rfq", careless)
    body = graph(db, row)
    assert not [n for n in body.nodes if n.kind == "rfq"]
    assert "without access" in caplog.text
    # The required field makes it impossible to build an unauthorized LinkedNode.
    with pytest.raises(ValidationError):
        LinkedNode(**rfq_node(row.updated_at))


def test_a_failing_provider_marks_partial_and_keeps_the_rest(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    _, row = approved(db)

    def explodes(conn, scope, requisition_id, read_check):
        raise RuntimeError("provider down")

    def breaks_the_transaction(conn, scope, requisition_id, read_check):
        conn.execute("SELECT 1/0")
        return []

    monkeypatch.setitem(links_module._PROVIDERS, "rfq", explodes)
    monkeypatch.setitem(links_module._PROVIDERS, "bid", breaks_the_transaction)
    body = graph(db, row)
    assert body.partial is True
    assert {n.kind for n in body.nodes} == set(ORDER) - {"attachment", "note"}


def test_foreign_tenant_and_ungranted_links_are_404(lifecycle_db):
    db, _ = lifecycle_db
    foreign = draft(db, p=project(db, org=db[2]), org=db[2])
    _, row = approved(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert (await send(browser, "GET", f"/{foreign.id}/links")).status_code == 404
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                )
            assert (await send(browser, "GET", f"/{row.id}/links")).status_code == 404

    asyncio.run(scenario())
