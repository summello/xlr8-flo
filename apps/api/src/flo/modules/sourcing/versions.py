"""What an RFQ version says to vendors, and how it differs from the one before (SRC-005)."""

from datetime import datetime
from typing import cast
from uuid import UUID

from flo.modules.sourcing.models import RfqRepository

CONTENT_FIELDS = ("specifications", "delivery_location", "delivery_terms", "terms", "deadline_tz")


def snapshot(repo: RfqRepository, id: UUID) -> dict[str, object]:
    """Content, lines, requirements and vendor-visible attachments as the vendors see them."""
    head = repo.execute(
        f"SELECT {', '.join(CONTENT_FIELDS)}, deadline_at FROM rfq "
        "WHERE org_id = %(org_id)s AND id = %(id)s",
        {"id": id},
    ).fetchone()
    assert head is not None
    lines = repo.execute(
        "SELECT requisition_line_id, line_no, quantity::text, description, uom_code, specification "
        "FROM rfq_line WHERE org_id = %(org_id)s AND rfq_id = %(id)s ORDER BY line_no",
        {"id": id},
    ).fetchall()
    requirements = repo.execute(
        "SELECT position, text FROM rfq_requirement "
        "WHERE org_id = %(org_id)s AND rfq_id = %(id)s AND NOT waived ORDER BY position",
        {"id": id},
    ).fetchall()
    attachments = repo.execute(
        "SELECT a.id, v.display_name, v.version FROM attachment a "
        "JOIN rfq_attachment_visibility x ON x.org_id = a.org_id AND x.attachment_id = a.id "
        "AND x.rfq_id = a.document_id AND x.vendor_visible "
        "JOIN attachment_version v ON v.org_id = a.org_id AND v.attachment_id = a.id "
        "AND v.version = (SELECT max(m.version) FROM attachment_version m "
        "WHERE m.org_id = a.org_id AND m.attachment_id = a.id) "
        "WHERE a.org_id = %(org_id)s AND a.document_type = 'rfq' AND a.document_id = %(id)s "
        "AND a.deleted_at IS NULL",
        {"id": id},
    ).fetchall()
    return {
        **dict(zip(CONTENT_FIELDS, head[:-1], strict=True)),
        "deadline_at": cast(datetime, head[-1]).isoformat(),
        "lines": {
            str(r[0]): {
                "line_no": r[1],
                "quantity": r[2],
                "description": r[3],
                "uom_code": r[4],
                "specification": r[5],
            }
            for r in lines
        },
        "requirements": {str(r[0]): r[1] for r in requirements},
        "attachments": {str(r[0]): {"name": r[1], "version": r[2]} for r in attachments},
    }


def diff(was: dict[str, object], now: dict[str, object], path: str = "") -> dict[str, object]:
    """Field-level `{was, now}` between two snapshots; nested keys are joined by `/`."""
    out: dict[str, object] = {}
    for key in sorted(was.keys() | now.keys()):
        a, b = was.get(key), now.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            out |= diff(a, b, f"{path}{key}/")
        elif a != b:
            out[f"{path}{key}"] = {"was": a, "now": b}
    return out
