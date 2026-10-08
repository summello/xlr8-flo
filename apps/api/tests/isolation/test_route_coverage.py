"""TEN-010: every route that takes a resource id has a named foreign-tenant/owner 404 case."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter

from flo.api.admin_users import router as admin_users_router
from flo.api.auth import router as auth_router
from flo.api.budget import router as budget_router
from flo.api.fiscal import router as fiscal_router
from flo.api.imports import router as imports_router
from flo.api.master import router as master_router
from flo.api.org import router as org_router
from flo.api.projects import router as projects_router

TESTS = Path(__file__).resolve().parents[1]

# (method, path) -> "file::test" that proves a foreign id returns 404.
COVERED = {
    (
        "GET",
        "/api/v1/imports/templates",
    ): "imports/test_upload.py::test_template_collection_and_download_are_guarded",
    (
        "GET",
        "/api/v1/imports/templates/{name}/file",
    ): "imports/test_upload.py::test_template_collection_and_download_are_guarded",
    ("POST", "/api/v1/imports"): "imports/test_upload.py::test_upload_uses_session_tenant",
    ("GET", "/api/v1/imports/{id}"): "imports/test_upload.py::test_get_foreign_batch",
    ("PUT", "/api/v1/imports/{id}/mapping"): "imports/test_upload.py::test_put_foreign_batch",
    (
        "GET",
        "/api/v1/budget/reconciliation/status",
    ): "budget/test_reconcile.py::test_reconciliation_status_tenant_isolation",
    (
        "GET",
        "/api/v1/budget/reconciliation/drift",
    ): "budget/test_reconcile.py::test_foreign_reconciliation_drift",
    (
        "POST",
        "/internal/jobs/budget-reconcile",
    ): "budget/test_reconcile.py::test_internal_trigger_rejects_tenant_session",
    ("GET", "/api/v1/projects/{project_id}/balance/aggregate"): (
        "budget/test_funding_modes.py::test_foreign_aggregate"
    ),
    (
        "POST",
        "/api/v1/projects/{id}/transitions",
    ): "projects/test_lifecycle.py::test_transition_foreign_project",
    (
        "GET",
        "/api/v1/projects/{id}/transitions/available",
    ): "projects/test_lifecycle.py::test_available_foreign_project",
    (
        "POST",
        "/api/v1/budget/transfers",
    ): "budget/test_transfer_same_level.py::test_foreign_transfer",
    (
        "GET",
        "/api/v1/projects/{project_id}/balance",
    ): "budget/test_balance_queries.py::test_foreign_balance",
    (
        "GET",
        "/api/v1/projects/{project_id}/balance/reconcile",
    ): "budget/test_balance_queries.py::test_foreign_reconcile",
    (
        "GET",
        "/api/v1/projects/{project_id}/ledger",
    ): "budget/test_balance_queries.py::test_foreign_ledger",
    (
        "POST",
        "/api/v1/projects/{project_id}/budget/allocations",
    ): "budget/test_allocation.py::test_foreign_allocation",
    (
        "POST",
        "/api/v1/projects/{project_id}/budget/adjustments",
    ): "budget/test_allocation.py::test_foreign_adjustment",
    (
        "GET",
        "/api/v1/projects/{id}/children",
    ): "projects/test_hierarchy.py::test_children_foreign_project",
    ("GET", "/api/v1/projects/{id}/tree"): "projects/test_hierarchy.py::test_tree_foreign_project",
    ("POST", "/api/v1/projects"): "projects/test_projects.py::test_create_foreign_bu",
    ("GET", "/api/v1/projects"): "projects/test_projects.py::test_list_tenant_isolation",
    ("GET", "/api/v1/projects/{id}"): "projects/test_projects.py::test_get_foreign_project",
    ("PATCH", "/api/v1/projects/{id}"): "projects/test_projects.py::test_patch_foreign_project",
    ("PUT", "/api/v1/fiscal/calendar"): "org/test_fiscal.py::test_collection_tenant_isolation",
    ("GET", "/api/v1/fiscal/periods"): "org/test_fiscal.py::test_collection_tenant_isolation",
    (
        "POST",
        "/api/v1/fiscal/years/{fiscal_year}:generate",
    ): "org/test_fiscal.py::test_collection_tenant_isolation",
    ("POST", "/api/v1/fiscal/periods/{id}:close"): "org/test_fiscal.py::test_foreign_period_id",
    ("POST", "/api/v1/fiscal/periods/{id}:reopen"): "org/test_fiscal.py::test_foreign_period_id",
    ("GET", "/api/v1/master/currency"): "org/test_master.py::test_collection_tenant_isolation",
    ("GET", "/api/v1/master/{kind}"): "org/test_master.py::test_collection_tenant_isolation",
    ("POST", "/api/v1/master/{kind}"): "org/test_master.py::test_collection_tenant_isolation",
    ("PATCH", "/api/v1/master/{kind}/{id}"): "org/test_master.py::test_foreign_master_id",
    ("POST", "/api/v1/master/{kind}/{id}:deactivate"): "org/test_master.py::test_foreign_master_id",
    (
        "POST",
        "/api/v1/org/units/{unit_id}/addresses",
    ): "org/test_addresses.py::test_foreign_unit_addresses",
    (
        "GET",
        "/api/v1/org/units/{unit_id}/addresses",
    ): "org/test_addresses.py::test_foreign_unit_addresses",
    (
        "PATCH",
        "/api/v1/org/units/{unit_id}/addresses/{id}",
    ): "org/test_addresses.py::test_foreign_address_id",
    ("POST", "/api/v1/org/units"): "org/test_units.py::test_create_foreign_parent",
    ("GET", "/api/v1/org/units"): "org/test_units.py::test_list_tenant_filter_and_cursor",
    ("GET", "/api/v1/org/units/{id}"): "org/test_units.py::test_get_foreign_unit",
    ("PATCH", "/api/v1/org/units/{id}"): "org/test_units.py::test_patch_foreign_unit",
    ("PUT", "/api/v1/org/settings/{key}"): "org/test_settings.py::test_put_foreign_unit",
    ("DELETE", "/api/v1/org/settings/{key}"): "org/test_settings.py::test_delete_foreign_unit",
    ("GET", "/api/v1/org/units/{id}/settings/{key}/effective"): (
        "org/test_settings.py::test_effective_foreign_unit"
    ),
    ("GET", "/api/v1/admin/users/{user_id}/effective-access"): (
        "authz/test_effective_access.py::test_effective_access_conceals_a_foreign_tenant_subject"
    ),
    ("GET", "/api/v1/admin/users/{user_id}/effective-access/permissions"): (
        "authz/test_effective_access.py::test_permissions_conceals_a_foreign_tenant_subject"
    ),
    ("POST", "/api/v1/admin/users/{user_id}/deactivate"): (
        "authz/test_effective_access.py::test_deactivate_conceals_a_foreign_tenant_subject"
    ),
    ("DELETE", "/api/v1/admin/users/{user_id}/roles/{grant_id}"): (
        "authz/test_effective_access.py::test_fresh_step_up_allows_subject_bound_revoke"
    ),
    ("DELETE", "/api/v1/auth/sessions/{session_id}"): (
        "session/test_http.py::test_unknown_or_foreign_session_id_is_not_found_and_logout_clears_cookies"
    ),
}


def id_routes(*routers: APIRouter) -> set[tuple[str, str]]:
    return {
        (method, route.path)  # type: ignore[attr-defined]
        for router in routers
        for route in router.routes
        if "{" in route.path  # type: ignore[attr-defined]
        for method in route.methods  # type: ignore[attr-defined]
    }


def missing_cases(routes: set[tuple[str, str]], covered: dict[tuple[str, str], str]) -> list[str]:
    problems = [
        f"no isolation case: {method} {path}" for method, path in sorted(routes - covered.keys())
    ]
    for target in covered.values():
        file, name = target.split("::")
        if not re.search(rf"^def {name}\(", (TESTS / file).read_text(), re.MULTILINE):
            problems.append(f"covering test not found: {target}")
    return problems


def test_every_id_route_has_a_named_foreign_tenant_case() -> None:
    assert (
        missing_cases(
            id_routes(
                admin_users_router,
                auth_router,
                org_router,
                master_router,
                fiscal_router,
                projects_router,
                budget_router,
                imports_router,
            ),
            COVERED,
        )
        == []
    )


def test_gate_fails_on_an_uncovered_route_and_on_a_dangling_reference() -> None:
    dangling = {("GET", "/api/v1/admin/users/{user_id}/new"): "authz/test_enforcement.py::nope"}
    extra = COVERED | dangling
    widget = {("GET", "/api/v1/widgets/{widget_id}")}
    routes = (
        id_routes(
            admin_users_router,
            auth_router,
            org_router,
            master_router,
            fiscal_router,
            projects_router,
            budget_router,
            imports_router,
        )
        | widget
    )
    problems = missing_cases(routes, extra)
    assert "no isolation case: GET /api/v1/widgets/{widget_id}" in problems
    assert any("covering test not found" in problem for problem in problems)
