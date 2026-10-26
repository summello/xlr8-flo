"""TEN-010: every route that takes a resource id has a named foreign-tenant/owner 404 case."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter

from flo.api.admin_users import router as admin_users_router
from flo.api.approvals import router as approvals_router
from flo.api.auth import router as auth_router
from flo.api.budget import router as budget_router
from flo.api.collaboration import router as collaboration_router
from flo.api.fiscal import router as fiscal_router
from flo.api.imports import router as imports_router
from flo.api.invitations import router as invitations_router
from flo.api.master import router as master_router
from flo.api.org import router as org_router
from flo.api.projects import router as projects_router
from flo.api.requisitions import router as requisitions_router
from flo.api.sourcing import router as sourcing_router
from flo.api.vendor_questionnaires import router as vendor_questionnaires_router
from flo.api.vendors import router as vendors_router
from flo.api.workflows import router as workflows_router

TESTS = Path(__file__).resolve().parents[1]

# (method, path) -> "file::test" that proves a foreign id returns 404.
AMENDMENT_ROUTES = (
    "requisitions/test_amendments.py::"
    "test_routes_conceal_foreign_tenants_and_refuse_view_only_users"
)
CODES_ROUTES = (
    "requisitions/test_expired_codes.py::"
    "test_codes_routes_conceal_foreign_tenants_and_need_the_permission"
)
SOURCING_ROUTES = "sourcing/test_rfq.py::test_rfq_routes_conceal_foreign_tenants"
CONTENT_ROUTES = "sourcing/test_rfq_content.py::test_rfq_content_routes_conceal_foreign_tenants"
SCREENS_ROUTES = "sourcing/test_rfq_screens.py::test_screens_routes_conceal_foreign_tenants"
LIFECYCLE_ROUTES = "sourcing/test_rfq_lifecycle.py::test_lifecycle_routes_conceal_foreign_tenants"
BID_ROUTES = "sourcing/test_bids.py::test_bid_routes_conceal_foreign_tenants"
COMPARISON_ROUTES = "sourcing/test_comparison.py::test_comparison_routes_conceal_foreign_tenants"
EVALUATION_ROUTES = "sourcing/test_evaluation.py::test_evaluation_routes_conceal_foreign_tenants"
QROUTES = "vendors/test_questionnaire.py::test_questionnaire_routes_conceal_foreign_tenants"
OROUTES = (
    "vendors/test_questionnaire.py::"
    "test_onboarding_routes_conceal_foreign_tenants_and_other_requesters"
)
PROUTES = "vendors/test_performance.py::test_score_routes_conceal_foreign_tenants"
CROUTES = "vendors/test_changes.py::test_vendor_change_routes_conceal_foreign_tenants"
LISTDOCS = (
    "vendors/test_list_documents.py::"
    "test_counts_and_documents_filter_routes_conceal_foreign_tenants"
)
COVERED = {
    ("GET", "/api/v1/vendors/counts"): LISTDOCS,
    ("GET", "/api/v1/vendors/duplicate-check"): CROUTES,
    ("POST", "/api/v1/vendors/{id}/submit"): CROUTES,
    ("POST", "/api/v1/vendors/{id}/revise"): CROUTES,
    ("POST", "/api/v1/vendors/{id}/changes"): CROUTES,
    ("GET", "/api/v1/vendors/{id}/changes"): CROUTES,
    ("POST", "/api/v1/vendors/{id}/changes/{cid}/withdraw"): CROUTES,
    ("POST", "/api/v1/vendors/{id}/inactivate"): CROUTES,
    ("GET", "/api/v1/vendors/{id}/scores"): PROUTES,
    ("POST", "/api/v1/vendors/{id}/scores"): PROUTES,
    ("POST", "/api/v1/vendors/{id}/scores/{sid}/disputes"): PROUTES,
    ("POST", "/api/v1/vendors/{id}/scores/{sid}/disputes/{did}/resolve"): PROUTES,
    ("GET", "/api/v1/vendors/{id}/performance"): PROUTES,
    ("GET", "/api/v1/admin/vendor-questionnaires/{vid}"): QROUTES,
    ("PATCH", "/api/v1/admin/vendor-questionnaires/{vid}"): QROUTES,
    ("POST", "/api/v1/admin/vendor-questionnaires/{vid}/questions"): QROUTES,
    ("PATCH", "/api/v1/admin/vendor-questionnaires/{vid}/questions/{key}"): QROUTES,
    ("DELETE", "/api/v1/admin/vendor-questionnaires/{vid}/questions/{key}"): QROUTES,
    ("POST", "/api/v1/admin/vendor-questionnaires/{vid}/questions/{key}/move"): QROUTES,
    ("POST", "/api/v1/admin/vendor-questionnaires/{vid}/publish"): QROUTES,
    ("POST", "/api/v1/admin/vendor-questionnaires/{vid}/copy"): QROUTES,
    ("GET", "/api/v1/vendors/{id}/onboarding"): OROUTES,
    ("PUT", "/api/v1/vendors/{id}/onboarding/answers"): OROUTES,
    ("GET", "/api/v1/sourcing/requisition-lines"): SOURCING_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs"): SOURCING_ROUTES,
    ("PATCH", "/api/v1/sourcing/rfqs/{id}/lines"): SOURCING_ROUTES,
    ("GET", "/api/v1/sourcing/rfqs/{id}"): SOURCING_ROUTES,
    ("PATCH", "/api/v1/sourcing/rfqs/{id}"): CONTENT_ROUTES,
    ("PUT", "/api/v1/sourcing/rfqs/{id}/requirements"): CONTENT_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/requirements/from-template"): CONTENT_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/requirements/{rid}/waive"): CONTENT_ROUTES,
    ("PUT", "/api/v1/sourcing/rfqs/{id}/attachments/{aid}/visibility"): CONTENT_ROUTES,
    ("GET", "/api/v1/sourcing/rfqs/{id}/invitee-candidates"): CONTENT_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/invitees"): CONTENT_ROUTES,
    ("DELETE", "/api/v1/sourcing/rfqs/{id}/invitees/{vendor_id}"): CONTENT_ROUTES,
    ("PATCH", "/api/v1/sourcing/rfqs/{id}/invitees/{vendor_id}"): CONTENT_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/publish"): LIFECYCLE_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/amend"): LIFECYCLE_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/extend-deadline"): LIFECYCLE_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/start-evaluation"): LIFECYCLE_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/invitees/{vendor_id}/resend"): LIFECYCLE_ROUTES,
    ("GET", "/api/v1/sourcing/rfqs/{id}/versions"): LIFECYCLE_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/bids"): BID_ROUTES,
    ("GET", "/api/v1/sourcing/rfqs/{id}/bids"): BID_ROUTES,
    ("GET", "/api/v1/sourcing/rfqs/{id}/bids/{bid_id}"): BID_ROUTES,
    ("PATCH", "/api/v1/sourcing/rfqs/{id}/bids/{bid_id}"): BID_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/bids/{bid_id}/submit"): BID_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/bids/{bid_id}/replace"): BID_ROUTES,
    ("GET", "/api/v1/sourcing/rfqs/{id}/comparison"): COMPARISON_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/comparison/refresh-rate"): COMPARISON_ROUTES,
    ("GET", "/api/v1/sourcing/rfqs/{id}/evaluation"): EVALUATION_ROUTES,
    ("PUT", "/api/v1/sourcing/rfqs/{id}/evaluation/criteria"): EVALUATION_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/evaluation/evaluators"): EVALUATION_ROUTES,
    ("DELETE", "/api/v1/sourcing/rfqs/{id}/evaluation/evaluators/{identity_id}"): EVALUATION_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/evaluation/declaration"): EVALUATION_ROUTES,
    ("PUT", "/api/v1/sourcing/rfqs/{id}/evaluation/scores"): EVALUATION_ROUTES,
    ("POST", "/api/v1/sourcing/rfqs/{id}/evaluation/scores/submit"): EVALUATION_ROUTES,
    ("GET", "/api/v1/sourcing/rfqs"): SCREENS_ROUTES,
    ("GET", "/api/v1/sourcing/rfqs/{id}/history"): SCREENS_ROUTES,
    (
        "GET",
        "/api/v1/vendors/{id}/eligibility",
    ): "vendors/test_eligibility.py::test_vendor_eligibility_route_conceals_foreign_tenants",
    (
        "GET",
        "/api/v1/vendors/{id}/documents",
    ): "vendors/test_documents.py::test_vendor_document_routes_conceal_foreign_tenants",
    (
        "POST",
        "/api/v1/vendors/{id}/documents",
    ): "vendors/test_documents.py::test_vendor_document_routes_conceal_foreign_tenants",
    (
        "POST",
        "/api/v1/vendors/{id}/documents/{did}/replace",
    ): "vendors/test_documents.py::test_vendor_document_routes_conceal_foreign_tenants",
    (
        "POST",
        "/api/v1/vendors/{id}/documents/{did}/verify",
    ): "vendors/test_documents.py::test_vendor_document_routes_conceal_foreign_tenants",
    (
        "POST",
        "/api/v1/vendors/{id}/documents/{did}/reject",
    ): "vendors/test_documents.py::test_vendor_document_routes_conceal_foreign_tenants",
    (
        "POST",
        "/internal/jobs/vendor-document-expiry",
    ): "vendors/test_documents.py::test_internal_expiry_trigger_origin_key_and_nonreplay",
    (
        "POST",
        "/api/v1/vendors/{id}/sensitive/reveal",
    ): "vendors/test_sensitive.py::test_sensitive_routes_conceal_foreign_tenants",
    (
        "PUT",
        "/api/v1/vendors/{id}/sensitive",
    ): "vendors/test_sensitive.py::test_sensitive_routes_conceal_foreign_tenants",
    (
        "GET",
        "/api/v1/vendors/{id}/contacts",
    ): "vendors/test_contacts.py::test_vendor_contact_routes_conceal_foreign_tenants",
    (
        "POST",
        "/api/v1/vendors/{id}/contacts",
    ): "vendors/test_contacts.py::test_vendor_contact_routes_conceal_foreign_tenants",
    (
        "PATCH",
        "/api/v1/vendors/{id}/contacts/{contact_id}",
    ): "vendors/test_contacts.py::test_vendor_contact_routes_conceal_foreign_tenants",
    (
        "DELETE",
        "/api/v1/vendors/{id}/contacts/{contact_id}",
    ): "vendors/test_contacts.py::test_vendor_contact_routes_conceal_foreign_tenants",
    (
        "GET",
        "/api/v1/vendors",
    ): "vendors/test_vendors.py::test_vendor_routes_conceal_foreign_tenants",
    (
        "POST",
        "/api/v1/vendors",
    ): "vendors/test_vendors.py::test_vendor_routes_conceal_foreign_tenants",
    (
        "GET",
        "/api/v1/vendors/{id}",
    ): "vendors/test_vendors.py::test_vendor_routes_conceal_foreign_tenants",
    (
        "PATCH",
        "/api/v1/vendors/{id}",
    ): "vendors/test_vendors.py::test_vendor_routes_conceal_foreign_tenants",
    (
        "POST",
        "/api/v1/vendors/{id}/units/{unit_id}/disable",
    ): "vendors/test_vendors.py::test_vendor_routes_conceal_foreign_tenants",
    (
        "POST",
        "/api/v1/vendors/{id}/units/{unit_id}/enable",
    ): "vendors/test_vendors.py::test_vendor_routes_conceal_foreign_tenants",
    ("POST", "/api/v1/requisitions/{id}/codes"): CODES_ROUTES,
    ("POST", "/api/v1/projects/{id}/codes"): CODES_ROUTES,
    (
        "GET",
        "/api/v1/budget/change-requests/{id}",
    ): "budget/test_change_requests.py::test_change_request_foreign_tenant",
    (
        "GET",
        "/api/v1/approvals/instances/{id}/explanation",
    ): "approvals/test_explanation.py::test_explanation_foreign_tenant_admin_and_concealment",
    (
        "POST",
        "/api/v1/requisitions/{id}/transitions",
    ): "requisitions/test_lifecycle.py::test_foreign_lifecycle_routes",
    (
        "GET",
        "/api/v1/requisitions/{id}/links",
    ): "requisitions/test_links.py::test_foreign_tenant_and_ungranted_links_are_404",
    (
        "GET",
        "/api/v1/requisitions/{id}/revisions",
    ): AMENDMENT_ROUTES,
    (
        "GET",
        "/api/v1/requisitions/{id}/changes",
    ): AMENDMENT_ROUTES,
    (
        "POST",
        "/api/v1/requisitions/{id}/amendments",
    ): AMENDMENT_ROUTES,
    (
        "GET",
        "/api/v1/requisitions/{id}/amendments/current",
    ): AMENDMENT_ROUTES,
    (
        "PUT",
        "/api/v1/requisitions/{id}/amendments/{revision_no}",
    ): AMENDMENT_ROUTES,
    (
        "DELETE",
        "/api/v1/requisitions/{id}/amendments/{revision_no}",
    ): AMENDMENT_ROUTES,
    (
        "POST",
        "/api/v1/requisitions/{id}/amendments/{revision_no}/submit",
    ): AMENDMENT_ROUTES,
    (
        "POST",
        "/api/v1/requisitions/{id}/amendments/{revision_no}/withdraw",
    ): AMENDMENT_ROUTES,
    (
        "GET",
        "/api/v1/requisitions/{id}/events",
    ): "requisitions/test_lifecycle.py::test_foreign_lifecycle_routes",
    (
        "GET",
        "/api/v1/requisitions/{id}/transitions/available",
    ): "requisitions/test_lifecycle.py::test_foreign_lifecycle_routes",
    (
        "POST",
        "/api/v1/approvals/tasks/{task_id}/decisions",
    ): "approvals/test_decisions.py::test_approval_routes_foreign_tenant",
    (
        "POST",
        "/api/v1/approvals/instances/{id}/withdraw",
    ): "approvals/test_decisions.py::test_approval_routes_foreign_tenant",
    (
        "GET",
        "/api/v1/approvals/instances/{id}/decisions",
    ): "approvals/test_decisions.py::test_approval_routes_foreign_tenant",
    (
        "GET",
        "/api/v1/workflows/{id}/versions/{version}/route-preview",
    ): "approvals/test_route_preview.py::test_foreign_tenant_route_preview_and_scope_are_404",
    (
        "GET",
        "/api/v1/approvals/instances/{id}",
    ): "approvals/test_engine.py::test_instance_route_foreign_tenant_and_roles",
    (
        "GET",
        "/api/v1/approvals/eligible-approvers",
    ): "approvals/test_eligibility.py::test_picker_foreign_scope_and_authorization",
    ("GET", "/api/v1/approvals/delegations/decided-on-my-behalf"): (
        "approvals/test_inbox.py::test_decided_on_my_behalf_is_self_scoped_and_isolated"
    ),
    ("GET", "/api/v1/approvals/tasks"): (
        "approvals/test_inbox.py::test_inbox_http_is_self_scoped_and_foreign_tenant_sees_nothing"
    ),
    ("POST", "/api/v1/approvals/delegations"): (
        "approvals/test_delegation.py::"
        "test_delegation_routes_unauthenticated_forbidden_and_foreign_scope"
    ),
    ("GET", "/api/v1/approvals/delegations"): (
        "approvals/test_delegation.py::"
        "test_delegation_routes_unauthenticated_forbidden_and_foreign_scope"
    ),
    ("GET", "/api/v1/approvals/delegations/eligible-delegates"): (
        "approvals/test_delegation.py::"
        "test_delegation_routes_unauthenticated_forbidden_and_foreign_scope"
    ),
    (
        "POST",
        "/api/v1/approvals/delegations/{id}/revoke",
    ): (
        "approvals/test_delegation.py::"
        "test_revoke_http_foreign_and_unknown_ids_have_identical_problem_bodies"
    ),
    (
        "POST",
        "/api/v1/approvals/tasks/{task_id}/reassign",
    ): (
        "approvals/test_delegation.py::"
        "test_delegation_routes_unauthenticated_forbidden_and_foreign_scope"
    ),
    (
        "GET",
        "/api/v1/workflows/{id}/versions/{version}/validation",
    ): "approvals/test_workflow_definitions.py::test_foreign_tenant_routes_and_authorization",
    (
        "GET",
        "/api/v1/documents/{document_type}/{document_id}/notes",
    ): "collaboration/test_attachments.py::test_foreign_tenant_every_collaboration_route",
    (
        "POST",
        "/api/v1/documents/{document_type}/{document_id}/notes",
    ): "collaboration/test_attachments.py::test_foreign_tenant_every_collaboration_route",
    (
        "PATCH",
        "/api/v1/notes/{id}",
    ): "collaboration/test_attachments.py::test_foreign_tenant_every_collaboration_route",
    (
        "GET",
        "/api/v1/notes/{id}/revisions",
    ): "collaboration/test_attachments.py::test_foreign_tenant_every_collaboration_route",
    (
        "GET",
        "/api/v1/documents/{document_type}/{document_id}/attachments",
    ): "collaboration/test_attachments.py::test_foreign_tenant_every_collaboration_route",
    (
        "POST",
        "/api/v1/documents/{document_type}/{document_id}/attachments",
    ): "collaboration/test_attachments.py::test_foreign_tenant_every_collaboration_route",
    (
        "POST",
        "/api/v1/attachments/{id}/versions",
    ): "collaboration/test_attachments.py::test_foreign_tenant_every_collaboration_route",
    (
        "GET",
        "/api/v1/attachments/{id}/content",
    ): "collaboration/test_attachments.py::test_foreign_tenant_every_collaboration_route",
    (
        "DELETE",
        "/api/v1/attachments/{id}",
    ): "collaboration/test_attachments.py::test_foreign_tenant_every_collaboration_route",
    (
        "GET",
        "/api/v1/requisitions/{id}/submission-check",
    ): (
        "requisitions/test_submission_check.py::"
        "test_submission_check_foreign_tenant_and_permission_outcomes"
    ),
    (
        "GET",
        "/api/v1/requisitions/{id}/lines",
    ): "requisitions/test_lines.py::test_foreign_tenant_line_routes_and_permissions",
    (
        "POST",
        "/api/v1/requisitions/{id}/lines",
    ): "requisitions/test_lines.py::test_foreign_tenant_line_routes_and_permissions",
    (
        "PUT",
        "/api/v1/requisitions/{id}/lines",
    ): "requisitions/test_lines.py::test_foreign_tenant_line_routes_and_permissions",
    (
        "PUT",
        "/api/v1/requisitions/{id}/lines/{line_id}",
    ): "requisitions/test_lines.py::test_foreign_tenant_line_routes_and_permissions",
    (
        "DELETE",
        "/api/v1/requisitions/{id}/lines/{line_id}",
    ): "requisitions/test_lines.py::test_foreign_tenant_line_routes_and_permissions",
    (
        "GET",
        "/api/v1/catalogue",
    ): "requisitions/test_catalogue.py::test_http_search_and_foreign_unit_is_404",
    (
        "GET",
        "/api/v1/requisitions/lookups/{kind}",
    ): "requisitions/test_lookups.py::test_unknown_kind_422_and_route_reachable",
    (
        "POST",
        "/api/v1/requisitions",
    ): "requisitions/test_header.py::test_foreign_tenant_routes_and_list_isolation",
    (
        "GET",
        "/api/v1/requisitions",
    ): "requisitions/test_header.py::test_foreign_tenant_routes_and_list_isolation",
    (
        "GET",
        "/api/v1/requisitions/{id}",
    ): "requisitions/test_header.py::test_foreign_tenant_routes_and_list_isolation",
    (
        "PATCH",
        "/api/v1/requisitions/{id}",
    ): "requisitions/test_header.py::test_foreign_tenant_routes_and_list_isolation",
    (
        "GET",
        "/api/v1/workflows",
    ): "approvals/test_workflow_definitions.py::test_foreign_tenant_routes_and_authorization",
    (
        "POST",
        "/api/v1/workflows",
    ): "approvals/test_workflow_definitions.py::test_foreign_tenant_routes_and_authorization",
    (
        "GET",
        "/api/v1/workflows/{id}",
    ): "approvals/test_workflow_definitions.py::test_foreign_tenant_routes_and_authorization",
    (
        "POST",
        "/api/v1/workflows/{id}/versions",
    ): "approvals/test_workflow_definitions.py::test_foreign_tenant_routes_and_authorization",
    (
        "GET",
        "/api/v1/workflows/{id}/versions/{version}",
    ): "approvals/test_workflow_definitions.py::test_foreign_tenant_routes_and_authorization",
    (
        "PUT",
        "/api/v1/workflows/{id}/versions/{version}",
    ): "approvals/test_workflow_definitions.py::test_foreign_tenant_routes_and_authorization",
    (
        "POST",
        "/api/v1/workflows/{id}/versions/{version}/publish",
    ): "approvals/test_workflow_definitions.py::test_foreign_tenant_routes_and_authorization",
    (
        "POST",
        "/api/v1/workflows/{id}/retire",
    ): "approvals/test_workflow_definitions.py::test_foreign_tenant_routes_and_authorization",
    (
        "GET",
        "/api/v1/projects/{id}/risk-indicators",
    ): "projects/test_risk_indicators.py::test_foreign_dashboard_reads",
    ("GET", "/api/v1/budget/summary"): "budget/test_summary.py::test_foreign_summary_unit",
    (
        "POST",
        "/api/v1/imports/{id}/cancel",
    ): "imports/test_async_import.py::test_history_and_cancel_foreign_tenant_routes_and_guards",
    (
        "GET",
        "/api/v1/imports",
    ): "imports/test_async_import.py::test_history_and_cancel_foreign_tenant_routes_and_guards",
    (
        "POST",
        "/api/v1/imports/{id}/validate",
    ): "imports/test_validation.py::test_foreign_batch_routes",
    (
        "POST",
        "/api/v1/imports/{id}/commit",
    ): "imports/test_validation.py::test_foreign_batch_routes",
    (
        "GET",
        "/api/v1/imports/{id}/preview",
    ): "imports/test_validation.py::test_foreign_batch_routes",
    (
        "GET",
        "/api/v1/imports/{id}/errors.csv",
    ): "imports/test_validation.py::test_foreign_batch_routes",
    (
        "GET",
        "/api/v1/auth/me",
    ): "projects/test_list_ui.py::test_auth_me_unauthenticated_and_session_identity_isolation",
    (
        "PATCH",
        "/api/v1/auth/me",
    ): (
        "identity/test_display_name.py::test_patch_me_for_another_org_member_only_touches_own_identity"
    ),
    (
        "GET",
        "/api/v1/projects/{id}/risks",
    ): "projects/test_risks.py::test_risk_foreign_tenant_routes",
    (
        "POST",
        "/api/v1/projects/{id}/risks",
    ): "projects/test_risks.py::test_risk_foreign_tenant_routes",
    (
        "PATCH",
        "/api/v1/projects/{id}/risks/{risk_id}",
    ): "projects/test_risks.py::test_risk_foreign_tenant_routes",
    (
        "POST",
        "/api/v1/invitations",
    ): "identity/test_invitations.py::test_admin_tenant_isolation_and_guards",
    (
        "GET",
        "/api/v1/invitations",
    ): "identity/test_invitations.py::test_admin_tenant_isolation_and_guards",
    (
        "POST",
        "/api/v1/invitations/{id}/resend",
    ): "identity/test_invitations.py::test_admin_tenant_isolation_and_guards",
    (
        "POST",
        "/api/v1/invitations/{id}/withdraw",
    ): "identity/test_invitations.py::test_admin_tenant_isolation_and_guards",
    (
        "GET",
        "/api/v1/invitations/by-token",
    ): "identity/test_invitations.py::test_uniform_bytes_for_all_unavailable_states_on_both_routes",
    (
        "POST",
        "/api/v1/invitations/accept",
    ): (
        "identity/test_invitations.py::"
        "test_existing_account_accept_into_b_preserves_a_and_never_replays"
    ),
    (
        "GET",
        "/api/v1/auth/organizations",
    ): "isolation/test_multi_org.py::test_selection_isolation_and_no_replay",
    (
        "POST",
        "/api/v1/auth/organization",
    ): "isolation/test_multi_org.py::test_selection_isolation_and_no_replay",
    (
        "PUT",
        "/api/v1/org/tenant-label",
    ): "isolation/test_multi_org.py::test_tenant_label_authorization_validation_and_isolation",
    (
        "GET",
        "/api/v1/fx/rates",
    ): "org/test_fx.py::test_fx_reads_foreign_org_inputs_do_not_change_reference_data",
    (
        "GET",
        "/api/v1/fx/status",
    ): "org/test_fx.py::test_fx_reads_foreign_org_inputs_do_not_change_reference_data",
    (
        "PUT",
        "/api/v1/org/base-currency",
    ): "org/test_fx.py::test_base_currency_set_repeat_lock_and_foreign_org",
    (
        "POST",
        "/internal/jobs/fx-ingest",
    ): "org/test_fx.py::test_internal_fx_production_stack_repeats_failures_and_guards",
    ("GET", "/api/v1/auth/csrf"): "session/test_http.py::test_csrf_bootstrap_ignores_foreign_org",
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
    (
        "POST",
        "/internal/jobs/requisition-expiry",
    ): "requisitions/test_expiry.py::test_internal_expiry_rejects_missing_key_and_origin",
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
    (
        "GET",
        "/api/v1/projects/{id}/tree",
    ): "projects/test_risk_indicators.py::test_foreign_dashboard_reads",
    ("DELETE", "/api/v1/requisitions/{id}"): (
        "requisitions/test_soft_delete.py::test_foreign_tenant_delete_routes_are_404"
    ),
    ("DELETE", "/api/v1/projects/{id}"): (
        "requisitions/test_soft_delete.py::test_foreign_tenant_delete_routes_are_404"
    ),
    ("DELETE", "/api/v1/budget/change-requests/{id}"): (
        "budget/test_change_requests.py::test_change_request_foreign_tenant"
    ),
    ("DELETE", "/api/v1/workflows/{id}/versions/{version}"): (
        "requisitions/test_soft_delete.py::test_workflow_draft_delete_keeps_the_version_number"
    ),
    ("POST", "/api/v1/projects"): "projects/test_projects.py::test_create_foreign_bu",
    (
        "GET",
        "/api/v1/projects/lookups/{kind}",
    ): "projects/test_project_budget.py::test_lookup_gated_scoped_searchable",
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
    (
        "GET",
        "/api/v1/projects/{id}/phases",
    ): "projects/test_schedule.py::test_schedule_foreign_tenant_routes",
    (
        "POST",
        "/api/v1/projects/{id}/phases",
    ): "projects/test_schedule.py::test_schedule_foreign_tenant_routes",
    (
        "PATCH",
        "/api/v1/projects/{id}/phases/{phase_id}",
    ): "projects/test_schedule.py::test_schedule_foreign_tenant_routes",
    (
        "GET",
        "/api/v1/projects/{id}/milestones",
    ): "projects/test_schedule.py::test_schedule_foreign_tenant_routes",
    (
        "POST",
        "/api/v1/projects/{id}/milestones",
    ): "projects/test_schedule.py::test_schedule_foreign_tenant_routes",
    (
        "PATCH",
        "/api/v1/projects/{id}/milestones/{mid}",
    ): "projects/test_schedule.py::test_schedule_foreign_tenant_routes",
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
                invitations_router,
                approvals_router,
                workflows_router,
                requisitions_router,
                vendors_router,
                vendor_questionnaires_router,
                sourcing_router,
                collaboration_router,
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
            invitations_router,
            approvals_router,
            workflows_router,
            requisitions_router,
            vendors_router,
            vendor_questionnaires_router,
            sourcing_router,
            collaboration_router,
        )
        | widget
    )
    problems = missing_cases(routes, extra)
    assert "no isolation case: GET /api/v1/widgets/{widget_id}" in problems
    assert any("covering test not found" in problem for problem in problems)
