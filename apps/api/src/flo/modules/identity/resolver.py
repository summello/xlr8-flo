"""Uncached, recursive effective-permission resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast
from uuid import UUID

from psycopg import sql

from flo.kernel.authz import AuthorizationDecision as DecisionProtocol
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import GrantedScopes
from flo.kernel.db.repo import ScopedRepo
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.modules.identity.models import (
    AuthorizationContext,
    AuthorizationDecision,
    AuthorizationTarget,
    ScopeType,
    permission_code,
)

type DatabaseRow = Sequence[object] | Mapping[str, object]


class QueryResult(Protocol):
    """Result surface shared by psycopg and focused test doubles."""

    def fetchone(self) -> DatabaseRow | None: ...

    def fetchall(self) -> list[DatabaseRow]: ...


class AuthorizationConnection(Protocol):
    """Scoped database surface required for one effective-access check."""

    def execute(
        self,
        query: str | sql.Composed,
        params: Mapping[str, object] | None = None,
    ) -> QueryResult: ...


def _value(row: DatabaseRow, index: int, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]


def _scope_cte() -> str:
    """One scope and effective-date definition for checks and holder queries."""
    return """
            WITH RECURSIVE ancestors (
                scope_type, scope_id, project_grant_allowed
            ) AS (
                SELECT %(target_scope_type)s::text,
                       %(target_scope_id)s::uuid,
                       true
                UNION
                SELECT hierarchy.parent_scope_type,
                       hierarchy.parent_scope_id,
                       CASE
                           WHEN hierarchy.parent_scope_type = 'project'
                           THEN ancestors.project_grant_allowed AND hierarchy.roll_down
                           ELSE ancestors.project_grant_allowed
                       END
                  FROM authorization_scope AS hierarchy
                  JOIN ancestors
                    ON hierarchy.scope_type = ancestors.scope_type
                   AND hierarchy.scope_id = ancestors.scope_id
                 WHERE hierarchy.org_id = %(org_id)s
                   AND hierarchy.parent_scope_type IS NOT NULL
            ), eligible_grants AS (
                SELECT assignment.role_id, assignment.user_id
                 FROM user_role AS assignment
                 WHERE assignment.org_id = %(org_id)s
                   AND (%(user_id)s::uuid IS NULL OR assignment.user_id = %(user_id)s)
                   AND (
                       assignment.effective_from IS NULL
                       OR assignment.effective_from <= CURRENT_TIMESTAMP
                   )
                   AND (
                       (
                           assignment.scope_type = 'org'
                           AND assignment.scope_id = %(org_id)s
                       )
                       OR EXISTS (
                           SELECT 1
                             FROM ancestors
                            WHERE ancestors.scope_type = assignment.scope_type
                              AND ancestors.scope_id = assignment.scope_id
                              AND (
                                  assignment.scope_type <> 'project'
                                  OR ancestors.project_grant_allowed
                              )
                       )
                   )
            )
    """


class AuthorizationResolver(ScopedRepo[AuthorizationDecision]):
    """Resolve every request directly from current grants without a permission cache."""

    def __init__(self, connection: AuthorizationConnection, scope: Scope) -> None:
        super().__init__(connection, scope)
        self._connection = connection

    def check(
        self,
        user_id: IdentityId,
        permission: str,
        target: TargetProtocol,
    ) -> DecisionProtocol:
        """Return default-deny scope and action facts for one request.

        The recursive CTE always walks the whole project chain so a BU grant
        continues to contain its projects. A project ancestor is eligible only
        while every traversed project edge permits roll-down.
        """

        checked_permission = permission_code(permission)
        context = AuthorizationContext(
            user_id, checked_permission, cast(AuthorizationTarget, target)
        )
        if target.scope_type is ScopeType.ORG and target.scope_id != self.scope.org_id:
            return cast(
                DecisionProtocol, AuthorizationDecision(context, scope_matched=False, allowed=False)
            )

        row = self._connection.execute(
            _scope_cte()
            + """
            SELECT EXISTS (SELECT 1 FROM eligible_grants
                           WHERE user_id = %(user_id)s) AS scope_matched,
                   EXISTS (
                       SELECT 1
                         FROM eligible_grants
                         JOIN role_permission
                           ON role_permission.org_id = %(org_id)s
                          AND role_permission.role_id = eligible_grants.role_id
                        WHERE eligible_grants.user_id = %(user_id)s
                          AND role_permission.permission_code = %(permission)s
                   ) AS allowed
            """,
            self.scoped_params(
                {
                    "user_id": user_id,
                    "permission": checked_permission,
                    "target_scope_type": cast(ScopeType, target.scope_type).value,
                    "target_scope_id": target.scope_id,
                }
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("authorization query did not return a decision")
        return cast(
            DecisionProtocol,
            AuthorizationDecision(
                context=context,
                scope_matched=cast(bool, _value(row, 0, "scope_matched")),
                allowed=cast(bool, _value(row, 1, "allowed")),
            ),
        )

    def granted_scopes(self, user_id: IdentityId, permission: str) -> GrantedScopes:
        """Scopes where the user's current assignments carry the permission."""

        rows = self._connection.execute(
            """
            SELECT DISTINCT assignment.scope_type, assignment.scope_id
              FROM user_role AS assignment
              JOIN role_permission
                ON role_permission.org_id = assignment.org_id
               AND role_permission.role_id = assignment.role_id
             WHERE assignment.org_id = %(org_id)s
               AND assignment.user_id = %(user_id)s
               AND role_permission.permission_code = %(permission)s
               AND (assignment.effective_from IS NULL
                    OR assignment.effective_from <= CURRENT_TIMESTAMP)
            """,
            self.scoped_params({"user_id": user_id, "permission": permission_code(permission)}),
        ).fetchall()
        found = [
            (str(_value(r, 0, "scope_type")), cast(UUID, _value(r, 1, "scope_id"))) for r in rows
        ]
        return GrantedScopes(
            user_id=user_id,
            org_wide=any(kind == ScopeType.ORG.value for kind, _ in found),
            bu_ids=frozenset(i for kind, i in found if kind == ScopeType.BU.value),
            project_ids=frozenset(i for kind, i in found if kind == ScopeType.PROJECT.value),
        )

    def holders(self, permission: str, target: TargetProtocol) -> list[IdentityId]:
        return self._holders(target, permission=permission_code(permission))

    def role_holders(self, role_code: str, target: TargetProtocol) -> list[IdentityId]:
        return self._holders(target, role_code=role_code)

    def _holders(
        self, target: TargetProtocol, *, permission: str | None = None, role_code: str | None = None
    ) -> list[IdentityId]:
        if target.scope_type is ScopeType.ORG and target.scope_id != self.scope.org_id:
            return []
        # ponytail: approval groups are capped at 1000; larger groups need a redesigned step.
        rows = self._connection.execute(
            _scope_cte()
            + """
            SELECT DISTINCT identity.id FROM eligible_grants
            JOIN identity ON identity.id = eligible_grants.user_id AND identity.status = 'active'
            JOIN role ON role.org_id = %(org_id)s AND role.id = eligible_grants.role_id
            WHERE (%(role_code)s::text IS NULL OR role.code = %(role_code)s)
              AND (%(permission)s::text IS NULL OR EXISTS (
                  SELECT 1 FROM role_permission
                  WHERE role_permission.org_id = %(org_id)s
                    AND role_permission.role_id = eligible_grants.role_id
                    AND role_permission.permission_code = %(permission)s))
            ORDER BY identity.id LIMIT 1000
            """,
            self.scoped_params(
                {
                    "permission": permission,
                    "role_code": role_code,
                    "user_id": None,
                    "target_scope_type": cast(ScopeType, target.scope_type).value,
                    "target_scope_id": target.scope_id,
                }
            ),
        ).fetchall()
        return [IdentityId(cast(UUID, _value(row, 0, "id"))) for row in rows]

    def require(
        self,
        user_id: IdentityId,
        permission: str,
        target: TargetProtocol,
    ) -> AuthorizationContext:
        """Return the check context only when an explicit contained grant allows it."""

        decision = self.check(user_id, permission, target)
        if not decision.allowed:
            raise PermissionError("permission denied")
        return cast(AuthorizationContext, decision.context)
