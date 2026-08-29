"""Tenant-scoped explanations for a user's effective permissions."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from psycopg import sql

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.db.repo import ScopedRepo
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import PermissionCode, RoleCode, ScopeType

type DatabaseRow = Sequence[object] | Mapping[str, object]


class QueryResult(Protocol):
    """Result surface shared by psycopg and focused test doubles."""

    def fetchone(self) -> DatabaseRow | None: ...

    def fetchall(self) -> list[DatabaseRow]: ...


class EffectiveAccessConnection(AuditConnection, Protocol):
    """One scoped connection for explanation data and its audit evidence."""

    def execute(
        self,
        query: str | sql.Composed,
        params: Mapping[str, object] | None = None,
    ) -> QueryResult: ...


@dataclass(frozen=True, slots=True)
class AccessUser:
    """Identity facts retained and displayed after deactivation."""

    id: IdentityId
    email: str
    status: str

    @property
    def attribution(self) -> str:
        """Return a stable historical attribution label without anonymizing it."""

        marker = " (deactivated)" if self.status == "deactivated" else ""
        return f"{self.email}{marker}"


@dataclass(frozen=True, slots=True)
class AccessGrant:
    """One active or pending assignment and its complete provenance."""

    id: UUID
    role: RoleCode
    role_name: str
    scope_type: ScopeType
    scope_id: UUID
    scope_name: str
    granted_by: str | None
    granted_at: datetime
    effective_from: datetime | None


@dataclass(frozen=True, slots=True)
class PermissionSource:
    """The exact role and grant scope responsible for an allowed permission."""

    via_role: RoleCode
    via_scope_type: ScopeType
    via_scope_id: UUID
    inherited_from: str | None


@dataclass(frozen=True, slots=True)
class ExplainedPermission:
    """One allowed permission paired with one concrete source grant."""

    code: PermissionCode
    allowed: bool
    source: PermissionSource


@dataclass(frozen=True, slots=True)
class DeniedExample:
    """A catalog permission that has no currently effective source."""

    code: PermissionCode
    reason: str


@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    """Complete active, pending, allowed, and denied access explanation."""

    user: AccessUser
    grants: tuple[AccessGrant, ...]
    pending_grants: tuple[AccessGrant, ...]
    permissions: tuple[ExplainedPermission, ...]
    denied_examples: tuple[DeniedExample, ...]


@dataclass(frozen=True, slots=True)
class PermissionTableRow:
    """One server-filterable explorer row for an allowed or denied permission."""

    id: str
    code: PermissionCode
    access: str
    source: str
    explanation: str


@dataclass(frozen=True, slots=True)
class PermissionPage:
    """Cursor page consumed by the shared DataGrid."""

    last_cursor: str | None
    next_cursor: str | None
    previous_cursor: str | None
    rows: tuple[PermissionTableRow, ...]
    start_index: int
    total: int


def _value(row: DatabaseRow, index: int, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]


def _scope_name(scope_type: ScopeType, scope_id: UUID) -> str:
    if scope_type is ScopeType.ORG:
        return "Organization"
    label = "Business unit" if scope_type is ScopeType.BU else "Project"
    return f"{label} {scope_id}"


def _grant(row: DatabaseRow) -> AccessGrant:
    scope_type = ScopeType(cast(str, _value(row, 3, "scope_type")))
    scope_id = cast(UUID, _value(row, 4, "scope_id"))
    granted_by_id = cast(UUID | None, _value(row, 6, "granted_by"))
    granted_by_email = cast(str | None, _value(row, 7, "granted_by_email"))
    return AccessGrant(
        id=cast(UUID, _value(row, 0, "id")),
        role=RoleCode(cast(str, _value(row, 1, "role"))),
        role_name=cast(str, _value(row, 2, "role_name")),
        scope_type=scope_type,
        scope_id=scope_id,
        scope_name=_scope_name(scope_type, scope_id),
        granted_by=granted_by_email or (str(granted_by_id) if granted_by_id is not None else None),
        granted_at=cast(datetime, _value(row, 5, "granted_at")),
        effective_from=cast(datetime | None, _value(row, 8, "effective_from")),
    )


class EffectiveAccessRepository(ScopedRepo[EffectiveAccess]):
    """Read only identities and grants belonging to the active organization."""

    def __init__(self, connection: EffectiveAccessConnection, scope: Scope) -> None:
        super().__init__(connection, scope)
        self._connection = connection

    def user(self, user_id: IdentityId) -> AccessUser | None:
        """Return an identity only when a current-tenant grant establishes membership."""

        row = self._connection.execute(
            """
            SELECT identity.id, identity.email, identity.status
              FROM identity
             WHERE identity.id = %(user_id)s
               AND EXISTS (
                   SELECT 1
                     FROM user_role
                    WHERE user_role.org_id = %(org_id)s
                      AND user_role.user_id = identity.id
               )
            """,
            self.scoped_params({"user_id": user_id}),
        ).fetchone()
        if row is None:
            return None
        return AccessUser(
            id=IdentityId(cast(UUID, _value(row, 0, "id"))),
            email=cast(str, _value(row, 1, "email")),
            status=cast(str, _value(row, 2, "status")),
        )

    def grants(
        self, user_id: IdentityId
    ) -> tuple[tuple[AccessGrant, ...], tuple[AccessGrant, ...]]:
        """Split current and future assignments using the database transaction clock."""

        rows = self._connection.execute(
            """
            SELECT assignment.id, role.code, role.name,
                   assignment.scope_type, assignment.scope_id,
                   assignment.granted_at, assignment.granted_by,
                   grantor.email AS granted_by_email, assignment.effective_from,
                   assignment.effective_from IS NOT NULL
                       AND assignment.effective_from > CURRENT_TIMESTAMP AS pending
              FROM user_role AS assignment
              JOIN role
                ON role.org_id = assignment.org_id
               AND role.id = assignment.role_id
              LEFT JOIN identity AS grantor ON grantor.id = assignment.granted_by
             WHERE assignment.org_id = %(org_id)s
               AND assignment.user_id = %(user_id)s
             ORDER BY pending, assignment.effective_from NULLS FIRST,
                      role.name, assignment.scope_type, assignment.scope_id, assignment.id
            """,
            self.scoped_params({"user_id": user_id}),
        ).fetchall()
        active: list[AccessGrant] = []
        pending: list[AccessGrant] = []
        for row in rows:
            (pending if cast(bool, _value(row, 9, "pending")) else active).append(_grant(row))
        return tuple(active), tuple(pending)

    def permissions(self, user_id: IdentityId) -> tuple[ExplainedPermission, ...]:
        """Return one source row per effective role permission, never a bare code."""

        rows = self._connection.execute(
            """
            SELECT permission.code, role.code, assignment.scope_type, assignment.scope_id
              FROM user_role AS assignment
              JOIN role
                ON role.org_id = assignment.org_id
               AND role.id = assignment.role_id
              JOIN role_permission
                ON role_permission.org_id = role.org_id
               AND role_permission.role_id = role.id
              JOIN permission ON permission.code = role_permission.permission_code
             WHERE assignment.org_id = %(org_id)s
               AND assignment.user_id = %(user_id)s
               AND (
                   assignment.effective_from IS NULL
                   OR assignment.effective_from <= CURRENT_TIMESTAMP
               )
             ORDER BY permission.code, role.code, assignment.scope_type,
                      assignment.scope_id, assignment.id
            """,
            self.scoped_params({"user_id": user_id}),
        ).fetchall()
        return tuple(
            ExplainedPermission(
                code=PermissionCode(cast(str, _value(row, 0, "permission_code"))),
                allowed=True,
                source=PermissionSource(
                    via_role=RoleCode(cast(str, _value(row, 1, "role_code"))),
                    via_scope_type=(
                        scope_type := ScopeType(cast(str, _value(row, 2, "scope_type")))
                    ),
                    via_scope_id=(scope_id := cast(UUID, _value(row, 3, "scope_id"))),
                    inherited_from=(
                        f"{scope_type.value}:{scope_id}"
                        if scope_type in {ScopeType.ORG, ScopeType.BU}
                        else None
                    ),
                ),
            )
            for row in rows
        )

    def denied_examples(
        self,
        user_id: IdentityId,
        allowed_codes: frozenset[PermissionCode],
    ) -> tuple[DeniedExample, ...]:
        """Explain every missing catalog permission, including pending-only sources."""

        pending_rows = self._connection.execute(
            """
            SELECT role_permission.permission_code, role.name,
                   assignment.scope_type, assignment.scope_id,
                   assignment.effective_from
              FROM user_role AS assignment
              JOIN role
                ON role.org_id = assignment.org_id
               AND role.id = assignment.role_id
              JOIN role_permission
                ON role_permission.org_id = role.org_id
               AND role_permission.role_id = role.id
             WHERE assignment.org_id = %(org_id)s
               AND assignment.user_id = %(user_id)s
               AND assignment.effective_from > CURRENT_TIMESTAMP
             ORDER BY role_permission.permission_code, assignment.effective_from, assignment.id
            """,
            self.scoped_params({"user_id": user_id}),
        ).fetchall()
        pending_reasons: dict[PermissionCode, str] = {}
        for row in pending_rows:
            code = PermissionCode(cast(str, _value(row, 0, "permission_code")))
            if code in pending_reasons:
                continue
            scope_type = ScopeType(cast(str, _value(row, 2, "scope_type")))
            scope_id = cast(UUID, _value(row, 3, "scope_id"))
            effective_from = cast(datetime, _value(row, 4, "effective_from"))
            pending_reasons[code] = (
                f"The {cast(str, _value(row, 1, 'role_name'))} grant at "
                f"{_scope_name(scope_type, scope_id)} becomes active at "
                f"{effective_from.isoformat()}."
            )

        catalog_rows = self._connection.execute(
            "SELECT code FROM permission ORDER BY code"
        ).fetchall()
        return tuple(
            DeniedExample(
                code=code,
                reason=pending_reasons.get(
                    code,
                    "No active role grant includes this permission in the organization.",
                ),
            )
            for row in catalog_rows
            if (code := PermissionCode(cast(str, _value(row, 0, "code")))) not in allowed_codes
        )


class EffectiveAccessService:
    """Explain sensitive access and append an audit event in the same transaction."""

    def __init__(
        self,
        connection: EffectiveAccessConnection,
        scope: Scope,
        viewer_id: IdentityId,
    ) -> None:
        self._connection = connection
        self._scope = scope
        self._viewer = AuditActor(ActorKind.USER, viewer_id)

    def explain(self, user_id: IdentityId) -> EffectiveAccess:
        """Return effective access or conceal a subject outside the organization."""

        with tenant_transaction(self._connection, self._scope):
            repository = EffectiveAccessRepository(self._connection, self._scope)
            user = repository.user(user_id)
            if user is None:
                raise LookupError("user was not found")
            grants, pending_grants = repository.grants(user_id)
            permissions = repository.permissions(user_id)
            denied = repository.denied_examples(
                user_id,
                frozenset(permission.code for permission in permissions),
            )
            AuditWriter(self._connection, self._scope).write(
                actor=self._viewer,
                action="user.effective_access.view",
                target_type="identity",
                target_id=user_id,
                outcome=Outcome.SUCCESS,
                reason="Viewed effective access",
            )
            return EffectiveAccess(user, grants, pending_grants, permissions, denied)

    def permission_page(
        self,
        user_id: IdentityId,
        *,
        filter_text: str,
        sort_by: str | None,
        descending: bool,
        cursor: str | None,
        page_size: int,
    ) -> PermissionPage:
        """Return a bounded server-filtered page while preserving source explanations."""

        if page_size < 1 or page_size > 50:
            raise ValueError("page size must be between 1 and 50")
        explanation = self.explain(user_id)
        rows = [
            PermissionTableRow(
                id=(
                    f"{permission.code}|allowed|{permission.source.via_role}|"
                    f"{permission.source.via_scope_type.value}|"
                    f"{permission.source.via_scope_id}"
                ),
                code=permission.code,
                access="allowed",
                source=(
                    f"{permission.source.via_role} at "
                    f"{permission.source.via_scope_type.value}:"
                    f"{permission.source.via_scope_id}"
                ),
                explanation=(
                    f"Inherited from {permission.source.inherited_from}."
                    if permission.source.inherited_from is not None
                    else "Granted directly at this scope."
                ),
            )
            for permission in explanation.permissions
        ]
        rows.extend(
            PermissionTableRow(
                id=f"{denial.code}|denied",
                code=denial.code,
                access="denied",
                source="No active source",
                explanation=denial.reason,
            )
            for denial in explanation.denied_examples
        )
        needle = filter_text.strip().casefold()
        if needle:
            rows = [
                row
                for row in rows
                if needle
                in " ".join((row.code, row.access, row.source, row.explanation)).casefold()
            ]
        sort_key = sort_by or "code"
        if sort_key not in {"code", "access", "source"}:
            raise ValueError("unsupported permission sort")
        rows.sort(
            key=lambda row: (cast(str, getattr(row, sort_key)).casefold(), row.id),
            reverse=descending,
        )
        offset = _cursor_offset(cursor)
        total = len(rows)
        offset = min(offset, max(0, total - 1)) if total else 0
        page_rows = tuple(rows[offset : offset + page_size])
        last_offset = max(0, ((max(0, total - 1)) // page_size) * page_size)
        return PermissionPage(
            last_cursor=_page_cursor(last_offset) if total > page_size else None,
            next_cursor=(_page_cursor(offset + page_size) if offset + page_size < total else None),
            previous_cursor=(_page_cursor(max(0, offset - page_size)) if offset > 0 else None),
            rows=page_rows,
            start_index=offset,
            total=total,
        )


def _page_cursor(offset: int) -> str:
    raw = f"offset:{offset}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode("ascii")
        prefix, value = decoded.split(":", 1)
        offset = int(value)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid permission cursor") from exc
    if prefix != "offset" or offset < 0:
        raise ValueError("invalid permission cursor")
    return offset
