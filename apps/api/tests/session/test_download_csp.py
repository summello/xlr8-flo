"""Handler CSP may only tighten the fixed browser policy with exactly sandbox."""

import pytest
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from flo.kernel.session.middleware import SECURITY_HEADERS, BrowserSecurityHeadersMiddleware


@pytest.mark.parametrize(
    "policy,expected",
    [
        ("default-src *", False),
        ("sandbox", True),
        ("sandbox ", False),
        ("sandbox; default-src *", False),
    ],
)
def test_handler_csp_only_exact_sandbox_is_preserved(policy, expected):
    async def handler(request):
        return Response(headers={"Content-Security-Policy": policy})

    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(BrowserSecurityHeadersMiddleware)
    with TestClient(app) as client:
        response = client.get("/")
    fixed = SECURITY_HEADERS[b"content-security-policy"].decode()
    assert response.headers.get_list("content-security-policy") == (
        ["sandbox", fixed] if expected else [fixed]
    )
