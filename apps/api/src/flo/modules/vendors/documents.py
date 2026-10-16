"""Vendor documents: issue and expiry, verification, reviewer (VEN-003, VEN-011)."""

from datetime import date
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.collaboration.service import DocumentAccess, register_document_access
from flo.modules.identity.service import AuthorizationTarget
from flo.modules.org.service import MasterCodeUnusable, OrgService, today
from flo.modules.vendors.models import VendorRepository
from flo.modules.vendors.schemas import (
    DocState,
    DocumentCreate,
    DocumentReject,
    DocumentReplace,
    VendorRead,
    conflict,
    invalid,
)

KIND = "vendor_document_type"
EXPIRING_DAYS = 30
SEPARATION_KEY = "approval.separation_of_duty"
# Joined to the version in force, so the file's current scan state and uploader come with it.
DOC_COLUMNS = (
    "d.id, d.type_code, d.attachment_id, v.version, v.scan_status, v.uploaded_by, d.issue_date, "
    "d.expiry_date, d.verification, d.reviewed_by, d.reviewed_at, d.rejection_reason, d.created_at, "
    # The newest uploaded version, which a replace names; the row's own version is the one in force.
    "(SELECT l.id FROM attachment_version l WHERE l.org_id = d.org_id "
    "AND l.attachment_id = d.attachment_id ORDER BY l.version DESC LIMIT 1)"
)
DOC_FROM = "FROM vendor_document d JOIN attachment_version v ON v.id = d.attachment_version_id"


def vendor_access(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, id: UUID
) -> TargetProtocol:
    """Vendors are organization-scoped records; a foreign or unknown id is a 404."""
    row = connection.execute(
        "SELECT 1 FROM vendor WHERE org_id = %(org_id)s AND id = %(id)s",
        {"org_id": scope.org_id, "id": id},
    ).fetchone()
    if row is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return cast(TargetProtocol, AuthorizationTarget.organization(scope.org_id))


def register_access() -> None:
    """Attachments on a vendor use E09-S10's routes; read is vendor.view, write vendor.edit."""
    register_document_access(DocumentAccess("vendor", "vendor.view", "vendor.edit", vendor_access))


def state_of(verification: str | None, expiry: date | None, on: date) -> tuple[str, int | None]:
    """Precedence: rejected, expired, expiring, unverified, ok. Never touches the vendor."""
    if verification == "rejected":
        return "rejected", None
    if expiry is not None:
        left = (expiry - on).days
        if left < 0:
            return "expired", None
        if left <= EXPIRING_DAYS:
            return "expiring", left
    return ("unverified" if verification == "unverified" else "ok"), None


class DocumentsMixin:
    connection: psycopg.Connection[tuple[object, ...]]
    scope: Scope
    actor_id: UUID | None
    settings: Settings | None
    repo: VendorRepository

    if TYPE_CHECKING:

        def _row(self, id: UUID) -> VendorRead: ...

        def _audit(
            self,
            action: str,
            id: UUID,
            before: dict[str, object] | None,
            after: dict[str, object],
            reason: str | None = None,
            target_type: str = "vendor",
        ) -> None: ...

    def _doc_rows(
        self, vendor_id: UUID, id: UUID | None = None, lock: bool = False
    ) -> list[tuple[object, ...]]:
        return self.repo.execute(
            f"SELECT {DOC_COLUMNS} {DOC_FROM} WHERE d.org_id = %(org_id)s "
            "AND d.vendor_id = %(vendor_id)s AND (%(id)s::uuid IS NULL OR d.id = %(id)s) "
            f"ORDER BY d.type_code{' FOR UPDATE OF d' if lock else ''}",
            {"vendor_id": vendor_id, "id": id},
        ).fetchall()

    def _required_types(self, on: date) -> set[str]:
        rows = self.repo.execute(
            "SELECT code FROM master_record WHERE org_id = %(org_id)s AND kind = %(kind)s "
            "AND active AND effective_from <= %(on)s "
            "AND (effective_to IS NULL OR effective_to >= %(on)s) "
            "AND attributes @> '{\"required\": true}'::jsonb",
            {"kind": KIND, "on": on},
        ).fetchall()
        return {str(r[0]) for r in rows}

    @staticmethod
    def _doc_state(row: tuple[object, ...], required: set[str], on: date) -> DocState:
        (id, type_code, attachment_id, version, scan, _uploader, issued, expiry, verification) = (
            row[:9]
        )
        name, left = state_of(str(verification), cast(date | None, expiry), on)
        return DocState.model_validate(
            {
                "type_code": type_code,
                "required": type_code in required,
                "state": name,
                "days_left": left,
                "document_id": id,
                "attachment_id": attachment_id,
                "attachment_version": version,
                "scan_status": scan,
                "issue_date": issued,
                "expiry_date": expiry,
                "verification": verification,
                "reviewed_by": row[9],
                "reviewed_at": row[10],
                "rejection_reason": row[11],
                "created_at": row[12],
                "latest_version_id": row[13],
            }
        )

    def document_state(self, vendor_id: UUID, on: date) -> list[DocState]:
        """The one rule for a vendor's document standing; eligibility (E11-S11) calls this."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            required = self._required_types(on)
            rows = [self._doc_state(r, required, on) for r in self._doc_rows(vendor_id)]
        have = {r.type_code for r in rows}
        rows += [DocState(type_code=t, required=True, state="missing") for t in required - have]
        return sorted(rows, key=lambda r: r.type_code)

    def _document(self, vendor_id: UUID, id: UUID, lock: bool = False) -> tuple[object, ...]:
        rows = self._doc_rows(vendor_id, id, lock)
        if not rows:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return rows[0]

    def _doc_audit(
        self,
        action: str,
        vendor_id: UUID,
        before: tuple[object, ...] | None,
        after: tuple[object, ...],
    ) -> None:
        # Identifiers, type, dates and verification only: no file name and no file content.
        def keep(row: tuple[object, ...]) -> dict[str, object]:
            return {
                "id": str(row[0]),
                "type_code": row[1],
                "attachment_version": row[3],
                "issue_date": str(row[6]) if row[6] else None,
                "expiry_date": str(row[7]) if row[7] else None,
                "verification": row[8],
            }

        self._audit(
            action,
            vendor_id,
            keep(before) if before else None,
            keep(after),
            reason=cast(str | None, after[11]) if action.endswith(".reject") else None,
        )

    def add_document(self, vendor_id: UUID, body: DocumentCreate) -> DocState:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            try:
                code = (
                    OrgService(self.connection, self.scope)
                    .assert_usable(KIND, body.type_code, today())
                    .code
                )
            except MasterCodeUnusable:
                raise invalid(
                    "type_code", "Choose a defined document type.", "invalid_type"
                ) from None
            self._dates(body.issue_date, body.expiry_date)
            attachment = self.repo.execute(
                "SELECT a.id FROM attachment a WHERE a.org_id = %(org_id)s AND a.id = %(id)s "
                "AND a.document_type = 'vendor' AND a.document_id = %(vendor_id)s "
                "AND a.deleted_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM vendor_document x WHERE x.attachment_id = a.id)",
                {"id": body.attachment_id, "vendor_id": vendor_id},
            ).fetchone()
            version = self.repo.execute(
                "SELECT id FROM attachment_version WHERE org_id = %(org_id)s "
                "AND attachment_id = %(id)s ORDER BY version DESC LIMIT 1",
                {"id": body.attachment_id},
            ).fetchone()
            if attachment is None or version is None:
                raise invalid(
                    "attachment_id",
                    "Upload the file to this vendor first, then link it. A file already "
                    "linked to a document cannot be linked again.",
                    "invalid_attachment",
                )
            id = uuid4()
            inserted = self.repo.execute(
                "INSERT INTO vendor_document (id, org_id, vendor_id, type_code, attachment_id, "
                "attachment_version_id, issue_date, expiry_date) VALUES (%(id)s, %(org_id)s, "
                "%(vendor_id)s, %(code)s, %(attachment)s, %(version)s, %(issue)s, %(expiry)s) "
                "ON CONFLICT DO NOTHING RETURNING id",
                {
                    "id": id,
                    "vendor_id": vendor_id,
                    "code": code,
                    "attachment": body.attachment_id,
                    "version": version[0],
                    "issue": body.issue_date,
                    "expiry": body.expiry_date,
                },
            ).fetchone()
            if inserted is None:
                raise conflict(
                    "use_replace",
                    "This vendor already has a document of that type. Replace it instead.",
                )
            after = self._document(vendor_id, id)
            self._doc_audit("vendor.document.add", vendor_id, None, after)
            return self._doc_state(after, self._required_types(today()), today())

    @staticmethod
    def _dates(issue: date | None, expiry: date | None) -> None:
        if issue is not None and expiry is not None and expiry < issue:
            raise invalid(
                "expiry_date", "The expiry date cannot be before the issue date.", "invalid_dates"
            )

    def replace_document(self, vendor_id: UUID, id: UUID, body: DocumentReplace) -> DocState:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            before = self._document(vendor_id, id, lock=True)
            self._dates(body.issue_date, body.expiry_date)
            row = self.repo.execute(
                "SELECT version FROM attachment_version WHERE org_id = %(org_id)s "
                "AND id = %(id)s AND attachment_id = %(attachment)s",
                {"id": body.attachment_version_id, "attachment": before[2]},
            ).fetchone()
            if row is None or cast(int, row[0]) <= cast(int, before[3]):
                raise invalid(
                    "attachment_version_id",
                    "Upload a new version of this document's file, then replace.",
                    "invalid_attachment",
                )
            self.repo.execute(
                "UPDATE vendor_document SET attachment_version_id = %(version)s, "
                "issue_date = %(issue)s, expiry_date = %(expiry)s, verification = 'unverified', "
                "reviewed_by = NULL, reviewed_at = NULL, rejection_reason = NULL, "
                "notified_expiring_at = NULL, notified_expired_at = NULL "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {
                    "id": id,
                    "version": body.attachment_version_id,
                    "issue": body.issue_date,
                    "expiry": body.expiry_date,
                },
            )
            after = self._document(vendor_id, id)
            self._doc_audit("vendor.document.replace", vendor_id, before, after)
            return self._doc_state(after, self._required_types(today()), today())

    def _separation_of_duty(self) -> bool:
        row = self.repo.execute(
            "SELECT value FROM org_setting WHERE org_id = %(org_id)s AND unit_id IS NULL "
            f"AND key = '{SEPARATION_KEY}'"
        ).fetchone()
        return True if row is None else bool(row[0])

    def _review(
        self, vendor_id: UUID, id: UUID, verdict: str, reason: str | None = None
    ) -> DocState:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            before = self._document(vendor_id, id, lock=True)
            if before[8] != "unverified":
                raise conflict(
                    "not_unverified",
                    "Only an unverified document can be reviewed. "
                    "Replace the file to review it again.",
                )
            if verdict == "verified" and before[5] == self.actor_id and self._separation_of_duty():
                raise conflict(
                    "separation_of_duty",
                    "You uploaded this file, so someone else must verify it.",
                )
            self.repo.execute(
                "UPDATE vendor_document SET verification = %(verdict)s, reviewed_by = %(actor)s, "
                "reviewed_at = now(), rejection_reason = %(reason)s "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": id, "verdict": verdict, "actor": self.actor_id, "reason": reason},
            )
            after = self._document(vendor_id, id)
            action = "vendor.document.verify" if verdict == "verified" else "vendor.document.reject"
            self._doc_audit(action, vendor_id, before, after)
            return self._doc_state(after, self._required_types(today()), today())

    def verify_document(self, vendor_id: UUID, id: UUID) -> DocState:
        return self._review(vendor_id, id, "verified")

    def reject_document(self, vendor_id: UUID, id: UUID, body: DocumentReject) -> DocState:
        return self._review(vendor_id, id, "rejected", body.reason)
