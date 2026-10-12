"""Tenant-scoped list and lineage metadata; counts use pinned running instances."""

from datetime import datetime
from typing import cast

from flo.modules.approvals.models import WorkflowRepository
from flo.modules.approvals.schemas import WorkflowRead, WorkflowVersionSummary


# ponytail: per-row enrichment is capped by the 50-row page; batch page queries before
# raising that ceiling or if workflow-list latency exceeds 2s.
def enrich(repo: WorkflowRepository, row: WorkflowRead) -> WorkflowRead:
    has_instances = bool(
        cast(
            tuple[object, ...],
            repo.execute("SELECT to_regclass('approval_instance') IS NOT NULL").fetchone(),
        )[0]
    )
    count = (
        "(SELECT count(*) FROM approval_instance a WHERE a.org_id=%(org_id)s "
        "AND a.workflow_version_id=v.id AND a.status='running') "
        if has_instances
        else "NULL::bigint "
    )
    records = repo.execute(
        "SELECT v.version,v.status,v.created_at,v.published_at,i.email,"
        + count
        + "FROM workflow_version v JOIN identity i ON i.id=v.created_by "
        "WHERE v.org_id=%(org_id)s AND v.definition_id=%(id)s ORDER BY v.version",
        {"id": row.id},
    ).fetchall()
    row.versions = [
        WorkflowVersionSummary.model_validate(
            dict(
                zip(
                    ("version", "status", "created_at", "published_at", "author_name", "in_flight"),
                    v,
                    strict=True,
                )
            )
        )
        for v in records
    ]
    row.published_version = next((v.version for v in row.versions if v.status == "published"), None)
    row.draft_version = next((v.version for v in row.versions if v.status == "draft"), None)
    row.in_flight = (
        sum(v.in_flight or 0 for v in row.versions if v.version != row.published_version)
        if has_instances
        else None
    )
    latest = repo.execute(
        "SELECT a.occurred_at,i.email FROM audit_log a LEFT JOIN identity i ON i.id=a.actor_id "
        "WHERE a.org_id=%(org_id)s AND a.target_id=%(id)s AND a.target_type='workflow' "
        "ORDER BY a.occurred_at DESC LIMIT 1",
        {"id": row.id},
    ).fetchone()
    row.edited_at = cast(datetime, latest[0]) if latest else row.created_at
    row.edited_by_name = str(latest[1]) if latest else "Unknown"
    name = (
        repo.execute(
            "SELECT name FROM org_unit WHERE org_id=%(org_id)s AND id=%(id)s", {"id": row.scope_id}
        ).fetchone()
        if row.scope_type == "bu"
        else repo.execute("SELECT name FROM organization WHERE id=%(org_id)s").fetchone()
    )
    label = {
        "project": "Projects",
        "requisition": "Capital Requisitions",
        "budget_change": "Budget Changes",
        "vendor": "Vendors",
        "rfq": "RFQs",
        "award": "Awards",
        "po": "Purchase Orders",
    }[row.document_type]
    row.applies_to = f"{label} · {name[0] if name else 'Organization'}"
    return row
