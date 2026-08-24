"""One response path for all API failures and request correlation."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from flo.kernel.errors.codes import ERROR_TAXONOMY, ErrorCode, ProblemError
from flo.kernel.errors.schema import ProblemDetails, ProblemFieldError
from flo.kernel.logging import (
    correlation_context,
    current_correlation_id,
    install_correlation_logging,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"
_logger = logging.getLogger(__name__)
_SAFE_HTTP_HEADERS = frozenset({"allow", "retry-after", "www-authenticate"})
_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})


def _correlation_id() -> str:
    """Return the request correlation id, generating a safe fallback if needed."""

    return current_correlation_id() or uuid4().hex


def _problem_response(
    problem: ProblemDetails,
    *,
    status_code: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response_headers = dict(headers or {})
    if "x-correlation-id" not in {name.lower() for name in response_headers}:
        response_headers["X-Correlation-ID"] = problem.correlation_id
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(mode="json", exclude_none=True),
        headers=response_headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _field_path(location: tuple[str | int, ...]) -> str:
    input_locations = {"body", "cookie", "header", "path", "query"}
    parts = location[1:] if location and location[0] in input_locations else location
    path = ""
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif path:
            path += f".{part}"
        else:
            path = part
    return path or "request"


def _validation_errors(exc: RequestValidationError) -> tuple[ProblemFieldError, ...]:
    return tuple(
        ProblemFieldError(
            field=_field_path(tuple(error["loc"])),
            message=str(error["msg"]),
        )
        for error in exc.errors()
    )


def _http_error_code(exc: HTTPException) -> ErrorCode | None:
    return {
        400: ErrorCode.BAD_REQUEST,
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        405: ErrorCode.METHOD_NOT_ALLOWED,
        409: ErrorCode.CONFLICT,
        415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        422: ErrorCode.VALIDATION_FAILED,
        429: ErrorCode.TOO_MANY_REQUESTS,
        503: ErrorCode.SERVICE_UNAVAILABLE,
    }.get(exc.status_code)


def _safe_http_headers(exc: HTTPException) -> dict[str, str]:
    if exc.headers is None:
        return {}
    return {key: value for key, value in exc.headers.items() if key.lower() in _SAFE_HTTP_HEADERS}


def _unmapped_http_problem(
    request: Request,
    exc: HTTPException,
    correlation_id: str,
) -> ProblemDetails:
    """Preserve an unknown HTTP status without exposing its potentially unsafe detail."""

    is_client_error = 400 <= exc.status_code < 500
    return ProblemDetails(
        type="about:blank",
        title=(
            "The request could not be processed"
            if is_client_error
            else "The request could not be completed"
        ),
        status=exc.status_code,
        detail=(
            "The request was not processed. No data was changed."
            if is_client_error
            else "An error prevented the request from completing."
        ),
        instance=request.url.path,
        correlation_id=correlation_id,
        recovery="Check the request and try again." if is_client_error else None,
    )


async def problem_exception_handler(request: Request, exc: Exception) -> Response:
    """Serialize every handled and unhandled exception through one safe path."""

    correlation_id = _correlation_id()
    if not isinstance(exc, (ProblemError, RequestValidationError, HTTPException)):
        taxonomy = ERROR_TAXONOMY[ErrorCode.INTERNAL_ERROR]
        if current_correlation_id() is None:
            with correlation_context(correlation_id):
                _logger.error(
                    "Unhandled exception",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
        else:
            _logger.error(
                "Unhandled exception",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        return _problem_response(
            ProblemDetails(correlation_id=correlation_id),
            status_code=taxonomy.status,
        )

    headers: dict[str, str] | None = None
    errors: tuple[ProblemFieldError, ...] | None = None
    detail: str | None = None
    checks: dict[str, str] | None = None
    code: ErrorCode | None
    if isinstance(exc, ProblemError):
        code = exc.code
        detail = exc.detail
        errors = exc.errors or None
        checks = exc.checks
    elif isinstance(exc, RequestValidationError):
        code = ErrorCode.VALIDATION_FAILED
        errors = _validation_errors(exc)
    else:
        code = _http_error_code(exc)
        headers = _safe_http_headers(exc)
        if code is None:
            problem = _unmapped_http_problem(request, exc, correlation_id)
            return _problem_response(problem, status_code=exc.status_code, headers=headers)

    taxonomy = ERROR_TAXONOMY[code]
    problem = ProblemDetails(
        type=taxonomy.type_uri,
        title=taxonomy.title,
        status=taxonomy.status,
        detail=detail or taxonomy.detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        recovery=taxonomy.recovery,
        errors=errors,
        checks=checks,
    )
    return _problem_response(problem, status_code=taxonomy.status, headers=headers)


class CorrelationIdMiddleware:
    """Generate correlation once and preserve it across all response paths."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        correlation_id = uuid4().hex
        response_started = False

        async def send_with_correlation(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = list(message.get("headers", []))
                header_name = b"x-correlation-id"
                headers = [(key, value) for key, value in headers if key.lower() != header_name]
                headers.append((header_name, correlation_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with correlation_context(correlation_id):
            try:
                await self._app(scope, receive, send_with_correlation)
            except Exception as exc:
                if response_started:
                    raise
                request = Request(scope, receive=receive)
                response = await problem_exception_handler(request, exc)
                await response(scope, receive, send_with_correlation)


def _response_documentation() -> dict[str, dict[str, Any]]:
    return {
        "4XX": {
            "description": "Client error expressed as RFC 9457 problem details.",
            "model": ProblemDetails,
        },
        "5XX": {
            "description": "Server error expressed as RFC 9457 problem details.",
            "model": ProblemDetails,
        },
    }


def _install_openapi_problem_responses(app: FastAPI) -> None:
    documentation = _response_documentation()
    app.router.responses.update(documentation)
    for route in app.routes:
        if isinstance(route, APIRoute):
            route.responses.update(documentation)

    original_openapi = app.openapi

    def problem_openapi() -> dict[str, Any]:
        schema = original_openapi()
        problem_schema = {"$ref": "#/components/schemas/ProblemDetails"}
        for path_item in schema.get("paths", {}).values():
            for method, operation in path_item.items():
                if method not in _METHODS:
                    continue
                responses = operation.setdefault("responses", {})
                responses.pop("422", None)
                for status, description in (
                    ("4XX", "Client error expressed as RFC 9457 problem details."),
                    ("5XX", "Server error expressed as RFC 9457 problem details."),
                ):
                    responses[status] = {
                        "description": description,
                        "content": {
                            PROBLEM_MEDIA_TYPE: {
                                "schema": problem_schema,
                            }
                        },
                    }
        return schema

    setattr(app, "openapi", problem_openapi)
    app.openapi_schema = None


def install_problem_details(app: FastAPI) -> None:
    """Install problem handling after all other user middleware registrations.

    Starlette prepends middleware as it is registered. Calling this installer last
    keeps correlation outermost, so it can serialize failures from other middleware.
    """

    if getattr(app.state, "problem_details_installed", False):
        return

    install_correlation_logging()
    app.add_middleware(CorrelationIdMiddleware)
    for exception_type in (ProblemError, RequestValidationError, HTTPException, Exception):
        app.add_exception_handler(exception_type, problem_exception_handler)
    _install_openapi_problem_responses(app)
    app.state.problem_details_installed = True
