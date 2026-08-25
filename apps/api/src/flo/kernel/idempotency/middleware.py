"""ASGI middleware that makes tenant POST commands safely retryable."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from hashlib import sha256
from typing import cast

from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Send
from starlette.types import Scope as ASGIScope

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.idempotency.store import (
    ClaimKind,
    IdempotencyConnection,
    IdempotencyStore,
    JsonValue,
    StoredResponse,
)
from flo.kernel.tenancy.context import Scope, TenantScopeMissing, current_scope
from flo.kernel.tenancy.rls import tenant_transaction

IDEMPOTENCY_HEADER = "Idempotency-Key"
TRANSACTION_CONNECTION_STATE_KEY = "transaction_connection"
_REPLAYABLE_RESPONSE_HEADERS = ("location", "etag")
IDEMPOTENCY_KEY_EXEMPT_PATHS = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/refresh",
    }
)

_logger = logging.getLogger(__name__)

type ConnectionFactory = Callable[[], AbstractContextManager[IdempotencyConnection]]
type RequiredKeyPredicate = Callable[[ASGIScope], bool]


class _RollbackResponse(Exception):
    def __init__(self, messages: list[Message]) -> None:
        super().__init__("response requires transaction rollback")
        self.messages = messages


def request_hash(body: bytes) -> str:
    """Hash JSON after sorting keys and removing insignificant whitespace."""

    if not body:
        canonical = b""
    else:
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            canonical = body
        else:
            canonical = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
    return sha256(canonical).hexdigest()


def transaction_connection(request: Request) -> IdempotencyConnection:
    """Return the connection whose transaction owns the idempotent command."""

    connection = getattr(request.state, TRANSACTION_CONNECTION_STATE_KEY, None)
    if connection is None:
        raise RuntimeError("request has no active idempotency transaction")
    return cast(IdempotencyConnection, connection)


def _default_required_key(scope: ASGIScope) -> bool:
    path = str(scope.get("path", "")).rstrip("/")
    return path not in IDEMPOTENCY_KEY_EXEMPT_PATHS


def _active_tenant_scope() -> Scope | None:
    try:
        return current_scope()
    except TenantScopeMissing:
        return None


async def _body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    more_body = True
    while more_body:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunks.append(message.get("body", b""))
        more_body = message.get("more_body", False)
    return b"".join(chunks)


def _replay_receive(body: bytes) -> Receive:
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _response_parts(messages: list[Message]) -> tuple[Message, bytes]:
    starts = [message for message in messages if message["type"] == "http.response.start"]
    if len(starts) != 1:
        raise RuntimeError("idempotent endpoint did not produce exactly one response start")
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return starts[0], body


def _json_response(body: bytes, status_code: int) -> tuple[bytes, str]:
    if not body and status_code in {204, 205, 304}:
        return b"", "null"
    try:
        parsed = cast(JsonValue, json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("idempotent endpoint returned a non-JSON response") from exc
    serialized = json.dumps(
        parsed,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return serialized.encode(), serialized


def _with_body(start: Message, body: bytes) -> list[Message]:
    headers = [
        (name, value)
        for name, value in start.get("headers", [])
        if name.lower() != b"content-length"
    ]
    headers.append((b"content-length", str(len(body)).encode("ascii")))
    return [
        {**start, "headers": headers},
        {"type": "http.response.body", "body": body, "more_body": False},
    ]


def _replayable_headers(start: Message) -> dict[str, str]:
    selected: dict[str, str] = {}
    for raw_name, raw_value in start.get("headers", []):
        name = raw_name.decode("ascii").lower()
        if name in _REPLAYABLE_RESPONSE_HEADERS:
            selected[name] = raw_value.decode("latin-1")
    return selected


def _stored_messages(response: StoredResponse) -> list[Message]:
    if response.status_code in {204, 205, 304}:
        body = b""
        headers: list[tuple[bytes, bytes]] = []
    else:
        body = json.dumps(
            response.body,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        headers = [(b"content-type", b"application/json")]
    headers.extend(
        (name.encode("ascii"), response.headers[name].encode("latin-1"))
        for name in _REPLAYABLE_RESPONSE_HEADERS
        if name in response.headers
    )
    headers.append((b"content-length", str(len(body)).encode("ascii")))
    return [
        {
            "type": "http.response.start",
            "status": response.status_code,
            "headers": headers,
        },
        {"type": "http.response.body", "body": body, "more_body": False},
    ]


async def _send_messages(messages: list[Message], send: Send) -> None:
    for message in messages:
        await send(message)


class IdempotencyMiddleware:
    """Run a keyed tenant POST and its response record in one transaction."""

    def __init__(
        self,
        app: ASGIApp,
        connection_factory: ConnectionFactory,
        required_key: RequiredKeyPredicate = _default_required_key,
    ) -> None:
        self._app = app
        self._connection_factory = connection_factory
        self._required_key = required_key

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        key = (headers.get(IDEMPOTENCY_HEADER) or "").strip()
        if not key:
            if self._required_key(scope):
                raise ProblemError(
                    ErrorCode.BAD_REQUEST,
                    detail=f"{IDEMPOTENCY_HEADER} is required for this operation.",
                    errors=(
                        ProblemFieldError(
                            field=IDEMPOTENCY_HEADER,
                            message="This header is required.",
                        ),
                    ),
                )
            await self._app(scope, receive, send)
            return

        tenant_scope = _active_tenant_scope()
        if tenant_scope is None:
            _logger.warning(
                "Idempotency skipped because tenant scope is missing for %s",
                scope.get("path", ""),
            )
            await self._app(scope, receive, send)
            return

        request_body = await _body(receive)
        body_hash = request_hash(request_body)
        endpoint = str(scope.get("path", ""))
        outgoing: list[Message]
        with self._connection_factory() as connection:
            try:
                with tenant_transaction(connection, tenant_scope):
                    state = cast(dict[str, object], scope.setdefault("state", {}))
                    state[TRANSACTION_CONNECTION_STATE_KEY] = connection
                    store = IdempotencyStore(connection, tenant_scope)
                    claim = store.claim(key, endpoint, body_hash)
                    if claim.kind == ClaimKind.IN_PROGRESS:
                        raise ProblemError(
                            ErrorCode.REQUEST_IN_FLIGHT,
                            headers={"Retry-After": "1"},
                        )
                    if claim.kind == ClaimKind.REUSED:
                        raise ProblemError(ErrorCode.IDEMPOTENCY_KEY_REUSED)
                    if claim.kind == ClaimKind.COMPLETED:
                        if claim.response is None:
                            raise RuntimeError("completed claim has no stored response")
                        outgoing = _stored_messages(claim.response)
                    else:
                        buffered: list[Message] = []

                        async def buffer(message: Message) -> None:
                            buffered.append(message)

                        await self._app(scope, _replay_receive(request_body), buffer)
                        start, raw_body = _response_parts(buffered)
                        status_code = cast(int, start["status"])
                        if status_code >= 400:
                            raise _RollbackResponse(buffered)
                        wire_body, serialized = _json_response(raw_body, status_code)
                        store.complete(
                            key,
                            status_code=status_code,
                            serialized_response_body=serialized,
                            response_headers=_replayable_headers(start),
                        )
                        outgoing = _with_body(start, wire_body)
            except _RollbackResponse as response:
                outgoing = response.messages

        await _send_messages(outgoing, send)


def install_idempotency(
    app: FastAPI,
    connection_factory: ConnectionFactory,
    required_key: RequiredKeyPredicate = _default_required_key,
) -> None:
    """Install before tenancy and problem-details middleware are installed."""

    app.add_middleware(
        IdempotencyMiddleware,
        connection_factory=connection_factory,
        required_key=required_key,
    )
