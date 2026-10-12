"""Live approval membership, with document-specific permissions and separation of duty."""

from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import PermissionHoldersResolver
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.conditions import approve_permission
from flo.modules.approvals.models import WorkflowRepository
from flo.modules.approvals.schemas import Approvers, Contract, RoleApprovers, UserApprovers
from flo.modules.identity.service import (
    AuthorizationTarget,
    ScopeType,
)
from flo.modules.org.service import OrgService


class Eligible(Contract):
    user_id: UUID
    basis: str


class Excluded(Contract):
    user_id: UUID
    reason: str


class EligibleSet(Contract):
    members: list[Eligible]
    excluded: list[Excluded]


class PickerRow(Contract):
    user_id: UUID
    email: str


class PickerPage(Contract):
    rows: list[PickerRow]


def eligible_approvers(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    rule: Approvers,
    target: AuthorizationTarget,
    requester_id: UUID,
    resolver: PermissionHoldersResolver,
) -> EligibleSet:
    with tenant_transaction(cast(RlsSession, conn), scope):
        permission = approve_permission(document_type)
        holders = set(resolver.holders(permission, cast(TargetProtocol, target)))
        excluded: list[Excluded] = []
        if isinstance(rule, UserApprovers):
            selected = holders & set(rule.user_ids)
            # Foreign/deactivated identities must not be disclosed even as exclusions.
            active = {
                cast(UUID, row[0])
                for row in WorkflowRepository(conn, scope)
                .execute(
                    "SELECT DISTINCT i.id FROM identity i JOIN user_role u ON u.user_id=i.id "
                    "WHERE u.org_id=%(org_id)s AND i.status='active'"
                )
                .fetchall()
            }
            excluded = [
                Excluded(user_id=id, reason="not_eligible_in_scope")
                for id in sorted(set(rule.user_ids) - holders)
                if id in active
            ]
            basis = "users"
        elif isinstance(rule, RoleApprovers):
            selected = holders & set(
                resolver.role_holders(rule.role_code, cast(TargetProtocol, target))
            )
            basis = f"role:{rule.role_code}"
        else:
            selected = holders
            basis = f"permission:{permission}"
        repo = WorkflowRepository(conn, scope)
        if target.scope_type is ScopeType.ORG:
            row = repo.execute(
                "SELECT value FROM org_setting WHERE org_id=%(org_id)s AND unit_id IS NULL "
                "AND key='approval.separation_of_duty'"
            ).fetchone()
            separation = row[0] if row else True
        else:
            unit_id = target.scope_id
            if target.scope_type is ScopeType.PROJECT:
                row = repo.execute(
                    "WITH RECURSIVE ancestors AS (SELECT scope_type,scope_id,parent_scope_type,"
                    "parent_scope_id FROM authorization_scope WHERE org_id=%(org_id)s "
                    "AND scope_type='project' AND scope_id=%(id)s UNION ALL "
                    "SELECT s.scope_type,s.scope_id,s.parent_scope_type,s.parent_scope_id "
                    "FROM authorization_scope s JOIN ancestors a ON "
                    "s.scope_type=a.parent_scope_type AND s.scope_id=a.parent_scope_id "
                    "WHERE s.org_id=%(org_id)s) SELECT scope_id FROM ancestors "
                    "WHERE scope_type='bu' LIMIT 1",
                    {"id": target.scope_id},
                ).fetchone()
                if row is None:
                    from flo.kernel.errors import ErrorCode, ProblemError

                    raise ProblemError(ErrorCode.NOT_FOUND)
                unit_id = cast(UUID, row[0])
            separation = (
                OrgService(conn, scope)
                .effective_setting(unit_id, "approval.separation_of_duty")
                .value
            )
        if separation and requester_id in selected:
            selected.remove(IdentityId(requester_id))
            excluded.append(Excluded(user_id=requester_id, reason="separation_of_duty"))
        scope_label = f"{target.scope_type.value}:{target.scope_id}"
        return EligibleSet(
            members=[
                Eligible(user_id=id, basis=f"{basis}@{scope_label}") for id in sorted(selected)
            ],
            excluded=sorted(excluded, key=lambda entry: entry.user_id),
        )


def approver_picker(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    target: AuthorizationTarget,
    q: str | None,
    resolver: PermissionHoldersResolver,
) -> PickerPage:
    with tenant_transaction(cast(RlsSession, conn), scope):
        ids = resolver.holders(approve_permission(document_type), cast(TargetProtocol, target))
        rows = (
            WorkflowRepository(conn, scope)
            .execute(
                "SELECT i.id,i.email FROM identity i WHERE i.status='active' AND i.id=ANY(%(ids)s) "
                "AND EXISTS (SELECT 1 FROM user_role u "
                "WHERE u.org_id=%(org_id)s AND u.user_id=i.id) "
                "AND (%(q)s::text IS NULL OR starts_with(lower(i.email),lower(%(q)s))) "
                "ORDER BY i.email,i.id LIMIT 50",
                {"ids": ids, "q": q},
            )
            .fetchall()
        )
        return PickerPage(rows=[PickerRow(user_id=cast(UUID, r[0]), email=str(r[1])) for r in rows])
