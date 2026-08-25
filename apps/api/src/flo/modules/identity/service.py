"""Transactional role and grant changes with before/after audit evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast
from uuid import UUID, uuid4

from psycopg import sql

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.db.repo import ScopedRepo
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import (
    BASELINE_ROLE_PERMISSIONS,
    BASELINE_ROLES,
    AuthorizationTarget,
    PermissionCode,
    Role,
    RoleCode,
    ScopeType,
    UserRole,
    permission_code,
    role_code,
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
            self.scoped_params(
                {"id": role_id, "code": code, "name": name, "is_system": is_system}
            ),
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
            parent_scope_type is not ScopeType.ORG
            or parent_scope_id != self.scope.org_id
        ):
            raise ValueError("a BU scope must be directly contained by the session organization")
        if scope_type is ScopeType.PROJECT and parent_scope_type is ScopeType.ORG:
            raise ValueError("a project scope must be contained by a BU or project")

        parent_exists = parent_scope_type is ScopeType.ORG
        if not parent_exists:
            parent_exists = self._connection.execute(
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
            ).fetchone() is not None
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
    ) -> tuple[UserRole, bool]:
        if target.record_id is not None:
            raise ValueError("roles can be granted only to org, BU, or project scopes")
        if target.scope_type is ScopeType.ORG and target.scope_id != self.scope.org_id:
            raise LookupError("organization scope was not found")
        assignment_id = uuid4()
        row = self._connection.execute(
            """
            INSERT INTO user_role (id, org_id, user_id, role_id, scope_type, scope_id)
            SELECT %(id)s, %(org_id)s, %(user_id)s, role.id,
                   %(scope_type)s, %(scope_id)s
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
            RETURNING id, user_id, role_id, scope_type, scope_id
            """,
            self.scoped_params(
                {
                    "id": assignment_id,
                    "user_id": user_id,
                    "role_id": role_id,
                    "scope_type": target.scope_type.value,
                    "scope_id": target.scope_id,
                }
            ),
        ).fetchone()
        if row is not None:
            return _user_role(row), True
        existing = self._connection.execute(
            """
            SELECT id, user_id, role_id, scope_type, scope_id
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
        return _user_role(existing), False

    def revoke_role(self, assignment_id: UUID) -> UserRole | None:
        row = self._connection.execute(
            """
            DELETE FROM user_role
             WHERE org_id = %(org_id)s AND id = %(assignment_id)s
            RETURNING id, user_id, role_id, scope_type, scope_id
            """,
            self.scoped_params({"assignment_id": assignment_id}),
        ).fetchone()
        return None if row is None else _user_role(row)


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
    ) -> UserRole:
        """Assign a role at one validated contained scope and audit the grant."""

        with tenant_transaction(self._connection, self._scope):
            assignment, created = self._repository().grant_role(user_id, role_id, target)
            if created:
                self._audit().write(
                    actor=self._actor,
                    action="user_role.grant",
                    target_type="user_role",
                    target_id=assignment.id,
                    outcome=Outcome.SUCCESS,
                    after_source=assignment,
                    after_fields=("id", "user_id", "role_id", "scope_type", "scope_id"),
                )
            return assignment

    def revoke_role(self, assignment_id: UUID) -> bool:
        """Remove an assignment and audit its complete prior grant."""

        with tenant_transaction(self._connection, self._scope):
            assignment = self._repository().revoke_role(assignment_id)
            if assignment is None:
                return False
            self._audit().write(
                actor=self._actor,
                action="user_role.revoke",
                target_type="user_role",
                target_id=assignment.id,
                outcome=Outcome.SUCCESS,
                before_source=assignment,
                before_fields=("id", "user_id", "role_id", "scope_type", "scope_id"),
            )
            return True
