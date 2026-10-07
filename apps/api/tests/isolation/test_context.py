from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from flo.kernel.tenancy.context import TenantScopeMissing, current_scope
from flo.kernel.tenancy.middleware import install_tenant_context


@dataclass(frozen=True)
class AuthenticatedSession:
    org_id: UUID


def test_middleware_uses_only_server_side_session_and_clears_each_request() -> None:
    org_a, org_b = uuid4(), uuid4()
    sessions = {
        "opaque-a": AuthenticatedSession(org_a),
        "opaque-b": AuthenticatedSession(org_b),
    }
    app = FastAPI()

    def resolve_session(request: Request) -> AuthenticatedSession | None:
        return sessions.get(request.cookies.get("flo_session", ""))

    install_tenant_context(app, resolve_session)

    @app.get("/scope")
    def read_scope() -> dict[str, str | None]:
        try:
            org_id = str(current_scope().org_id)
        except TenantScopeMissing:
            org_id = None
        return {"org_id": org_id}

    with TestClient(app) as client:
        assert client.get("/scope", cookies={"flo_session": "opaque-a"}).json() == {
            "org_id": str(org_a)
        }
        assert client.get("/scope", cookies={"flo_session": "opaque-b"}).json() == {
            "org_id": str(org_b)
        }
        assert client.get(
            "/scope",
            headers={"org_id": str(org_a)},
            params={"org_id": str(org_a)},
        ).json() == {"org_id": None}
