"""Operator-only, atomic bootstrap of a tenant and its MFA-pending administrator."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.errors import UniqueViolation

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.config import Settings
from flo.kernel.identity import (
    IdentityAlreadyExistsError,
    IdentityConnection,
    PasswordPolicyError,
    build_local_identity_provider,
)
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.identity.service import (
    AuthorizationTarget,
    IdentityAuthorizationConnection,
    IdentityAuthorizationService,
    ScopeType,
    add_identity_membership,
    identity_organization,
    role_code,
)
from flo.modules.org.service import create_organization


class BootstrapConflict(Exception):
    """The code or administrator identity is already assigned elsewhere."""


@dataclass(frozen=True)
class BootstrapResult:
    org_id: UUID
    admin_email: str
    created: bool


def _existing(
    conn: psycopg.Connection[tuple[object, ...]], code: str, email: str
) -> BootstrapResult | None:
    row = conn.execute("SELECT org_id FROM organization_code WHERE code = %s", (code,)).fetchone()
    if row is None:
        return None
    org_id = cast(UUID, row[0])
    if identity_organization(cast(IdentityAuthorizationConnection, conn), email) != org_id:
        raise BootstrapConflict
    return BootstrapResult(org_id, email, False)


async def bootstrap(
    conn: psycopg.Connection[tuple[object, ...]],
    *,
    org_name: str,
    org_code: str,
    admin_email: str,
    password: str,
    settings: Settings,
    base_currency: str | None = None,
) -> BootstrapResult:
    """Commit all bootstrap state together; the code primary key arbitrates races."""
    code = org_code.upper()
    email = admin_email.casefold()
    if re.fullmatch(r"[A-Z][A-Z0-9_-]{1,31}", code) is None:
        raise ValueError(
            "Organization code must be 2–32 letters, digits, underscores or hyphens, "
            "starting with a letter."
        )
    try:
        with conn.transaction(), correlation_context(str(uuid4())):
            existing = _existing(conn, code, email)
            if existing is not None:
                return existing
            org_id = create_organization(conn, name=org_name, base_currency=base_currency)
            # Insert before hashing/grants: a concurrent loser waits on this key,
            # then rolls its organization back and resolves the winner.
            conn.execute(
                "INSERT INTO organization_code (code, org_id) VALUES (%s, %s)", (code, org_id)
            )
            provider = await build_local_identity_provider(cast(IdentityConnection, conn), settings)
            admin_id = await provider.create_identity(email, password)
            service_conn = cast(IdentityAuthorizationConnection, conn)
            add_identity_membership(service_conn, admin_id, org_id)
            scope = Scope(org_id)
            with tenant_transaction(cast(RlsSession, conn), scope):
                service = IdentityAuthorizationService(service_conn, scope, admin_id)
                roles = service.seed_baseline_roles()
                administrator = roles[role_code("organization-administrator")]
                # Database registry includes permissions added after the baseline.
                permissions = conn.execute("SELECT code FROM permission ORDER BY code").fetchall()
                for permission in permissions:
                    service.grant_permission(administrator.id, cast(str, permission[0]))
                service.grant_role(
                    admin_id, administrator.id, AuthorizationTarget(ScopeType.ORG, org_id)
                )
                AuditWriter(cast(AuditConnection, conn), scope).write(
                    actor=AuditActor(ActorKind.SYSTEM),
                    action="org.bootstrap",
                    target_type="organization",
                    target_id=org_id,
                    outcome=Outcome.SUCCESS,
                    after_source={"admin_email": email, "code": code},
                    after_fields=("admin_email", "code"),
                )
            return BootstrapResult(org_id, email, True)
    except UniqueViolation:
        with conn.transaction():
            existing = _existing(conn, code, email)
            if existing is not None:
                return existing
        raise BootstrapConflict from None
    except IdentityAlreadyExistsError:
        raise BootstrapConflict from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-name", required=True)
    parser.add_argument("--org-code", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--base-currency")
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        if settings.database_url is None:
            raise ValueError("Configure DATABASE_URL before running bootstrap.")
        password = os.environ.get("FLO_BOOTSTRAP_PASSWORD")
        if password is None:
            password = getpass.getpass("Initial administrator password: ")
        with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
            result = asyncio.run(
                bootstrap(
                    conn,
                    org_name=args.org_name,
                    org_code=args.org_code,
                    admin_email=args.admin_email,
                    password=password,
                    settings=settings,
                    base_currency=args.base_currency,
                )
            )
        if not result.created:
            print("already bootstrapped")
        print(
            f"Organization: {result.org_id}\nAdministrator: {result.admin_email}\n"
            "Next: sign in and enroll MFA."
        )
        return 0
    except PasswordPolicyError:
        print(
            "Initial password does not meet NIST policy; "
            "choose a longer, uncompromised passphrase.",
            file=sys.stderr,
        )
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except BootstrapConflict:
        print(
            "Organization code or administrator is already assigned; "
            "use the original administrator or another code.",
            file=sys.stderr,
        )
        return 3
    except psycopg.Error:
        print(
            "Bootstrap database operation failed; no bootstrap changes were committed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
