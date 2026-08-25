from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest

from flo.modules.identity.models import (
    BASELINE_ROLE_PERMISSIONS,
    BASELINE_ROLES,
    MFA_REQUIRED_ROLE_CODES,
    PERMISSION_CODES,
    AuthorizationTarget,
    ScopeType,
    permission_code,
    role_code,
)

EXPECTED_ROLES = {
    "approver": "Approver",
    "auditor": "Auditor",
    "budget-finance-user": "Budget/Finance User",
    "executive-viewer": "Executive/Viewer",
    "organization-administrator": "Organization Administrator",
    "project-administrator": "Project Administrator",
    "purchasing-user": "Purchasing User",
    "requestor": "Requestor",
    "sourcing-specialist": "Sourcing Specialist",
    "vendor-manager": "Vendor Manager",
    "warehouse-asset-user": "Warehouse/Asset User",
}

EXPECTED_PERMISSIONS = {
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
    "project.edit",
    "project.view",
    "purchase_order.amend",
    "purchase_order.approve",
    "purchase_order.close",
    "purchase_order.create",
    "purchase_order.issue",
    "purchase_order.view",
    "record.read",
    "report.view",
    "requisition.approve",
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
    "vendor.view",
    "view.archive",
}

ROOT = Path(__file__).resolve().parents[4]


def test_baseline_role_and_permission_catalogs_are_complete_by_name() -> None:
    assert {str(code): name for code, name in BASELINE_ROLES} == EXPECTED_ROLES
    assert {str(permission) for permission in PERMISSION_CODES} == EXPECTED_PERMISSIONS
    assert set(BASELINE_ROLE_PERMISSIONS) == {code for code, _ in BASELINE_ROLES}
    assert all(BASELINE_ROLE_PERMISSIONS[code] for code, _ in BASELINE_ROLES)
    required_mfa = {str(code) for code in MFA_REQUIRED_ROLE_CODES}
    expected_mfa = {"organization-administrator", "auditor"}
    assert required_mfa == expected_mfa, (
        f"MISSING privileged MFA roles: {sorted(expected_mfa - required_mfa)}"
    )


def test_every_command_palette_permission_exists_in_the_server_catalog() -> None:
    registry = (
        ROOT / "apps" / "web" / "src" / "components" / "command" / "registry.ts"
    ).read_text(encoding="utf-8")
    declared = set(re.findall(r'requiredPermissions: \["([a-z0-9_.]+)"\]', registry))
    assert declared == {
        "organization.admin",
        "project.create",
        "record.read",
        "view.archive",
    }
    assert declared <= {str(permission) for permission in PERMISSION_CODES}


def test_auditor_permissions_are_read_only_by_construction() -> None:
    auditor = BASELINE_ROLE_PERMISSIONS[role_code("auditor")]
    assert auditor == {
        permission_code(permission)
        for permission in EXPECTED_PERMISSIONS
        if permission.rsplit(".", 1)[-1] in {"read", "view"}
    }
    mutating_suffixes = (".create", ".edit", ".approve", ".delete")
    assert not [permission for permission in auditor if permission.endswith(mutating_suffixes)]


@pytest.mark.parametrize("value", ["project:*", "Project.view", "view", "project..view"])
def test_permission_codes_fail_closed_on_noncanonical_values(value: str) -> None:
    with pytest.raises(ValueError, match="module.action"):
        permission_code(value)


def test_authorization_target_requires_complete_record_context() -> None:
    with pytest.raises(ValueError, match="supplied together"):
        AuthorizationTarget(ScopeType.PROJECT, uuid4(), record_type="requisition")
