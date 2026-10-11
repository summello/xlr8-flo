"""Locked invitation transitions, tenant-scoped data, and global hash lookup."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.errors import UniqueViolation

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.config import Settings
from flo.kernel.db.repo import ScopedRepo
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.identity import IdentityId, IdentityProvider, PasswordPolicyError
from flo.kernel.identity.port import IdentityAlreadyExistsError
from flo.kernel.outbox.store import OutboxConnection, OutboxStore
from flo.kernel.session.store import RequestDevice, SessionRecord
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.identity.schemas import (
    InvitationAccept,
    InvitationCreate,
    InvitationExpired,
    InvitationRecord,
    InvitationValid,
)
from flo.modules.identity.service import (
    IdentityAuthorizationConnection,
    IdentityAuthorizationService,
    add_identity_membership,
)

_COLUMNS = tuple(InvitationRecord.model_fields)
_SELECT = ", ".join(_COLUMNS)


def unavailable() -> ProblemError:
    return ProblemError(ErrorCode.NOT_FOUND, headers={"Cache-Control": "no-store"})


def _closed() -> ProblemError:
    return ProblemError(
        ErrorCode.CONFLICT,
        checks={"problem": "invitation_closed"},
        detail="This invitation is closed. Issue a new invitation if needed.",
    )


def _validation(field: str) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        errors=(
            ProblemFieldError(field=field, message="Choose a valid value for this organization."),
        ),
    )


def token_hash(token: str) -> str:
    return sha256(token.encode(), usedforsecurity=True).hexdigest()


class InvitationRepository(ScopedRepo[InvitationRecord]):
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(cast(IdentityAuthorizationConnection, connection), scope)
        self.connection = connection

    def get(self, invitation_id: UUID, *, lock: bool = False) -> InvitationRecord:
        row = self.connection.execute(
            f"SELECT {_SELECT} FROM invitation WHERE org_id = %(org_id)s AND id = %(id)s"
            + (" FOR UPDATE" if lock else ""),
            self.scoped_params({"id": invitation_id}),
        ).fetchone()
        if row is None:
            raise unavailable()
        return InvitationRecord.model_validate(dict(zip(_COLUMNS, row, strict=True)))

    def list(self) -> list[InvitationRecord]:
        rows = self.connection.execute(
            f"SELECT {_SELECT} FROM invitation WHERE org_id = %(org_id)s ORDER BY created_at, id",
            self.scoped_params({}),
        ).fetchall()
        return [
            InvitationRecord.model_validate(dict(zip(_COLUMNS, row, strict=True))) for row in rows
        ]

    def open_for_email(self, email: str) -> InvitationRecord | None:
        row = self.connection.execute(
            "SELECT id FROM invitation WHERE org_id = %(org_id)s AND lower(email) = %(email)s "
            "AND used_at IS NULL AND withdrawn_at IS NULL FOR UPDATE",
            self.scoped_params({"email": email.casefold()}),
        ).fetchone()
        return None if row is None else self.get(cast(UUID, row[0]), lock=True)

    def insert(
        self, body: InvitationCreate, actor: IdentityId, now: datetime, expires_at: datetime
    ) -> InvitationRecord:
        invitation_id = uuid4()
        self.connection.execute(
            "INSERT INTO invitation(id,org_id,email,full_name,role_code,scope_type,scope_id,"
            "invited_by,created_at,sent_at,expires_at) VALUES "
            "(%(id)s,%(org_id)s,%(email)s,%(full_name)s,%(role_code)s,%(scope_type)s,"
            "%(scope_id)s,%(actor)s,%(now)s,%(now)s,%(expires_at)s)",
            self.scoped_params(
                {
                    "id": invitation_id,
                    "email": body.email.casefold(),
                    "full_name": body.full_name,
                    "role_code": body.role_code,
                    "scope_type": body.scope_type.value,
                    "scope_id": self.scope.org_id
                    if body.scope_type is ScopeType.ORG
                    else body.scope_id,
                    "actor": actor,
                    "now": now,
                    "expires_at": expires_at,
                }
            ),
        )
        return self.get(invitation_id)

    def mark_withdrawn(self, invitation_id: UUID, actor: IdentityId, now: datetime) -> None:
        self.connection.execute(
            "UPDATE invitation SET withdrawn_at = %(now)s, withdrawn_by = %(actor)s "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            self.scoped_params({"id": invitation_id, "actor": actor, "now": now}),
        )

    def renew(self, invitation_id: UUID, now: datetime, expires_at: datetime) -> InvitationRecord:
        self.connection.execute(
            "UPDATE invitation SET sent_at = %(now)s, expires_at = %(expires_at)s, "
            "resend_counter = resend_counter + 1 WHERE org_id = %(org_id)s AND id = %(id)s",
            self.scoped_params({"id": invitation_id, "now": now, "expires_at": expires_at}),
        )
        return self.get(invitation_id)

    def mark_used(
        self, invitation_id: UUID, identity: IdentityId, now: datetime, full_name: str | None
    ) -> InvitationRecord:
        self.connection.execute(
            "UPDATE invitation SET used_at = %(now)s, used_by = %(identity)s, "
            "full_name = COALESCE(%(full_name)s, full_name) "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            self.scoped_params(
                {"id": invitation_id, "identity": identity, "now": now, "full_name": full_name}
            ),
        )
        return self.get(invitation_id)

    def role_scope(
        self, code: str, kind: ScopeType, scope_id: UUID | None
    ) -> tuple[UUID, str, str]:
        row = self.connection.execute(
            "SELECT id, name FROM role WHERE org_id = %(org_id)s AND code = %(code)s",
            self.scoped_params({"code": code}),
        ).fetchone()
        if row is None:
            raise _validation("role_code")
        if kind is ScopeType.ORG:
            if scope_id is not None and scope_id != self.scope.org_id:
                raise _validation("scope_id")
            words = "all business units"
        else:
            contained = self.connection.execute(
                "SELECT 1 FROM authorization_scope WHERE org_id = %(org_id)s "
                "AND scope_type = %(kind)s AND scope_id = %(id)s",
                self.scoped_params({"kind": kind.value, "id": scope_id}),
            ).fetchone()
            if contained is None:
                raise _validation("scope_id")
            # Scope display data remains tenant-scoped and parameterized.
            table = "org_unit" if kind is ScopeType.BU else "project"
            named = self.connection.execute(
                f"SELECT name FROM {table} WHERE org_id = %(org_id)s AND id = %(id)s",
                self.scoped_params({"id": scope_id}),
            ).fetchone()
            words = "business unit" if kind is ScopeType.BU else "project"
            if named is not None:
                words += ": " + cast(str, named[0])
        return cast(UUID, row[0]), cast(str, row[1]), words


class InvitationService:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        settings: Settings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.connection = connection
        self.settings = settings
        self.clock = clock

    def lookup(self, token: str) -> tuple[UUID, Scope, str]:
        # Malformed and unknown tokens share exactly the same primary-key lookup.
        digest = token_hash(token)
        row = self.connection.execute(
            "SELECT invitation_id, org_id FROM invitation_token WHERE token_hash = %s",
            (digest,),
        ).fetchone()
        if row is None:
            raise unavailable()
        return cast(UUID, row[0]), Scope(cast(UUID, row[1])), digest

    def _outbox(self, scope: Scope) -> OutboxStore:
        return OutboxStore(cast(OutboxConnection, self.connection), scope)

    def _discard(self, row: InvitationRecord, scope: Scope) -> None:
        self.connection.execute("DELETE FROM invitation_token WHERE invitation_id = %s", (row.id,))
        self._outbox(scope).discard_email(f"invitation:{row.id}:{row.resend_counter}")

    def _audit(
        self,
        row: InvitationRecord,
        scope: Scope,
        actor: IdentityId,
        action: str,
        device: RequestDevice,
    ) -> None:
        AuditWriter(cast(AuditConnection, self.connection), scope).write(
            actor=AuditActor(ActorKind.USER, actor),
            action="invitation." + action,
            target_type="invitation",
            target_id=row.id,
            outcome=Outcome.SUCCESS,
            after_source=row,
            after_fields=(
                "id",
                "org_id",
                "role_code",
                "scope_type",
                "scope_id",
                "invited_by",
                "used_at",
                "withdrawn_at",
            ),
            ip_address_value=device.ip_prefix.split("/")[0] if device.ip_prefix else None,
            user_agent=device.user_agent,
        )

    def _details(self, row: InvitationRecord, scope: Scope) -> InvitationValid:
        _, role_name, words = InvitationRepository(self.connection, scope).role_scope(
            row.role_code, row.scope_type, row.scope_id
        )
        organization = self.connection.execute(
            "SELECT display_name, tenant_label FROM organization_code WHERE org_id = %s",
            (scope.org_id,),
        ).fetchone()
        inviter = self.connection.execute(
            "SELECT email FROM identity WHERE id = %s", (row.invited_by,)
        ).fetchone()
        assert organization is not None and inviter is not None
        return InvitationValid(
            org_name=cast(str, organization[0]),
            tenant_label=cast(str | None, organization[1]),
            inviter_name=cast(str, inviter[0]),
            inviter_title=None,
            role_name=role_name,
            access_words=words,
            expires_at=row.expires_at,
            email=row.email,
            full_name=row.full_name,
        )

    def _send(self, row: InvitationRecord, scope: Scope) -> None:
        token = secrets.token_urlsafe(32)
        self.connection.execute(
            "INSERT INTO invitation_token(token_hash, invitation_id, org_id) VALUES (%s,%s,%s)",
            (token_hash(token), row.id, scope.org_id),
        )
        details = self._details(row, scope)
        # ponytail: never-delivered links retain their payload until discarded;
        # tokens expire after the configured TTL (7 days by default). Add scheduled
        # expired-invitation cleanup if volume grows.
        self._outbox(scope).add_email(
            to=row.email,
            template="invitation-v1",
            context={
                **details.model_dump(mode="json"),
                "link": "https://xlr8flo.summello.com/invite/" + token,
            },
            idempotency_key=f"invitation:{row.id}:{row.resend_counter}",
        )

    def issue(
        self, scope: Scope, actor: IdentityId, body: InvitationCreate, device: RequestDevice
    ) -> InvitationRecord:
        try:
            with tenant_transaction(cast(RlsSession, self.connection), scope):
                repo = InvitationRepository(self.connection, scope)
                repo.role_scope(body.role_code, body.scope_type, body.scope_id)
                now = self.clock()
                old = repo.open_for_email(body.email)
                if old is not None:
                    if old.expires_at > now:
                        raise ProblemError(
                            ErrorCode.CONFLICT,
                            checks={"problem": "invitation_open"},
                            detail="An invitation is already open. Resend or withdraw it first.",
                        )
                    self._withdraw(old, scope, actor, device)
                row = repo.insert(
                    body, actor, now, now + timedelta(days=self.settings.invitation_ttl_days)
                )
                self._send(row, scope)
                self._audit(row, scope, actor, "issued", device)
                return row
        except UniqueViolation as exc:
            raise ProblemError(
                ErrorCode.CONFLICT,
                checks={"problem": "invitation_open"},
                detail="An invitation is already open. Resend or withdraw it first.",
            ) from exc

    def list(self, scope: Scope) -> list[InvitationRecord]:
        with tenant_transaction(cast(RlsSession, self.connection), scope):
            return InvitationRepository(self.connection, scope).list()

    def _withdraw(
        self, row: InvitationRecord, scope: Scope, actor: IdentityId, device: RequestDevice
    ) -> None:
        InvitationRepository(self.connection, scope).mark_withdrawn(row.id, actor, self.clock())
        self._discard(row, scope)
        updated = InvitationRepository(self.connection, scope).get(row.id)
        self._audit(updated, scope, actor, "withdrawn", device)

    def withdraw(
        self, scope: Scope, actor: IdentityId, invitation_id: UUID, device: RequestDevice
    ) -> None:
        with tenant_transaction(cast(RlsSession, self.connection), scope):
            row = InvitationRepository(self.connection, scope).get(invitation_id, lock=True)
            if row.used_at is not None:
                raise _closed()
            if row.withdrawn_at is None:
                self._withdraw(row, scope, actor, device)

    def resend(
        self, scope: Scope, actor: IdentityId, invitation_id: UUID, device: RequestDevice
    ) -> InvitationRecord:
        with tenant_transaction(cast(RlsSession, self.connection), scope):
            repo = InvitationRepository(self.connection, scope)
            row = repo.get(invitation_id, lock=True)
            if row.used_at is not None or row.withdrawn_at is not None:
                raise _closed()
            self._discard(row, scope)
            now = self.clock()
            row = repo.renew(row.id, now, now + timedelta(days=self.settings.invitation_ttl_days))
            self._send(row, scope)
            self._audit(row, scope, actor, "resent", device)
            return row

    def by_token(self, token: str) -> InvitationValid | InvitationExpired:
        invitation_id, scope, digest = self.lookup(token)
        with tenant_transaction(cast(RlsSession, self.connection), scope):
            row = InvitationRepository(self.connection, scope).get(invitation_id, lock=True)
            self._check_live(row, digest)
            if row.expires_at <= self.clock():
                return InvitationExpired(lifetime_days=self.settings.invitation_ttl_days)
            return self._details(row, scope)

    def _check_live(self, row: InvitationRecord, digest: str) -> None:
        current = self.connection.execute(
            "SELECT 1 FROM invitation_token WHERE invitation_id = %s AND token_hash = %s",
            (row.id, digest),
        ).fetchone()
        if row.used_at is not None or row.withdrawn_at is not None or current is None:
            raise unavailable()

    async def accept(
        self,
        token: str,
        body: InvitationAccept,
        session: SessionRecord | None,
        provider: IdentityProvider,
        device: RequestDevice,
    ) -> IdentityId | None:
        invitation_id, scope, digest = self.lookup(token)
        try:
            with tenant_transaction(cast(RlsSession, self.connection), scope):
                repo = InvitationRepository(self.connection, scope)
                row = await asyncio.to_thread(repo.get, invitation_id, lock=True)
                self._check_live(row, digest)
                if row.expires_at <= self.clock():
                    raise unavailable()
                if session is not None:
                    identity = session.identity_id
                    email = self.connection.execute(
                        "SELECT email FROM identity WHERE id = %s", (identity,)
                    ).fetchone()
                    member = self.connection.execute(
                        "SELECT 1 FROM identity_membership WHERE identity_id = %s AND org_id = %s",
                        (identity, scope.org_id),
                    ).fetchone()
                    if email is None or cast(str, email[0]).casefold() != row.email or member:
                        raise unavailable()
                else:
                    existing = self.connection.execute(
                        "SELECT 1 FROM identity WHERE lower(email) = %s", (row.email,)
                    ).fetchone()
                    if existing is not None:
                        raise unavailable()
                    if not body.full_name or not body.accepted_terms:
                        raise _validation(
                            "accepted_terms" if not body.accepted_terms else "full_name"
                        )
                    if body.password is None:
                        raise _validation("password")
                    identity = await provider.create_identity(row.email, body.password)
                add_identity_membership(
                    cast(IdentityAuthorizationConnection, self.connection), identity, scope.org_id
                )
                role_id, _, _ = repo.role_scope(row.role_code, row.scope_type, row.scope_id)
                IdentityAuthorizationService(
                    cast(IdentityAuthorizationConnection, self.connection),
                    scope,
                    IdentityId(row.invited_by),
                ).grant_role(
                    identity,
                    role_id,
                    AuthorizationTarget(row.scope_type, row.scope_id or scope.org_id),
                )
                accepted = repo.mark_used(row.id, identity, self.clock(), body.full_name)
                self._discard(row, scope)
                self._audit(accepted, scope, identity, "accepted", device)
                return identity if session is None else None
        except (IdentityAlreadyExistsError, UniqueViolation) as exc:
            raise unavailable() from exc
        except PasswordPolicyError as exc:
            raise ProblemError(
                ErrorCode.VALIDATION_FAILED,
                errors=(
                    ProblemFieldError(
                        field="password", message="The password does not meet the password policy."
                    ),
                ),
            ) from exc
