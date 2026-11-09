"""ACC-008 walkthrough seed: the dev-only guard, and the seeded tenant carries the whole chain."""

import psycopg
import pytest

from flo.api import walkthrough_seed
from flo.api.walkthrough_seed import AlreadySeeded, main, refuse_reason, seed
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.org.test_bootstrap import settings


@pytest.fixture(autouse=True)
def only_the_vendor_adapter(monkeypatch):
    """Any run that imported api/health.py has registered every adapter already; leave only the
    vendor one (it registers on import), so the seed's own register_document_types() is proven."""
    from flo.modules.approvals import conditions

    monkeypatch.setattr(conditions, "_adapters", {"vendor": conditions._adapters["vendor"]})


PASSWORD = "disposable bootstrap test passphrase"
LOCAL = {
    "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
    "EMAIL_PROVIDER": "smtp",
    "STORAGE_PROVIDER": "local",
}


@pytest.mark.parametrize(
    "change",
    [
        {"DATABASE_URL": "postgresql://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/db"},
        {"DATABASE_URL": "postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/db"},
        {"DATABASE_URL": "postgresql://u:p@203.0.113.7:5432/db"},
        {"DATABASE_URL": None},
        {"EMAIL_PROVIDER": "resend"},
        {"EMAIL_PROVIDER": None},
        {"STORAGE_PROVIDER": "r2"},
        {"STORAGE_PROVIDER": None},
    ],
)
def test_the_guard_exits_2_before_settings_or_a_connection(monkeypatch, capsys, change):
    env = {k: v for k, v in (LOCAL | change).items() if v is not None}
    for name in LOCAL:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    def never(*_args, **_kwargs):
        raise AssertionError("the guard must refuse before Settings or a connection")

    monkeypatch.setattr(psycopg, "connect", never)
    monkeypatch.setattr(walkthrough_seed, "Settings", never)
    assert main([]) == 2
    assert capsys.readouterr().err.startswith("refused:")


def test_a_local_smtp_dev_stack_passes_the_guard():
    assert refuse_reason(LOCAL) is None
    assert refuse_reason(LOCAL | {"DATABASE_URL": "postgresql://u:p@127.0.0.1/db"}) is None
    assert refuse_reason(LOCAL | {"DATABASE_URL": "postgresql://u:p@[::1]/db"}) is None


def test_seed_creates_five_distinct_people_without_mfa_roles_and_refuses_a_second_run(
    bootstrap_db,
):
    conn = bootstrap_db
    result = seed(conn, settings(conn), password=PASSWORD)
    people = result["people"]
    assert len(set(people.values())) == 5
    assert conn.execute(
        "SELECT count(*) FROM user_role ur JOIN role r ON r.id = ur.role_id "
        "WHERE ur.user_id = ANY(%s) AND r.code IN ('organization-administrator', 'auditor')",
        (list(people.values()),),
    ).fetchone() == (0,)
    assert conn.execute(
        "SELECT count(*) FROM workflow_definition WHERE scope_type = 'bu'"
    ).fetchone() == (5,)
    assert conn.execute("SELECT status FROM vendor").fetchall() == [("active",)]
    tables = ("organization_code", "identity", "user_role", "vendor", "workflow_definition")

    def counts():
        return [conn.execute(f"SELECT count(*) FROM {t}").fetchone() for t in tables]  # noqa: S608

    before = counts()
    with pytest.raises(AlreadySeeded):
        seed(conn, settings(conn), password=PASSWORD)
    assert counts() == before


# -- the chain, as the five seeded people, through the real app --------------------------------


@pytest.fixture
def seeded(bootstrap_db, monkeypatch):
    from flo.api.health import app
    from flo.api.origin_auth import require_origin_secret
    from tests.org.test_bootstrap import connection_url

    conn = bootstrap_db
    monkeypatch.setenv("DATABASE_URL", connection_url(conn))
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", "A" * 43)
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_MEMORY_COST_KIB", "8192")
    app.dependency_overrides[require_origin_secret] = lambda: None
    app.middleware_stack = None
    result = seed(conn, settings(conn), password=PASSWORD)
    yield conn, app, result
    app.dependency_overrides.clear()
    app.middleware_stack = None


def test_the_seeded_tenant_carries_project_to_issued_po_with_the_walkthrough_values(
    seeded, monkeypatch
):
    import asyncio
    from contextlib import AsyncExitStack
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from flo.kernel.tenancy.context import Scope
    from flo.modules.sourcing.lifecycle import close_due
    from tests.isolation.test_real_sessions import client, headers, login

    conn, app, seeded_tenant = seeded
    unit_id, vendor_id = seeded_tenant["unit_id"], seeded_tenant["vendor_id"]
    people = seeded_tenant["people"]

    def one(query, *params):
        return conn.execute(query, params).fetchone()[0]

    async def scenario():
        async with AsyncExitStack() as stack:
            browsers = {}
            for key, (email, _) in walkthrough_seed.PEOPLE.items():
                browsers[key] = await stack.enter_async_context(client(app))
                await login(browsers[key], email, enroll=False)  # no MFA role: email and password

            async def call(who, method, path, body=None, expect=(200, 201), **extra):
                browser = browsers[who]
                result = await browser.request(
                    method,
                    "/api/v1" + path,
                    json=body,
                    headers=headers(browser, uuid4().hex) | {k: str(v) for k, v in extra.items()},
                )
                assert result.status_code in expect, (method, path, result.text)
                return result.json() if result.content else None

            async def approve(who, kind, doc, table):
                from flo.modules.approvals.service import active_instance

                instance = active_instance(conn, Scope(seeded_tenant["org_id"]), kind, doc)
                version = one(f"SELECT version FROM {table} WHERE id = %s", doc)  # noqa: S608
                await call(
                    who,
                    "POST",
                    f"/approvals/tasks/{instance.tasks[-1].id}/decisions",
                    {"outcome": "approve", "document_version": version},
                )

            # project: create, submit, approve
            project = await call(
                "requester",
                "POST",
                "/projects",
                {
                    "bu_id": str(unit_id),
                    "name": "Pump Station Refit",
                    "department_code": "OPS",
                    "ledger_account_code": "6100",
                    "currency": "USD",
                    "budget": {"amount": "50000.00", "currency": "USD"},
                    "sponsor_ids": [str(people["requester"])],
                },
            )
            await call(
                "requester",
                "POST",
                f"/projects/{project['id']}/transitions",
                {"to": "approval_pending"},
                **{"If-Match": 1},
            )
            await approve("approver", "project", project["id"], "project")
            assert one("SELECT status FROM project WHERE id = %s", project["id"]) == "active"

            # requisition: create, one line, submit, approve
            ship_to = one(
                "SELECT id FROM org_address WHERE unit_id = %s AND kind = 'ship_to'", unit_id
            )
            requisition = await call(
                "requester",
                "POST",
                "/requisitions",
                {
                    "title": "Pump spare kits",
                    "project_id": project["id"],
                    "department_code": "OPS",
                    "ledger_account_code": "6100",
                    "needed_by": str((datetime.now(UTC) + timedelta(days=30)).date()),
                    "delivery_address_id": str(ship_to),
                    "justification": "Spare kits for the pump station refit",
                },
            )
            await call(
                "requester",
                "POST",
                f"/requisitions/{requisition['id']}/lines",
                {
                    "line_type": "item",
                    "description": "Pump spare kit",
                    "quantity": "2",
                    "uom_code": "EA",
                    "unit_price": "1000.00",
                },
                **{
                    "If-Match": one(
                        "SELECT version FROM requisition WHERE id = %s", requisition["id"]
                    )
                },
            )
            version = one("SELECT version FROM requisition WHERE id = %s", requisition["id"])
            await call(
                "requester",
                "POST",
                f"/requisitions/{requisition['id']}/transitions",
                {"to": "approval_pending"},
                **{"If-Match": version},
            )
            await approve("approver", "requisition", requisition["id"], "requisition")
            line_id = one(
                "SELECT id FROM requisition_line WHERE requisition_id = %s", requisition["id"]
            )
            version = one("SELECT version FROM requisition WHERE id = %s", requisition["id"])

            # RFQ: create, content, invite, publish, bid
            rfq = await call(
                "buyer",
                "POST",
                "/sourcing/rfqs",
                {
                    "title": "Pump spare kits RFQ",
                    "lines": [
                        {
                            "requisition_line_id": str(line_id),
                            "requisition_version": version,
                            "quantity": "2",
                        }
                    ],
                },
            )
            rfq_id = rfq["id"]
            await call(
                "buyer",
                "PATCH",
                f"/sourcing/rfqs/{rfq_id}",
                {
                    "specifications": "Two pump spare kits per the attached datasheet",
                    "delivery_location": "Operations warehouse",
                    "delivery_terms": "DAP",
                    "terms": "Net 30",
                    "deadline_at": (datetime.now(UTC) + timedelta(minutes=65)).isoformat(),
                },
                **{"If-Match": one("SELECT edit_revision FROM rfq WHERE id = %s", rfq_id)},
            )
            await call(
                "buyer", "POST", f"/sourcing/rfqs/{rfq_id}/invitees", {"vendor_id": str(vendor_id)}
            )
            await call("buyer", "POST", f"/sourcing/rfqs/{rfq_id}/publish")
            bid = await call(
                "buyer",
                "POST",
                f"/sourcing/rfqs/{rfq_id}/bids",
                {
                    "vendor_id": str(vendor_id),
                    "rfq_version": 1,
                    "currency": "USD",
                    "sent_at": datetime.now(UTC).isoformat(),
                },
            )
            rfq_line = one("SELECT id FROM rfq_line WHERE rfq_id = %s", rfq_id)
            attachment = uuid4()
            conn.execute(
                "INSERT INTO attachment(id, org_id, document_type, document_id, created_by) "
                "VALUES (%s, %s, 'bid', %s, %s)",
                (attachment, seeded_tenant["org_id"], bid["id"], people["buyer"]),
            )
            edited = await call(
                "buyer",
                "PATCH",
                f"/sourcing/rfqs/{rfq_id}/bids/{bid['id']}",
                {
                    "evidence_attachment_id": str(attachment),
                    "lines": [
                        {
                            "rfq_line_id": str(rfq_line),
                            "quoted": True,
                            "unit_price": "950.00",
                            "tax": "0",
                        }
                    ],
                },
                **{"If-Match": bid["row_version"]},
            )
            await call(
                "buyer",
                "POST",
                f"/sourcing/rfqs/{rfq_id}/bids/{bid['id']}/submit",
                {},
                **{"If-Match": edited["row_version"]},
            )

            # close (the job, after the deadline), evaluation, award
            conn.execute("UPDATE rfq SET deadline_at = now() - interval '1 minute'")
            assert close_due(conn, Scope(seeded_tenant["org_id"])) == 1
            await call("buyer", "POST", f"/sourcing/rfqs/{rfq_id}/start-evaluation")
            award = await call(
                "buyer", "POST", f"/sourcing/rfqs/{rfq_id}/award", {"basis": "best_average_cost"}
            )
            award = await call(
                "buyer",
                "PATCH",
                f"/sourcing/rfqs/{rfq_id}/award",
                {"rationale": "Only compliant bid, within budget"},
                **{"If-Match": award["version"]},
            )
            assert [(ln["vendor_id"], ln["quantity"]) for ln in award["lines"]] == [
                (str(vendor_id), "2.0000")
            ]
            await call("buyer", "POST", f"/sourcing/rfqs/{rfq_id}/award/submit")
            await approve("sourcing_approver", "award", award["id"], "award")

            # purchase order: create, submit, approve, issue
            po = await call(
                "buyer",
                "POST",
                "/purchase-orders",
                {
                    "award_id": award["id"],
                    "vendor_id": str(vendor_id),
                    "lines": [{"rfq_line_id": str(rfq_line), "quantity": "2"}],
                },
            )
            await call("buyer", "POST", f"/purchase-orders/{po['id']}/submit")
            await approve("po_approver", "po", po["id"], "purchase_order")
            version = one("SELECT version FROM purchase_order WHERE id = %s", po["id"])
            issued = await call(
                "po_approver",
                "POST",
                f"/purchase-orders/{po['id']}/issue",
                None,
                **{"If-Match": f'"{version}"'},
            )
            return issued

    issued = asyncio.run(scenario())
    assert issued["status"] == "issued"
    assert conn.execute(
        "SELECT recipient_email FROM po_transmission WHERE po_id = %s", (issued["id"],)
    ).fetchall() == [("vendor@example.com",)]
