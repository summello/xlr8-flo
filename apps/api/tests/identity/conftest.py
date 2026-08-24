from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import cast

import pytest
from psycopg.errors import UniqueViolation

from flo.kernel.config import Settings
from flo.kernel.identity import IdentityConnection, IdentityId, build_local_identity_provider
from flo.kernel.identity.port import IdentityProvider


@dataclass
class FakeResult:
    row: tuple[object, ...] | None = None
    rowcount: int = 0

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class FakeIdentityConnection:
    def __init__(self) -> None:
        self.identities: dict[str, tuple[IdentityId, str]] = {}

    def transaction(self) -> nullcontext[None]:
        return nullcontext()

    def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> FakeResult:
        if query.startswith("SELECT"):
            stored = self.identities.get(cast(str, params[0]))
            return FakeResult(None if stored is None else (stored[0], stored[1]))
        if query.startswith("INSERT"):
            identity_id, email, password_hash = cast(tuple[IdentityId, str, str], params)
            if email in self.identities:
                raise UniqueViolation
            self.identities[email] = (identity_id, password_hash)
            return FakeResult(rowcount=1)
        if "AND password_hash" in query:
            replacement, identity_id, previous = cast(
                tuple[str, IdentityId, str], params
            )
            for email, stored in self.identities.items():
                if stored == (identity_id, previous):
                    self.identities[email] = (identity_id, replacement)
                    return FakeResult(rowcount=1)
            return FakeResult()
        if query.startswith("UPDATE"):
            replacement, identity_id = cast(tuple[str, IdentityId], params)
            for email, stored in self.identities.items():
                if stored[0] == identity_id:
                    self.identities[email] = (identity_id, replacement)
                    return FakeResult(rowcount=1)
            return FakeResult()
        raise AssertionError(f"unexpected SQL operation: {query.split()[0]}")


def fast_settings(*, time_cost: int = 1) -> Settings:
    return Settings(
        identity_argon2_time_cost=time_cost,
        identity_argon2_memory_cost_kib=8 * 1024,
        identity_argon2_parallelism=1,
    )


@pytest.fixture
def identity_connection() -> FakeIdentityConnection:
    return FakeIdentityConnection()


@pytest.fixture
def identity_provider(identity_connection: FakeIdentityConnection) -> IdentityProvider:
    connection: IdentityConnection = identity_connection
    return build_local_identity_provider(connection, fast_settings())
