"""Every RFQ change answers with the caller's can_edit / can_approve, as GET does (E12-S28)."""

import asyncio
import inspect
from datetime import timedelta

from fastapi.routing import APIRoute

from flo.api.health import app
from flo.modules.sourcing.schemas import RfqRead
from tests.isolation.test_real_sessions import client, login
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import approved, line_body, send
from tests.sourcing.test_rfq_content import vendor
from tests.sourcing.test_rfq_lifecycle import publish, ready
from tests.vendors.test_vendors import regrant


def flags(response):
    assert response.status_code in (200, 201), response.text
    body = response.json()
    return body["can_edit"], body["can_approve"]


def test_changes_answer_with_the_callers_edit_and_approve_flags(lifecycle_db):
    db, _ = lifecycle_db
    draft, _, _ = ready(db)
    live, _, _ = publish(db)
    row, line = approved(db)
    new_vendor = vendor(db, "W")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            created = await send(
                browser, "POST", "/rfqs", {"title": "P", "lines": [line_body(row, line, "1")]}
            )
            assert flags(created) == (True, True)
            patched = await send(
                browser,
                "PATCH",
                f"/rfqs/{draft.id}",
                {"terms": "Net 45"},
                {"If-Match": f'"{draft.edit_revision}"'},
            )
            assert flags(patched) == (True, True)
            invited = await send(
                browser, "POST", f"/rfqs/{draft.id}/invitees", {"vendor_id": str(new_vendor.id)}
            )
            assert flags(invited) == (True, True)
            extended = await send(
                browser,
                "POST",
                f"/rfqs/{live.id}/extend-deadline",
                {"deadline_at": (live.deadline_at + timedelta(days=1)).isoformat()},
            )
            assert flags(extended) == (True, True)

    asyncio.run(scenario())


def test_editor_without_approve_is_told_so(lifecycle_db):
    db, _ = lifecycle_db
    draft, _, _ = ready(db)
    regrant(db, ["rfq.view", "rfq.edit"])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            patched = await send(
                browser,
                "PATCH",
                f"/rfqs/{draft.id}",
                {"terms": "Net 45"},
                {"If-Match": f'"{draft.edit_revision}"'},
            )
            assert flags(patched) == (True, False)

    asyncio.run(scenario())


def test_every_rfq_read_route_goes_through_done():
    # app.routes holds routers as wrappers, and a route added straight to the app: flatten both.
    every = [
        inner
        for r in app.routes
        for inner in (r.original_router.routes if hasattr(r, "original_router") else [r])
    ]
    routes = [
        r
        for r in every
        if isinstance(r, APIRoute)
        and r.path.startswith("/api/v1/sourcing")
        and r.response_model is RfqRead
    ]
    assert len(routes) >= 17
    bare = [r.path for r in routes if "_done(" not in inspect.getsource(r.endpoint)]
    assert bare == []
