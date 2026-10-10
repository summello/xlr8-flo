"""Shared production authorization composition for requests and import jobs."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import psycopg

from flo.api.auth import _database_url
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.identity.resolver import AuthorizationConnection, AuthorizationResolver


@contextmanager
def production_authorization_resolver(scope: Scope) -> Iterator[AuthorizationResolver]:
    """Open one uncached, tenant-scoped resolver for one authorization check."""

    settings = Settings()
    try:
        connection = psycopg.connect(
            _database_url(settings),
            autocommit=False,
        )
    except psycopg.Error as exc:
        raise ProblemError(ErrorCode.SERVICE_UNAVAILABLE) from exc
    try:
        with tenant_transaction(cast(RlsSession, connection), scope):
            yield AuthorizationResolver(cast(AuthorizationConnection, connection), scope)
    finally:
        connection.close()
