"""Dev-only seed for the ACC-008 keyboard walkthrough (docs/ops/acc-008-walkthrough.md).

Composes existing services into one tenant: a unit with two addresses, master codes, five people
with role grants, five published workflows and one approved vendor. Never runs against a remote
database, a real mail provider or real object storage; the guard reads the raw environment first.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from pydantic import JsonValue

from flo.api.document_types import register_document_types
from flo.kernel.config import Settings
from flo.kernel.identity import (
    IdentityConnection,
    IdentityId,
    PasswordPolicyError,
    build_local_identity_provider,
)
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.schemas import DecisionWrite, WorkflowCreate, WorkflowVersionWrite
from flo.modules.approvals.service import WorkflowService, active_instance, decide
from flo.modules.identity.service import (
    AuthorizationTarget,
    IdentityAuthorizationConnection,
    IdentityAuthorizationService,
    ScopeType,
    add_identity_membership,
)
from flo.modules.org.bootstrap import bootstrap
from flo.modules.org.schemas import MasterCreate, OrgAddressCreate, OrgUnitCreate
from flo.modules.org.service import OrgService
from flo.modules.vendors.schemas import ContactCreate, SubmitCreate, VendorCreate
from flo.modules.vendors.service import VendorService

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
START = date(2020, 1, 1)
ADMIN_EMAIL = "admin@example.com"

# person key, email, baseline roles on the unit. Exactly these grants; none needs MFA.
PEOPLE: dict[str, tuple[str, tuple[str, ...]]] = {
    "requester": ("requester@example.com", ("project-manager",)),
    "approver": (
        "approver@example.com",
        ("project-administrator", "approver", "vendor-manager"),
    ),
    "buyer": ("buyer@example.com", ("sourcing-specialist", "purchasing-user")),
    "sourcing_approver": ("sourcing.approver@example.com", ("sourcing-specialist",)),
    "po_approver": ("po.approver@example.com", ("purchasing-user",)),
}
# document type -> the person whose approval step it names
WORKFLOWS = {
    "project": "approver",
    "requisition": "approver",
    "vendor": "approver",
    "award": "sourcing_approver",
    "po": "po_approver",
}


class AlreadySeeded(Exception):
    """The organization code is already taken; the seed never edits an existing tenant."""


def refuse_reason(env: Mapping[str, str]) -> str | None:
    """Why this environment is not a local dev stack, or None. Reads names Settings reads."""
    url = env.get("DATABASE_URL")
    if not url:
        return "refused: DATABASE_URL is not set; point it at the local stack (flo up)."
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    if host not in LOCAL_HOSTS:
        return "refused: DATABASE_URL host is not localhost; this seed only runs locally."
    if env.get("EMAIL_PROVIDER") != "smtp":
        return "refused: EMAIL_PROVIDER must be smtp (Mailpit); this seed never sends real mail."
    if env.get("STORAGE_PROVIDER") != "local":
        return "refused: STORAGE_PROVIDER must be local; this seed never touches real storage."
    return None


def _approve(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, kind: str, doc: UUID, by: UUID
) -> None:
    instance = active_instance(conn, scope, kind, doc)
    assert instance is not None
    decide(
        conn,
        scope,
        task_id=instance.tasks[-1].id,
        actor_id=by,
        body=DecisionWrite(outcome="approve", document_version=1),
    )


def seed(
    conn: psycopg.Connection[tuple[object, ...]],
    settings: Settings,
    *,
    password: str,
    org_code: str = "WALK",
) -> dict[str, object]:
    """Create the walkthrough tenant; raises AlreadySeeded if the code exists."""
    if conn.execute(
        "SELECT 1 FROM organization_code WHERE code = %s", (org_code.upper(),)
    ).fetchone():
        raise AlreadySeeded
    register_document_types()  # workflow validation needs every adapter; health.py does it in prod
    # Not one transaction: numbering allocates on its own connection and must see the committed org.
    # A failed run leaves a partial tenant; reset the database (the refusal message says so).
    with correlation_context(str(uuid4())):
        org_id = asyncio.run(
            bootstrap(
                conn,
                org_name="Walkthrough Co",
                org_code=org_code,
                admin_email=ADMIN_EMAIL,
                password=password,
                settings=settings,
                base_currency="USD",
            )
        ).org_id
        scope = Scope(org_id)
        admin = cast(
            UUID,
            conn.execute("SELECT id FROM identity WHERE email = %s", (ADMIN_EMAIL,)).fetchone()[0],  # type: ignore[index]
        )
        org = OrgService(conn, scope, admin)
        unit = org.create_unit(OrgUnitCreate(code="WALK-OPS", name="Operations", kind="bu"))
        address_kinds: tuple[tuple[Literal["bill_to", "ship_to"], str], ...] = (
            ("bill_to", "100 Main Street"),
            ("ship_to", "1 Harbor Road"),
        )
        for address_kind, line1 in address_kinds:
            org.create_address(
                unit.id,
                OrgAddressCreate(
                    kind=address_kind,
                    line1=line1,
                    city="Springfield",
                    country="US",
                    effective_from=START,
                ),
            )
        master_rows: tuple[tuple[str, str, str, dict[str, JsonValue]], ...] = (
            ("department", "OPS", "Operations", {}),
            ("ledger_account", "6100", "Maintenance spares", {"account_type": "expense"}),
            ("uom", "EA", "Each", {"dimension": "count"}),
        )
        for kind, code, name, attributes in master_rows:
            org.create_master(
                kind,
                MasterCreate(code=code, name=name, attributes=attributes, effective_from=START),
            )
        provider = asyncio.run(
            build_local_identity_provider(cast(IdentityConnection, conn), settings)
        )
        people: dict[str, UUID] = {}
        with tenant_transaction(cast(RlsSession, conn), scope):
            access = IdentityAuthorizationService(
                cast(IdentityAuthorizationConnection, conn), scope, IdentityId(admin)
            )
            for key, (email, roles) in PEOPLE.items():
                identity = asyncio.run(provider.create_identity(email, password))
                add_identity_membership(
                    cast(IdentityAuthorizationConnection, conn), identity, org_id
                )
                people[key] = cast(UUID, identity)
                for role in roles:
                    role_id = cast(
                        UUID,
                        conn.execute(
                            "SELECT id FROM role WHERE org_id = %s AND code = %s", (org_id, role)
                        ).fetchone()[0],  # type: ignore[index]
                    )
                    access.grant_role(identity, role_id, AuthorizationTarget(ScopeType.BU, unit.id))
        # Publish only after the grants exist: publish checks each approver against them.
        workflows = WorkflowService(conn, scope, admin)
        for document_type, who in WORKFLOWS.items():
            definition = workflows.create(
                WorkflowCreate(
                    document_type=document_type,  # type: ignore[arg-type]
                    name=f"Walkthrough {document_type}",
                    scope_type="bu",
                    scope_id=unit.id,  # the unit's UUID, never its code
                )
            )
            workflows.create_version(
                definition.id,
                WorkflowVersionWrite(
                    graph={  # type: ignore[arg-type]
                        "nodes": [
                            {"id": "start", "kind": "start"},
                            {
                                "id": "one",
                                "kind": "approval",
                                "name": "Approval",
                                "approvers": {"type": "users", "user_ids": [str(people[who])]},
                                "mode": "any",
                            },
                            {"id": "end", "kind": "end"},
                        ],
                        "edges": [{"from": "start", "to": "one"}, {"from": "one", "to": "end"}],
                    }
                ),
            )
            workflows.publish(definition.id, 1, True)
        # Restricted to the unit: a global vendor resolves its approval at organization scope,
        # where the Approver's unit-scoped grant does not reach.
        vendors = VendorService(conn, scope, admin, settings)
        vendor = vendors.create(
            VendorCreate(
                trading_name="Acme Pumps",
                legal_name="Acme Pumps LLC",
                availability="restricted",
                unit_ids=[unit.id],
            )
        )
        for contact_type, name, email in (
            ("billing", "Acme Billing", "billing@example.com"),
            ("service_delivery", "Acme Orders", "vendor@example.com"),
        ):
            vendors.create_contact(
                vendor.id, ContactCreate(type=contact_type, name=name, email=email)
            )
        change = vendors.submit(vendor.id, SubmitCreate())
        _approve(conn, scope, "vendor", change.id, people["approver"])
    return {"org_id": org_id, "unit_id": unit.id, "vendor_id": vendor.id, "people": people}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-code", default="WALK")
    args = parser.parse_args(argv)
    # Before Settings(): its own validation would raise a long error on some remote URLs.
    if (reason := refuse_reason(os.environ)) is not None:
        print(reason, file=sys.stderr)
        return 2
    try:
        settings = Settings()
        password = os.environ.get("FLO_WALKTHROUGH_PASSWORD") or getpass.getpass(
            "Password for the five walkthrough people: "
        )
        assert settings.database_url is not None
        with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
            seed(conn, settings, password=password, org_code=args.org_code)
    except AlreadySeeded:
        print("already seeded: use another --org-code or reset the database", file=sys.stderr)
        return 3
    except PasswordPolicyError:
        print("Password does not meet NIST policy; choose a longer passphrase.", file=sys.stderr)
        return 2
    print(f"Organization code: {args.org_code.upper()}")
    print("People (one shared password, never printed):")
    for email, _ in PEOPLE.values():
        print(f"  {email}")
    print("Mailpit: http://localhost:8025")
    print("Bid evidence file: docs/ops/acc-008-bid-evidence.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
