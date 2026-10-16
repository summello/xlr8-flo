"""Closed RBAC vocabulary and request authorization context."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID

from flo.kernel.identity import IdentityId

PermissionCode = NewType("PermissionCode", str)
RoleCode = NewType("RoleCode", str)

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_ROLE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:-[a-z0-9_]+)*$")


class ScopeType(StrEnum):
    """The only scopes to which a role can be granted."""

    ORG = "org"
    BU = "bu"
    PROJECT = "project"


@dataclass(frozen=True, slots=True)
class Role:
    """One organization-owned collection of explicit permissions."""

    id: UUID
    code: RoleCode
    name: str
    is_system: bool


@dataclass(frozen=True, slots=True)
class UserRole:
    """A role assignment at exactly one organization, BU, or project scope."""

    id: UUID
    user_id: IdentityId
    role_id: UUID
    scope_type: ScopeType
    scope_id: UUID
    granted_by: IdentityId | None
    granted_at: datetime
    effective_from: datetime | None


@dataclass(frozen=True, slots=True)
class AuthorizationTarget:
    """Server-resolved location and record facts used by one permission check.

    ``org_id`` is deliberately absent. The resolver receives it only through the
    authenticated request ``Scope``. ``created_by`` is carried now so approval
    separation-of-duty can consume it without changing this contract later.
    """

    scope_type: ScopeType
    scope_id: UUID
    record_type: str | None = None
    record_id: UUID | None = None
    created_by: IdentityId | None = None

    def __post_init__(self) -> None:
        if (self.record_type is None) != (self.record_id is None):
            raise ValueError("record type and record id must be supplied together")
        if self.record_type is not None and not self.record_type.strip():
            raise ValueError("record type must be non-empty")

    @classmethod
    def organization(cls, org_id: UUID) -> AuthorizationTarget:
        """Build the organization target from the authenticated scope only."""

        return cls(ScopeType.ORG, org_id)


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    """Facts behind a decision, including the future separation-of-duty input."""

    user_id: IdentityId
    permission: PermissionCode
    target: AuthorizationTarget

    @property
    def created_by(self) -> IdentityId | None:
        """Expose record authorship without enforcing approval policy yet."""

        return self.target.created_by


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Default-deny result separating scope concealment from action denial."""

    context: AuthorizationContext
    scope_matched: bool
    allowed: bool


def permission_code(value: str) -> PermissionCode:
    """Validate the stable ``module.action`` permission vocabulary."""

    if _CODE_PATTERN.fullmatch(value) is None:
        raise ValueError("permission codes must use module.action lower-case syntax")
    return PermissionCode(value)


def role_code(value: str) -> RoleCode:
    """Validate stable lower-case role identifiers."""

    if _ROLE_CODE_PATTERN.fullmatch(value) is None:
        raise ValueError("role codes must be lower-case words separated by hyphens")
    return RoleCode(value)


PERMISSION_CODES: tuple[PermissionCode, ...] = tuple(
    permission_code(value)
    for value in (
        "admin.access.read",
        "approval.admin",
        "approval.delegate",
        "asset.create",
        "asset.edit",
        "asset.view",
        "audit.read",
        "budget.approve",
        "budget.create",
        "budget.edit",
        "budget.transfer",
        "budget.view",
        "organization.admin",
        "organization.view",
        "project.admin",
        "project.approve",
        "project.create",
        "purchase_order.amend",
        "purchase_order.approve",
        "purchase_order.close",
        "purchase_order.create",
        "purchase_order.issue",
        "purchase_order.view",
        "record.read",
        "report.view",
        "requisition.approve",
        "requisition.submit",
        "requisition.withdraw",
        "requisition.cancel",
        "requisition.create",
        "requisition.edit",
        "requisition.view",
        "rfq.approve",
        "rfq.create",
        "rfq.edit",
        "rfq.view",
        "vendor.approve",
        "vendor.create",
        "vendor.edit",
        "vendor.request",
        "vendor.view",
        "view.archive",
        "workflow.read",
        "workflow.manage",
        "budget.adjust",
        "budget.allocate",
        "fiscal.close",
        "fiscal.manage",
        "fiscal.read",
        "fiscal.reopen",
        "fx.read",
        "import.read",
        "import.run",
        "ledger.read",
        "master.manage",
        "master.read",
        "master_code.substitute",
        "org.setting.manage",
        "org.unit.manage",
        "org.unit.read",
        "project.close.override",
        "project.complete",
        "project.read",
        "project.submit",
        "project.update",
        "project_request.create",
    )
)

BASELINE_ROLES: tuple[tuple[RoleCode, str], ...] = tuple(
    (role_code(code), name)
    for code, name in (
        ("organization-administrator", "Organization Administrator"),
        ("project-administrator", "Project Administrator"),
        ("project-manager", "Project Manager"),
        ("requestor", "Requestor"),
        ("approver", "Approver"),
        ("sourcing-specialist", "Sourcing Specialist"),
        ("purchasing-user", "Purchasing User"),
        ("vendor-manager", "Vendor Manager"),
        ("budget-finance-user", "Budget/Finance User"),
        ("warehouse-asset-user", "Warehouse/Asset User"),
        ("executive-viewer", "Executive/Viewer"),
        ("auditor", "Auditor"),
    )
)

# Authentication consumes only this closed classification. Tenant-owned role and
# scope details never cross into the pre-tenant credential boundary.
MFA_REQUIRED_ROLE_CODES: frozenset[RoleCode] = frozenset(
    {
        role_code("organization-administrator"),
        role_code("auditor"),
    }
)

_ALL_PERMISSIONS = frozenset(PERMISSION_CODES)
_READ_ONLY_PERMISSIONS = frozenset(
    permission
    for permission in PERMISSION_CODES
    if permission.rsplit(".", 1)[-1] in {"read", "view"}
)

BASELINE_ROLE_PERMISSIONS: dict[RoleCode, frozenset[PermissionCode]] = {
    role_code("organization-administrator"): _ALL_PERMISSIONS,
    role_code("project-administrator"): frozenset(
        permission_code(value)
        for value in (
            "vendor.request",
            "budget.view",
            "project.admin",
            "project.approve",
            "project.create",
            "project.read",
            "project.update",
            "project.submit",
            "project.complete",
            "project.close.override",
            "budget.allocate",
            "ledger.read",
            "master.read",
            "record.read",
            "report.view",
            "view.archive",
        )
    ),
    role_code("project-manager"): frozenset(
        permission_code(value)
        for value in (
            "vendor.request",
            "project.read",
            "project.create",
            "project.update",
            "project.submit",
            "project.complete",
            "requisition.view",
            "requisition.create",
            "requisition.edit",
            "requisition.submit",
            "requisition.withdraw",
            "project_request.create",
            "master_code.substitute",
            "budget.view",
            "ledger.read",
            "master.read",
            "record.read",
            "report.view",
        )
    ),
    role_code("requestor"): frozenset(
        permission_code(value)
        for value in (
            "vendor.request",
            "project.read",
            "record.read",
            "requisition.submit",
            "requisition.withdraw",
            "requisition.create",
            "requisition.edit",
            "requisition.view",
            "view.archive",
        )
    ),
    role_code("approver"): frozenset(
        permission_code(value)
        for value in (
            "vendor.request",
            "approval.delegate",
            "project.read",
            "record.read",
            "requisition.approve",
            "requisition.view",
        )
    ),
    role_code("sourcing-specialist"): frozenset(
        permission_code(value)
        for value in (
            "vendor.request",
            "record.read",
            "requisition.view",
            "rfq.approve",
            "rfq.create",
            "rfq.edit",
            "rfq.view",
            "vendor.view",
        )
    ),
    role_code("purchasing-user"): frozenset(
        permission_code(value)
        for value in (
            "vendor.request",
            "purchase_order.amend",
            "purchase_order.approve",
            "purchase_order.close",
            "purchase_order.create",
            "purchase_order.issue",
            "purchase_order.view",
            "record.read",
            "requisition.view",
            "rfq.view",
        )
    ),
    role_code("vendor-manager"): frozenset(
        permission_code(value)
        for value in (
            "vendor.request",
            "record.read",
            "vendor.approve",
            "vendor.create",
            "vendor.edit",
            "vendor.view",
        )
    ),
    role_code("budget-finance-user"): frozenset(
        permission_code(value)
        for value in (
            "vendor.request",
            "budget.approve",
            "budget.create",
            "budget.edit",
            "budget.adjust",
            "budget.allocate",
            "budget.transfer",
            "budget.view",
            "fiscal.read",
            "fx.read",
            "ledger.read",
            "record.read",
            "report.view",
        )
    ),
    role_code("warehouse-asset-user"): frozenset(
        permission_code(value)
        for value in (
            "vendor.request",
            "asset.create",
            "asset.edit",
            "asset.view",
            "record.read",
        )
    ),
    role_code("executive-viewer"): frozenset(
        permission_code(value)
        for value in (
            "vendor.request",
            "budget.view",
            "organization.view",
            "record.read",
            "report.view",
        )
    ),
    role_code("auditor"): _READ_ONLY_PERMISSIONS,
}
