"""Uncached, recursive effective-permission resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from psycopg import sql

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


class AuthorizationResolver(ScopedRepo[AuthorizationDecision]):
    """Resolve every request directly from current grants without a permission cache."""

    def __init__(self, connection: AuthorizationConnection, scope: Scope) -> None:
        super().__init__(connection, scope)
        self._connection = connection

    def check(
        self,
        user_id: IdentityId,
        permission: str,
        target: AuthorizationTarget,
    ) -> AuthorizationDecision:
        """Return default-deny scope and action facts for one request.

        The recursive CTE always walks the whole project chain so a BU grant
        continues to contain its projects. A project ancestor is eligible only
        while every traversed project edge permits roll-down.
        """

        checked_permission = permission_code(permission)
        context = AuthorizationContext(user_id, checked_permission, target)
        if target.scope_type is ScopeType.ORG and target.scope_id != self.scope.org_id:
            return AuthorizationDecision(context, scope_matched=False, allowed=False)

        row = self._connection.execute(
            """
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
                SELECT assignment.role_id
                  FROM user_role AS assignment
                 WHERE assignment.org_id = %(org_id)s
                   AND assignment.user_id = %(user_id)s
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
            SELECT EXISTS (SELECT 1 FROM eligible_grants) AS scope_matched,
                   EXISTS (
                       SELECT 1
                         FROM eligible_grants
                         JOIN role_permission
                           ON role_permission.org_id = %(org_id)s
                          AND role_permission.role_id = eligible_grants.role_id
                        WHERE role_permission.permission_code = %(permission)s
                   ) AS allowed
            """,
            self.scoped_params(
                {
                    "user_id": user_id,
                    "permission": checked_permission,
                    "target_scope_type": target.scope_type.value,
                    "target_scope_id": target.scope_id,
                }
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("authorization query did not return a decision")
        return AuthorizationDecision(
            context=context,
            scope_matched=cast(bool, _value(row, 0, "scope_matched")),
            allowed=cast(bool, _value(row, 1, "allowed")),
        )

    def require(
        self,
        user_id: IdentityId,
        permission: str,
        target: AuthorizationTarget,
    ) -> AuthorizationContext:
        """Return the check context only when an explicit contained grant allows it."""

        decision = self.check(user_id, permission, target)
        if not decision.allowed:
            raise PermissionError("permission denied")
        return decision.context
