"""Transactional role and grant changes with before/after audit evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from psycopg import sql
from pydantic import BaseModel

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.db.repo import ScopedRepo
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.session.store import SessionConnection, SessionRecord, revoke_all_sessions
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import (
    BASELINE_ROLE_PERMISSIONS,
    BASELINE_ROLES,
    MFA_REQUIRED_ROLE_CODES,
    PermissionCode,
    Role,
    RoleCode,
    UserRole,
    permission_code,
)
from flo.modules.identity.models import (
    AuthorizationTarget as AuthorizationTarget,
)
from flo.modules.identity.models import (
    ScopeType as ScopeType,
)
from flo.modules.identity.models import (
    role_code as role_code,
)

type DatabaseRow = Sequence[object] | Mapping[str, object]


class QueryResult(Protocol):
    """Result surface shared by psycopg and focused test doubles."""

    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> DatabaseRow | None: ...

    def fetchall(self) -> list[DatabaseRow]: ...


class IdentityAuthorizationConnection(AuditConnection, Protocol):
    """One connection shared by RBAC mutation, RLS, and its audit row."""

    def execute(
        self,
        query: str | sql.Composed,
        params: Mapping[str, object] | None = None,
    ) -> QueryResult: ...


def _value(row: DatabaseRow, index: int, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]


def _role(row: DatabaseRow) -> Role:
    return Role(
        id=cast(UUID, _value(row, 0, "id")),
        code=RoleCode(cast(str, _value(row, 1, "code"))),
        name=cast(str, _value(row, 2, "name")),
        is_system=cast(bool, _value(row, 3, "is_system")),
    )


def _user_role(row: DatabaseRow) -> UserRole:
    return UserRole(
        id=cast(UUID, _value(row, 0, "id")),
        user_id=IdentityId(cast(UUID, _value(row, 1, "user_id"))),
        role_id=cast(UUID, _value(row, 2, "role_id")),
        scope_type=ScopeType(cast(str, _value(row, 3, "scope_type"))),
        scope_id=cast(UUID, _value(row, 4, "scope_id")),
        granted_by=cast(IdentityId | None, _value(row, 5, "granted_by")),
        granted_at=cast(datetime, _value(row, 6, "granted_at")),
        effective_from=cast(datetime | None, _value(row, 7, "effective_from")),
    )


class RoleRepository(ScopedRepo[Role]):
    """The only query path for roles, permission membership, and assignments."""

    def __init__(self, connection: IdentityAuthorizationConnection, scope: Scope) -> None:
        super().__init__(connection, scope)
        self._connection = connection

    def create_role(
        self,
        code: RoleCode,
        name: str,
        *,
        is_system: bool,
    ) -> tuple[Role, bool]:
        role_id = uuid4()
        row = self._connection.execute(
            """
            INSERT INTO role (id, org_id, code, name, is_system)
            VALUES (%(id)s, %(org_id)s, %(code)s, %(name)s, %(is_system)s)
            ON CONFLICT (org_id, code) DO NOTHING
            RETURNING id, code, name, is_system
            """,
            self.scoped_params({"id": role_id, "code": code, "name": name, "is_system": is_system}),
        ).fetchone()
        if row is not None:
            return _role(row), True
        existing = self._connection.execute(
            """
            SELECT id, code, name, is_system
              FROM role
             WHERE org_id = %(org_id)s AND code = %(code)s
            """,
            self.scoped_params({"code": code}),
        ).fetchone()
        if existing is None:
            raise RuntimeError("role conflict did not resolve to an existing row")
        return _role(existing), False

    def attach_permission(self, role_id: UUID, permission: PermissionCode) -> bool:
        result = self._connection.execute(
            """
            INSERT INTO role_permission (org_id, role_id, permission_code)
            SELECT %(org_id)s, role.id, permission.code
              FROM role
              JOIN permission ON permission.code = %(permission)s
             WHERE role.org_id = %(org_id)s AND role.id = %(role_id)s
            ON CONFLICT DO NOTHING
            """,
            self.scoped_params({"role_id": role_id, "permission": permission}),
        )
        return result.rowcount == 1

    def detach_permission(self, role_id: UUID, permission: PermissionCode) -> bool:
        result = self._connection.execute(
            """
            DELETE FROM role_permission
             WHERE org_id = %(org_id)s
               AND role_id = %(role_id)s
               AND permission_code = %(permission)s
            """,
            self.scoped_params({"role_id": role_id, "permission": permission}),
        )
        return result.rowcount == 1

    def register_scope(
        self,
        scope_type: ScopeType,
        scope_id: UUID,
        parent_scope_type: ScopeType,
        parent_scope_id: UUID,
        *,
        roll_down: bool,
    ) -> bool:
        if scope_type is ScopeType.ORG:
            raise ValueError("organization scope is derived from the session")
        if scope_type is ScopeType.BU and (
            parent_scope_type is not ScopeType.ORG or parent_scope_id != self.scope.org_id
        ):
            raise ValueError("a BU scope must be directly contained by the session organization")
        if scope_type is ScopeType.PROJECT and parent_scope_type is ScopeType.ORG:
            raise ValueError("a project scope must be contained by a BU or project")

        parent_exists = parent_scope_type is ScopeType.ORG
        if not parent_exists:
            parent_exists = (
                self._connection.execute(
                    """
                SELECT 1
                  FROM authorization_scope
                 WHERE org_id = %(org_id)s
                   AND scope_type = %(parent_scope_type)s
                   AND scope_id = %(parent_scope_id)s
                """,
                    self.scoped_params(
                        {
                            "parent_scope_type": parent_scope_type.value,
                            "parent_scope_id": parent_scope_id,
                        }
                    ),
                ).fetchone()
                is not None
            )
        if not parent_exists:
            raise LookupError("parent authorization scope was not found")

        result = self._connection.execute(
            """
            INSERT INTO authorization_scope (
                org_id, scope_type, scope_id, parent_scope_type, parent_scope_id, roll_down
            ) VALUES (
                %(org_id)s, %(scope_type)s, %(scope_id)s,
                %(parent_scope_type)s, %(parent_scope_id)s, %(roll_down)s
            )
            ON CONFLICT (org_id, scope_type, scope_id) DO UPDATE
                SET parent_scope_type = EXCLUDED.parent_scope_type,
                    parent_scope_id = EXCLUDED.parent_scope_id,
                    roll_down = EXCLUDED.roll_down
                WHERE authorization_scope.parent_scope_type IS DISTINCT FROM
                          EXCLUDED.parent_scope_type
                   OR authorization_scope.parent_scope_id IS DISTINCT FROM EXCLUDED.parent_scope_id
                   OR authorization_scope.roll_down IS DISTINCT FROM EXCLUDED.roll_down
            """,
            self.scoped_params(
                {
                    "scope_type": scope_type.value,
                    "scope_id": scope_id,
                    "parent_scope_type": parent_scope_type.value,
                    "parent_scope_id": parent_scope_id,
                    "roll_down": roll_down,
                }
            ),
        )
        return result.rowcount == 1

    def grant_role(
        self,
        user_id: IdentityId,
        role_id: UUID,
        target: AuthorizationTarget,
        granted_by: IdentityId,
        effective_from: datetime | None,
    ) -> tuple[UserRole, bool, RoleCode]:
        if target.record_id is not None:
            raise ValueError("roles can be granted only to org, BU, or project scopes")
        if target.scope_type is ScopeType.ORG and target.scope_id != self.scope.org_id:
            raise LookupError("organization scope was not found")
        role_row = self._connection.execute(
            """
            SELECT code FROM role
             WHERE org_id = %(org_id)s AND id = %(role_id)s
            """,
            self.scoped_params({"role_id": role_id}),
        ).fetchone()
        if role_row is None:
            raise LookupError("role or authorization scope was not found")
        granted_role_code = RoleCode(cast(str, _value(role_row, 0, "code")))
        assignment_id = uuid4()
        row = self._connection.execute(
            """
            INSERT INTO user_role (
                id, org_id, user_id, role_id, scope_type, scope_id,
                granted_by, effective_from
            )
            SELECT %(id)s, %(org_id)s, %(user_id)s, role.id,
                   %(scope_type)s, %(scope_id)s, %(granted_by)s, %(effective_from)s
              FROM role
             WHERE role.org_id = %(org_id)s
               AND role.id = %(role_id)s
               AND (
                   %(scope_type)s = 'org'
                   OR EXISTS (
                       SELECT 1
                         FROM authorization_scope
                        WHERE authorization_scope.org_id = %(org_id)s
                          AND authorization_scope.scope_type = %(scope_type)s
                          AND authorization_scope.scope_id = %(scope_id)s
                   )
               )
            ON CONFLICT (org_id, user_id, role_id, scope_type, scope_id) DO NOTHING
            RETURNING id, user_id, role_id, scope_type, scope_id,
                      granted_by, granted_at, effective_from
            """,
            self.scoped_params(
                {
                    "id": assignment_id,
                    "user_id": user_id,
                    "role_id": role_id,
                    "scope_type": target.scope_type.value,
                    "scope_id": target.scope_id,
                    "granted_by": granted_by,
                    "effective_from": effective_from,
                }
            ),
        ).fetchone()
        if row is not None:
            return _user_role(row), True, granted_role_code
        existing = self._connection.execute(
            """
            SELECT id, user_id, role_id, scope_type, scope_id,
                   granted_by, granted_at, effective_from
              FROM user_role
             WHERE org_id = %(org_id)s
               AND user_id = %(user_id)s
               AND role_id = %(role_id)s
               AND scope_type = %(scope_type)s
               AND scope_id = %(scope_id)s
            """,
            self.scoped_params(
                {
                    "user_id": user_id,
                    "role_id": role_id,
                    "scope_type": target.scope_type.value,
                    "scope_id": target.scope_id,
                }
            ),
        ).fetchone()
        if existing is None:
            raise LookupError("role or authorization scope was not found")
        return _user_role(existing), False, granted_role_code

    def revoke_role(self, assignment_id: UUID) -> tuple[UserRole, RoleCode] | None:
        row = self._connection.execute(
            """
            DELETE FROM user_role AS assignment
             USING role
             WHERE assignment.org_id = %(org_id)s
               AND assignment.id = %(assignment_id)s
               AND role.org_id = assignment.org_id
               AND role.id = assignment.role_id
            RETURNING assignment.id, assignment.user_id, assignment.role_id,
                      assignment.scope_type, assignment.scope_id,
                      assignment.granted_by, assignment.granted_at,
                      assignment.effective_from, role.code
            """,
            self.scoped_params({"assignment_id": assignment_id}),
        ).fetchone()
        if row is None:
            return None
        return _user_role(row), RoleCode(cast(str, _value(row, 8, "code")))

    def revoke_user_role(
        self,
        user_id: IdentityId,
        assignment_id: UUID,
    ) -> tuple[UserRole, RoleCode] | None:
        """Delete one assignment only when both URL identifiers name the same tenant row."""

        row = self._connection.execute(
            """
            DELETE FROM user_role AS assignment
             USING role
             WHERE assignment.org_id = %(org_id)s
               AND assignment.user_id = %(user_id)s
               AND assignment.id = %(assignment_id)s
               AND role.org_id = assignment.org_id
               AND role.id = assignment.role_id
            RETURNING assignment.id, assignment.user_id, assignment.role_id,
                      assignment.scope_type, assignment.scope_id,
                      assignment.granted_by, assignment.granted_at,
                      assignment.effective_from, role.code
            """,
            self.scoped_params({"user_id": user_id, "assignment_id": assignment_id}),
        ).fetchone()
        if row is None:
            return None
        return _user_role(row), RoleCode(cast(str, _value(row, 8, "code")))

    def lock_user_status(self, user_id: IdentityId) -> tuple[str, str] | None:
        """Lock a current-tenant identity before changing its global login state."""

        row = self._connection.execute(
            """
            SELECT identity.email, identity.status
              FROM identity
             WHERE identity.id = %(user_id)s
               AND EXISTS (
                   SELECT 1
                     FROM user_role
                    WHERE user_role.org_id = %(org_id)s
                      AND user_role.user_id = identity.id
               )
             FOR UPDATE OF identity
            """,
            self.scoped_params({"user_id": user_id}),
        ).fetchone()
        if row is None:
            return None
        return cast(str, _value(row, 0, "email")), cast(str, _value(row, 1, "status"))

    def deactivate_user(self, user_id: IdentityId, deactivated_at: datetime) -> None:
        """Persist deactivation without deleting or anonymizing attribution."""

        result = self._connection.execute(
            """
            UPDATE identity
               SET status = 'deactivated',
                   deactivated_at = %(deactivated_at)s,
                   updated_at = %(deactivated_at)s
             WHERE id = %(user_id)s AND status = 'active'
            """,
            {"user_id": user_id, "deactivated_at": deactivated_at},
        )
        if result.rowcount != 1:
            raise RuntimeError("locked active identity was not deactivated")

    def adjust_privileged_role_grants(self, user_id: IdentityId, delta: int) -> None:
        """Maintain only a global count, never tenant role details, for the MFA gate."""

        if delta not in {-1, 1}:
            raise ValueError("privileged role grant adjustment must be -1 or 1")
        updated = self._connection.execute(
            """
            UPDATE identity
               SET privileged_role_grants = privileged_role_grants + %(delta)s,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %(user_id)s
               AND privileged_role_grants + %(delta)s >= 0
            """,
            {"user_id": user_id, "delta": delta},
        )
        if updated.rowcount != 1:
            raise RuntimeError("privileged role grant count could not be updated")


class IdentityAuthorizationService:
    """Write RBAC state and its audit evidence in one tenant transaction."""

    def __init__(
        self,
        connection: IdentityAuthorizationConnection,
        scope: Scope,
        actor_id: IdentityId,
    ) -> None:
        self._connection = connection
        self._scope = scope
        self._actor = AuditActor(ActorKind.USER, actor_id)

    def _repository(self) -> RoleRepository:
        return RoleRepository(self._connection, self._scope)

    def _audit(self) -> AuditWriter:
        return AuditWriter(self._connection, self._scope)

    def create_role(self, code: str, name: str, *, is_system: bool = False) -> Role:
        """Create an empty role; no permission is inherited or implied."""

        checked_code = role_code(code)
        if not name.strip():
            raise ValueError("role name must be non-empty")
        with tenant_transaction(self._connection, self._scope):
            role, created = self._repository().create_role(
                checked_code, name.strip(), is_system=is_system
            )
            if created:
                self._audit().write(
                    actor=self._actor,
                    action="role.create",
                    target_type="role",
                    target_id=role.id,
                    outcome=Outcome.SUCCESS,
                    after_source=role,
                    after_fields=("id", "code", "name", "is_system"),
                )
            return role

    def seed_baseline_roles(self) -> dict[RoleCode, Role]:
        """Materialize the eleven explicit system roles for the current organization."""

        seeded: dict[RoleCode, Role] = {}
        for code, name in BASELINE_ROLES:
            role = self.create_role(code, name, is_system=True)
            seeded[code] = role
            for permission in sorted(BASELINE_ROLE_PERMISSIONS[code]):
                self.grant_permission(role.id, permission)
        return seeded

    def grant_permission(self, role_id: UUID, permission: str) -> bool:
        """Attach one named permission; roles otherwise remain default-deny."""

        checked_permission = permission_code(permission)
        with tenant_transaction(self._connection, self._scope):
            attached = self._repository().attach_permission(role_id, checked_permission)
            if attached:
                self._audit().write(
                    actor=self._actor,
                    action="role.permission.grant",
                    target_type="role",
                    target_id=role_id,
                    outcome=Outcome.SUCCESS,
                    before_source={"permission": None},
                    before_fields=("permission",),
                    after_source={"permission": checked_permission},
                    after_fields=("permission",),
                )
            return attached

    def revoke_permission(self, role_id: UUID, permission: str) -> bool:
        """Detach one permission with no cache window after commit."""

        checked_permission = permission_code(permission)
        with tenant_transaction(self._connection, self._scope):
            detached = self._repository().detach_permission(role_id, checked_permission)
            if detached:
                self._audit().write(
                    actor=self._actor,
                    action="role.permission.revoke",
                    target_type="role",
                    target_id=role_id,
                    outcome=Outcome.SUCCESS,
                    before_source={"permission": checked_permission},
                    before_fields=("permission",),
                    after_source={"permission": None},
                    after_fields=("permission",),
                )
            return detached

    def register_scope(
        self,
        scope_type: ScopeType,
        scope_id: UUID,
        parent_scope_type: ScopeType,
        parent_scope_id: UUID,
        *,
        roll_down: bool = True,
    ) -> bool:
        """Register the server-owned BU/project containment used by recursive checks."""

        with tenant_transaction(self._connection, self._scope):
            return self._repository().register_scope(
                scope_type,
                scope_id,
                parent_scope_type,
                parent_scope_id,
                roll_down=roll_down,
            )

    def grant_role(
        self,
        user_id: IdentityId,
        role_id: UUID,
        target: AuthorizationTarget,
        *,
        effective_from: datetime | None = None,
    ) -> UserRole:
        """Assign a role at one validated contained scope and audit the grant."""

        with tenant_transaction(self._connection, self._scope):
            assignment, created, granted_role_code = self._repository().grant_role(
                user_id,
                role_id,
                target,
                cast(IdentityId, self._actor.id),
                effective_from,
            )
            if created:
                if granted_role_code in MFA_REQUIRED_ROLE_CODES:
                    self._repository().adjust_privileged_role_grants(user_id, 1)
                self._audit().write(
                    actor=self._actor,
                    action="user_role.grant",
                    target_type="user_role",
                    target_id=assignment.id,
                    outcome=Outcome.SUCCESS,
                    after_source=assignment,
                    after_fields=(
                        "id",
                        "user_id",
                        "role_id",
                        "scope_type",
                        "scope_id",
                        "granted_by",
                        "granted_at",
                        "effective_from",
                    ),
                )
            return assignment

    def revoke_role(self, assignment_id: UUID) -> bool:
        """Remove an assignment and audit its complete prior grant."""

        with tenant_transaction(self._connection, self._scope):
            revoked = self._repository().revoke_role(assignment_id)
            if revoked is None:
                return False
            assignment, revoked_role_code = revoked
            if revoked_role_code in MFA_REQUIRED_ROLE_CODES:
                self._repository().adjust_privileged_role_grants(assignment.user_id, -1)
            self._audit().write(
                actor=self._actor,
                action="user_role.revoke",
                target_type="user_role",
                target_id=assignment.id,
                outcome=Outcome.SUCCESS,
                before_source=assignment,
                before_fields=(
                    "id",
                    "user_id",
                    "role_id",
                    "scope_type",
                    "scope_id",
                    "granted_by",
                    "granted_at",
                    "effective_from",
                ),
            )
            return True

    def revoke_user_role(self, user_id: IdentityId, assignment_id: UUID) -> bool:
        """Revoke only the subject's named grant and audit its complete provenance."""

        with tenant_transaction(self._connection, self._scope):
            revoked = self._repository().revoke_user_role(user_id, assignment_id)
            if revoked is None:
                return False
            assignment, revoked_role_code = revoked
            if revoked_role_code in MFA_REQUIRED_ROLE_CODES:
                self._repository().adjust_privileged_role_grants(assignment.user_id, -1)
            self._audit().write(
                actor=self._actor,
                action="user_role.revoke",
                target_type="user_role",
                target_id=assignment.id,
                outcome=Outcome.SUCCESS,
                before_source=assignment,
                before_fields=(
                    "id",
                    "user_id",
                    "role_id",
                    "scope_type",
                    "scope_id",
                    "granted_by",
                    "granted_at",
                    "effective_from",
                ),
            )
            return True

    def deactivate_user(self, user_id: IdentityId) -> bool:
        """Deactivate an identity and revoke every session without deleting attribution."""

        with tenant_transaction(self._connection, self._scope):
            repository = self._repository()
            locked = repository.lock_user_status(user_id)
            if locked is None:
                raise LookupError("user was not found")
            email, status = locked
            if status == "deactivated":
                return False
            clock_row = self._connection.execute("SELECT clock_timestamp()").fetchone()
            if clock_row is None:
                raise RuntimeError("database clock did not return a timestamp")
            deactivated_at = cast(datetime, _value(clock_row, 0, "clock_timestamp"))
            repository.deactivate_user(user_id, deactivated_at)
            revoke_all_sessions(
                cast(SessionConnection, self._connection),
                user_id,
                deactivated_at,
            )
            self._audit().write(
                actor=self._actor,
                action="identity.deactivate",
                target_type="identity",
                target_id=user_id,
                outcome=Outcome.SUCCESS,
                before_source={"email": email, "status": "active"},
                before_fields=("email", "status"),
                after_source={"email": email, "status": "deactivated"},
                after_fields=("email", "status"),
            )
            return True


def register_business_unit_scope(
    connection: IdentityAuthorizationConnection, scope: Scope, unit_id: UUID
) -> None:
    """Register either unit kind directly under its org in the caller's transaction."""
    RoleRepository(connection, scope).register_scope(
        ScopeType.BU, unit_id, ScopeType.ORG, scope.org_id, roll_down=True
    )


def register_project_scope(
    connection: IdentityAuthorizationConnection,
    scope: Scope,
    project_id: UUID,
    parent_kind: Literal["bu", "project"],
    parent_id: UUID,
) -> None:
    """Register containment in the existing project creation transaction."""
    RoleRepository(connection, scope).register_scope(
        ScopeType.PROJECT, project_id, ScopeType(parent_kind), parent_id, roll_down=True
    )


def user_exists(connection: IdentityAuthorizationConnection, scope: Scope, user_id: UUID) -> bool:
    """Membership is evidenced by a role assignment in the session organization."""
    repository = RoleRepository(connection, scope)
    return (
        connection.execute(
            """SELECT 1 FROM identity WHERE id = %(user_id)s AND EXISTS (
        SELECT 1 FROM user_role WHERE org_id = %(org_id)s AND user_id = identity.id)""",
            repository.scoped_params({"user_id": user_id}),
        ).fetchone()
        is not None
    )


def identity_organization(connection: IdentityAuthorizationConnection, email: str) -> UUID | None:
    """Resolve global membership before tenant context exists."""
    row = connection.execute(
        """SELECT membership.org_id FROM identity
        JOIN identity_membership membership ON membership.identity_id = identity.id
        WHERE identity.email = %(email)s""",
        {"email": email.casefold()},
    ).fetchone()
    return None if row is None else cast(UUID, _value(row, 0, "org_id"))


def add_identity_membership(
    connection: IdentityAuthorizationConnection, identity_id: IdentityId, org_id: UUID
) -> None:
    """Add one organization membership; the database rejects duplicate membership."""
    connection.execute(
        "INSERT INTO identity_membership (identity_id, org_id) VALUES (%(identity)s, %(org)s)",
        {"identity": identity_id, "org": org_id},
    )


class OrganizationMembership(BaseModel):
    org_id: UUID
    name: str
    tenant_label: str | None
    last_used_at: datetime | None


def list_identity_memberships(
    connection: IdentityAuthorizationConnection, identity_id: IdentityId
) -> list[OrganizationMembership]:
    """Read only the caller's global display links before tenant selection."""
    rows = connection.execute(
        """SELECT m.org_id, c.display_name, c.tenant_label, m.last_used_at
        FROM identity_membership m JOIN organization_code c ON c.org_id = m.org_id
        WHERE m.identity_id = %(identity)s
        ORDER BY m.last_used_at DESC NULLS LAST, c.display_name, m.org_id""",
        {"identity": identity_id},
    ).fetchall()
    return [
        OrganizationMembership(
            org_id=cast(UUID, _value(row, 0, "org_id")),
            name=cast(str, _value(row, 1, "display_name")),
            tenant_label=cast(str | None, _value(row, 2, "tenant_label")),
            last_used_at=cast(datetime | None, _value(row, 3, "last_used_at")),
        )
        for row in rows
    ]


def select_identity_organization(
    connection: IdentityAuthorizationConnection, session: SessionRecord, org_id: UUID
) -> None:
    """Choose an owned membership atomically; repeated selection is a no-op."""
    with connection.transaction():
        membership = connection.execute(
            """SELECT 1 FROM identity_membership
            WHERE identity_id = %(identity)s AND org_id = %(org)s FOR SHARE""",
            {"identity": session.identity_id, "org": org_id},
        ).fetchone()
        if membership is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        row = connection.execute(
            """SELECT org_id FROM auth_session WHERE id = %(session)s
            AND identity_id = %(identity)s AND revoked_at IS NULL FOR UPDATE""",
            {"session": session.id, "identity": session.identity_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.UNAUTHORIZED)
        if _value(row, 0, "org_id") == org_id:
            return
        connection.execute(
            "UPDATE auth_session SET org_id = %(org)s WHERE id = %(session)s",
            {"org": org_id, "session": session.id},
        )
        connection.execute(
            """UPDATE identity_membership SET last_used_at = now()
            WHERE identity_id = %(identity)s AND org_id = %(org)s""",
            {"identity": session.identity_id, "org": org_id},
        )
        connection.execute(
            """INSERT INTO session_security_event
            (session_id, identity_id, event_type, occurred_at, ip_prefix, user_agent)
            VALUES (%(session)s, %(identity)s, 'organization_switched', now(), %(ip)s, %(ua)s)""",
            {
                "session": session.id,
                "identity": session.identity_id,
                "ip": session.ip_prefix,
                "ua": session.user_agent,
            },
        )


def identity_email(connection: IdentityAuthorizationConnection, identity_id: UUID) -> str | None:
    """Read the globally resolved uploader address without mutating identity."""
    row = connection.execute(
        "SELECT email FROM identity WHERE id=%(id)s", {"id": identity_id}
    ).fetchone()
    return None if row is None else cast(str, _value(row, 0, "email"))
