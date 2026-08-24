from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from flo.kernel.errors import (
    ERROR_TAXONOMY,
    ErrorCode,
    ProblemDetails,
    ProblemError,
    ProblemFieldError,
    install_problem_details,
)

_request_logger = logging.getLogger("flo.tests.request")


class LineInput(BaseModel):
    amount: int


class RequisitionInput(BaseModel):
    lines: list[LineInput]


class ExplodingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            raise RuntimeError("middleware failure")
        await self._app(scope, receive, send)


@pytest.fixture
def problem_app() -> FastAPI:
    app = FastAPI(title="Problem test API", version="1.0.0")
    install_problem_details(app)

    @app.get("/api/v1/ok")
    def ok() -> dict[str, bool]:
        _request_logger.info("Request log")
        return {"ok": True}

    @app.get("/api/v1/bad-request")
    def bad_request() -> None:
        raise ProblemError(ErrorCode.BAD_REQUEST)

    @app.post("/api/v1/requisitions/{requisition_id}/approve")
    def approve(requisition_id: str) -> None:
        raise ProblemError(
            ErrorCode.INSUFFICIENT_BUDGET,
            detail=(
                "The project has 12,400.00 USD available and this request needs 18,000.00 USD."
            ),
            errors=(
                ProblemFieldError(
                    field="lines[2].amount",
                    message="exceeds remaining line budget",
                ),
            ),
        )

    @app.post("/api/v1/requisitions")
    def create_requisition(body: RequisitionInput) -> dict[str, int]:
        return {"lines": len(body.lines)}

    @app.get("/api/v1/unavailable")
    def unavailable() -> None:
        raise ProblemError(ErrorCode.SERVICE_UNAVAILABLE)

    @app.get("/api/v1/explode")
    def explode() -> None:
        query = "SELECT secret FROM credentials"
        path = "/srv/xlr8flo/internal/database.py"
        raise ZeroDivisionError(f"{query} at {path} in psycopg.connection")

    @app.get("/api/v1/records/{record_id}")
    def record(record_id: str) -> None:
        owning_tenant = {"foreign-record": "tenant-b"}.get(record_id)
        if owning_tenant != "tenant-a":
            raise HTTPException(status_code=404, detail=f"owner={owning_tenant}")

    @app.get("/api/v1/http/{status_code}")
    def http_error(status_code: int) -> None:
        raise HTTPException(status_code=status_code, detail="unsafe framework detail")

    return app


@pytest.fixture
def client(problem_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(problem_app, raise_server_exceptions=False) as test_client:
        yield test_client


def _without_request_identity(response_body: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in response_body.items()
        if key not in {"correlation_id", "instance"}
    }


def test_insufficient_budget_matches_the_public_problem_contract(client: TestClient) -> None:
    response = client.post("/api/v1/requisitions/8f2a/approve")

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["x-correlation-id"] == response.json()["correlation_id"]
    assert response.json() == {
        "type": "https://xlr8flo.app/errors/insufficient-budget",
        "title": "Insufficient available budget",
        "status": 409,
        "detail": ("The project has 12,400.00 USD available and this request needs 18,000.00 USD."),
        "instance": "/api/v1/requisitions/8f2a/approve",
        "correlation_id": response.headers["x-correlation-id"],
        "recovery": (
            "Reduce the requested amount, transfer funds into the project, or request an override."
        ),
        "errors": [
            {
                "field": "lines[2].amount",
                "message": "exceeds remaining line budget",
            }
        ],
    }


def test_validation_uses_field_paths_without_echoing_submitted_input(client: TestClient) -> None:
    submitted_secret = "password=correct-horse-battery-staple"

    response = client.post(
        "/api/v1/requisitions",
        json={"lines": [{"amount": 1}, {"amount": 2}, {"amount": submitted_secret}]},
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["errors"] == [
        {
            "field": "lines[2].amount",
            "message": "Input should be a valid integer, unable to parse string as an integer",
        }
    ]
    assert submitted_secret not in response.text
    assert "input" not in response.json()


def test_unhandled_exception_is_generic_but_full_traceback_is_correlated_server_side(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="flo.kernel.errors.handler")

    response = client.get("/api/v1/explode")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {"correlation_id": response.headers["x-correlation-id"]}
    for forbidden in (
        "Traceback",
        "ZeroDivisionError",
        "test_errors",
        "SELECT",
        "/srv/",
        "psycopg",
    ):
        assert forbidden not in response.text

    record = next(record for record in caplog.records if record.message == "Unhandled exception")
    assert record.correlation_id == response.headers["x-correlation-id"]
    assert record.exc_info is not None
    assert "Traceback" in caplog.text
    assert "ZeroDivisionError" in caplog.text
    assert "SELECT secret FROM credentials" in caplog.text
    assert "/srv/xlr8flo/internal/database.py" in caplog.text


def test_every_response_has_a_server_generated_correlation_header(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="flo.tests.request")

    response = client.get(
        "/api/v1/ok",
        headers={"X-Correlation-ID": "client-controlled-value"},
    )

    correlation_id = response.headers["x-correlation-id"]
    assert response.status_code == 200
    assert correlation_id != "client-controlled-value"
    assert len(correlation_id) == 32
    request_log = next(record for record in caplog.records if record.message == "Request log")
    assert request_log.correlation_id == correlation_id


def test_exception_from_user_middleware_is_a_correlated_problem_document() -> None:
    app = FastAPI(title="Middleware failure API", version="1.0.0")
    app.add_middleware(ExplodingMiddleware)
    # This installer must remain last so correlation wraps every user middleware.
    install_problem_details(app)

    with TestClient(app, raise_server_exceptions=False) as middleware_client:
        response = middleware_client.get("/")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {"correlation_id": response.headers["x-correlation-id"]}


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (401, ErrorCode.UNAUTHORIZED),
        (403, ErrorCode.FORBIDDEN),
        (409, ErrorCode.CONFLICT),
        (415, ErrorCode.UNSUPPORTED_MEDIA_TYPE),
        (429, ErrorCode.TOO_MANY_REQUESTS),
        (503, ErrorCode.SERVICE_UNAVAILABLE),
    ],
)
def test_http_exceptions_use_status_specific_taxonomy(
    client: TestClient,
    status_code: int,
    code: ErrorCode,
) -> None:
    response = client.get(f"/api/v1/http/{status_code}")
    taxonomy = ERROR_TAXONOMY[code]

    assert response.status_code == status_code
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == taxonomy.type_uri
    assert response.json()["title"] == taxonomy.title
    assert response.json()["recovery"] == taxonomy.recovery


def test_unmapped_http_exception_preserves_its_status_without_leaking_detail(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/http/418")

    assert response.status_code == 418
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["status"] == 418
    assert response.json()["type"] == "about:blank"
    assert response.json()["recovery"].strip()
    assert "unsafe framework detail" not in response.text


def test_all_emitted_4xx_and_5xx_responses_are_problem_json_with_recovery_on_4xx(
    client: TestClient,
) -> None:
    responses = [
        client.get("/api/v1/bad-request"),
        client.get("/api/v1/missing"),
        client.post("/api/v1/ok"),
        client.post(
            "/api/v1/requisitions",
            json={"lines": [{"amount": "not-an-integer"}]},
        ),
        client.post("/api/v1/requisitions/8f2a/approve"),
        client.get("/api/v1/unavailable"),
        client.get("/api/v1/explode"),
    ]

    assert {response.status_code for response in responses} == {400, 404, 405, 409, 422, 500, 503}
    for response in responses:
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["x-correlation-id"] == response.json()["correlation_id"]
        ProblemDetails.model_validate(response.json())
        if 400 <= response.status_code < 500:
            assert response.json()["recovery"].strip()


def test_error_code_taxonomy_is_complete_and_one_to_one() -> None:
    assert set(ERROR_TAXONOMY) == set(ErrorCode)
    assert len({entry.type_uri for entry in ERROR_TAXONOMY.values()}) == len(ErrorCode)
    for code, entry in ERROR_TAXONOMY.items():
        assert entry.type_uri == f"https://xlr8flo.app/errors/{code.value}"
        assert 400 <= entry.status <= 599
        if 400 <= entry.status < 500:
            assert entry.recovery is not None
            assert entry.recovery.strip()


def test_problem_schema_rejects_a_client_error_without_recovery() -> None:
    with pytest.raises(ValueError, match="recovery is required for client errors"):
        ProblemDetails(correlation_id="correlation", status=400)


def test_foreign_and_nonexistent_records_have_indistinguishable_not_found_responses(
    client: TestClient,
) -> None:
    foreign = client.get("/api/v1/records/foreign-record")
    nonexistent = client.get("/api/v1/records/nonexistent-record")

    assert foreign.status_code == nonexistent.status_code == 404
    assert _without_request_identity(foreign.json()) == _without_request_identity(
        nonexistent.json()
    )
    assert "tenant-b" not in foreign.text


def test_openapi_documents_problem_schema_for_every_operation(problem_app: FastAPI) -> None:
    schema = problem_app.openapi()

    assert schema["openapi"].startswith("3.1.")
    assert "ProblemDetails" in schema["components"]["schemas"]
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            responses = operation["responses"]
            for status in ("4XX", "5XX"):
                content = responses[status]["content"]
                assert content == {
                    "application/problem+json": {
                        "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                    }
                }
            assert "422" not in responses
