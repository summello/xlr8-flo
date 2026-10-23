"""Vendor status changes through approval: submit, availability, suspend, reactivate (VEN-005)."""

from datetime import date
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.schemas import Outcome
from flo.modules.approvals.service import require_workflow_for, start, withdraw
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    AuthorizationTarget,
)
from flo.modules.vendors import approval as approval  # registers the "vendor" document type
from flo.modules.vendors import duplicates
from flo.modules.vendors import repo as sensitive
from flo.modules.vendors.models import VendorRepository
from flo.modules.vendors.schemas import (
    ChangeCreate,
    ChangeRead,
    ContactRead,
    DocState,
    DuplicateMatch,
    DuplicateResult,
    InactivateCreate,
    ScoreResult,
    SubmitCreate,
    conflict,
)

CHANGE_COLUMNS = (
    "c.id, c.vendor_id, c.kind, c.payload, c.reason, c.status, i.email, c.requested_at, "
    "c.decided_at"
)
CHANGE_FIELDS = (
    "id",
    "vendor_id",
    "kind",
    "payload",
    "reason",
    "status",
    "requested_by_email",
    "requested_at",
    "decided_at",
)
CHANGE_FROM = "FROM vendor_change c JOIN identity i ON i.id = c.requested_by"
# kind -> (status the vendor must be in, status it moves to on approval)
MOVES = {
    "create": ("pending_approval", "active"),
    "availability": ("active", "active"),
    "suspend": ("active", "suspended"),
    "reactivate": ("suspended", "active"),
}
FINAL = {Outcome.APPROVED: "approved", Outcome.WITHDRAWN: "withdrawn"}


def invalid_transition(detail: str) -> ProblemError:
    return conflict("invalid_transition", detail)


class ChangesMixin:
    connection: psycopg.Connection[tuple[object, ...]]
    scope: Scope
    actor_id: UUID | None
    repo: VendorRepository

    if TYPE_CHECKING:

        def _audit(
            self,
            action: str,
            id: UUID,
            before: dict[str, object] | None,
            after: dict[str, object],
            reason: str | None = None,
            target_type: str = "vendor",
        ) -> None: ...
        def contacts_complete(self, vendor_id: UUID) -> list[str]: ...
        def answers_complete(self, vendor_id: UUID) -> list[str]: ...
        def document_state(self, vendor_id: UUID, on: date) -> list[DocState]: ...
        def score(self, vendor_id: UUID) -> ScoreResult: ...
        def list_contacts(self, vendor_id: UUID) -> list[ContactRead]: ...
        def _identifiers(self, values: dict[str, str]) -> dict[str, str]: ...
        def _check_units(self, unit_ids: list[UUID]) -> list[UUID]: ...
        def _shape(self, availability: str, unit_ids: list[UUID]) -> None: ...
        def _set_units(self, id: UUID, unit_ids: list[UUID]) -> None: ...

    def _holds(self, permission: str) -> bool:
        assert self.actor_id is not None
        return (
            AuthorizationResolver(cast(AuthorizationConnection, self.connection), self.scope)
            .check(
                IdentityId(self.actor_id),
                permission,
                cast(TargetProtocol, AuthorizationTarget.organization(self.scope.org_id)),
            )
            .allowed
        )

    def _lock(self, id: UUID) -> tuple[str, str, UUID]:
        """The vendor row, locked: its status, availability and creator. A foreign id is 404."""
        row = self.repo.execute(
            "SELECT status, availability, created_by FROM vendor "
            "WHERE org_id = %(org_id)s AND id = %(id)s FOR UPDATE",
            {"id": id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return str(row[0]), str(row[1]), cast(UUID, row[2])

    def _set_status(self, id: UUID, before: str, after: str, change: UUID | None) -> None:
        self.repo.execute(
            "UPDATE vendor SET status = %(status)s, updated_at = now() "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": id, "status": after},
        )
        after_fields: dict[str, object] = {"status": after}
        if change:
            after_fields["change_id"] = str(change)
        self._audit("vendor.status_change", id, {"status": before}, after_fields)

    def _pending(self, vendor_id: UUID) -> None:
        if self.repo.execute(
            "SELECT 1 FROM vendor_change WHERE org_id = %(org_id)s AND vendor_id = %(id)s "
            "AND status = 'pending'",
            {"id": vendor_id},
        ).fetchone():
            raise conflict(
                "change_pending", "A change to this vendor is already awaiting approval."
            )

    def _open_request(
        self, vendor_id: UUID, kind: str, payload: dict[str, JsonValue], reason: str | None
    ) -> UUID:
        """Insert the pending request and start its approval (409 when no workflow is set up)."""
        id = uuid4()
        self.repo.execute(
            "INSERT INTO vendor_change (id, org_id, vendor_id, kind, payload, reason, "
            "requested_by) VALUES (%(id)s, %(org_id)s, %(vendor)s, %(kind)s, %(payload)s, "
            "%(reason)s, %(actor)s)",
            {
                "id": id,
                "vendor": vendor_id,
                "kind": kind,
                "payload": Jsonb(payload),
                "reason": reason,
                "actor": self.actor_id,
            },
        )
        require_workflow_for(self.connection, self.scope, "vendor", id, "vendors")
        return id

    def _start(self, change_id: UUID) -> None:
        assert self.actor_id is not None
        start(
            self.connection,
            self.scope,
            document_type="vendor",
            document_id=change_id,
            actor_id=self.actor_id,
            resolver=AuthorizationResolver(
                cast(AuthorizationConnection, self.connection), self.scope
            ),
        )

    def change(self, id: UUID) -> ChangeRead:
        row = self.repo.execute(
            f"SELECT {CHANGE_COLUMNS} {CHANGE_FROM} WHERE c.org_id = %(org_id)s AND c.id = %(id)s",
            {"id": id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return ChangeRead.model_validate(dict(zip(CHANGE_FIELDS, row, strict=True)))

    def list_changes(self, vendor_id: UUID) -> list[ChangeRead]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._lock_free(vendor_id)
            rows = self.repo.execute(
                f"SELECT {CHANGE_COLUMNS}, coalesce(c.requested_by = %(actor)s, false) "
                f"{CHANGE_FROM} WHERE c.org_id = %(org_id)s "
                "AND c.vendor_id = %(id)s ORDER BY c.requested_at DESC, c.id DESC",
                {"id": vendor_id, "actor": self.actor_id},
            ).fetchall()
        fields = (*CHANGE_FIELDS, "requested_by_me")
        return [ChangeRead.model_validate(dict(zip(fields, r, strict=True))) for r in rows]

    def _lock_free(self, id: UUID) -> None:
        if not self.repo.execute(
            "SELECT 1 FROM vendor WHERE org_id = %(org_id)s AND id = %(id)s", {"id": id}
        ).fetchone():
            raise ProblemError(ErrorCode.NOT_FOUND)

    # -- create ------------------------------------------------------------------------------

    def submit(self, id: UUID, body: SubmitCreate) -> ChangeRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            status, _, creator = self._lock(id)
            if creator != self.actor_id and not self._holds("vendor.edit"):
                raise ProblemError(ErrorCode.FORBIDDEN)
            if status != "draft":
                raise invalid_transition("Only a draft vendor can be submitted.")
            if missing := self.contacts_complete(id):
                raise ProblemError(
                    ErrorCode.VALIDATION_FAILED,
                    detail="Add the required contacts before submitting.",
                    errors=tuple(
                        ProblemFieldError(field=f"contacts.{t}", message=f"Add a {t} contact.")
                        for t in missing
                    ),
                    checks={"problem": "contacts_incomplete"},
                )
            # ponytail: a vendor created directly (no onboarding request) has no questionnaire,
            # so there are no answers to check or score; require one if policy says otherwise.
            onboarded = self.repo.execute(
                "SELECT 1 FROM vendor_onboarding WHERE org_id = %(org_id)s AND vendor_id = %(id)s",
                {"id": id},
            ).fetchone()
            payload: dict[str, JsonValue] = {}
            if onboarded:
                if unanswered := self.answers_complete(id):
                    raise ProblemError(
                        ErrorCode.VALIDATION_FAILED,
                        detail="Answer the required questions before submitting.",
                        errors=tuple(
                            ProblemFieldError(
                                field=f"answers.{k}", message="This question needs an answer."
                            )
                            for k in unanswered
                        ),
                        checks={"problem": "answers_incomplete"},
                    )
                # A score under the threshold is evidence for the approver, never a refusal (6h-6).
                result = self.score(id)
                payload |= {
                    "score": result.score,
                    "threshold": result.threshold,
                    "breakdown": [line.model_dump(mode="json") for line in result.breakdown],
                }
            # Locked, then checked: of two submits with one tax id, the later one sees the first.
            duplicates.lock(self.repo, id)
            matches = duplicates.find(self.repo, vendor_id=id)
            fields = sorted({field for field, _ in matches})
            if fields:
                if not (body.override_reason and self._holds("vendor.approve")):
                    raise ProblemError(
                        ErrorCode.CONFLICT,
                        detail="Another vendor already uses this identifier. "
                        "A vendor manager can submit it with a reason.",
                        errors=tuple(
                            ProblemFieldError(field=f, message="Already used by another vendor.")
                            for f in fields
                        ),
                        checks={"problem": "duplicate_identifier"},
                    )
                payload |= {"duplicates": cast(JsonValue, fields)}
                payload["override_reason"] = body.override_reason
            change_id = self._open_request(id, "create", payload, None)
            if fields:
                self._audit(
                    "vendor.duplicate_override",
                    id,
                    None,
                    {"fields": fields, "change_id": str(change_id)},
                    reason=body.override_reason,
                )
            self._set_status(id, status, "pending_approval", change_id)
            self._start(change_id)
            return self.change(change_id)

    def revise(self, id: UUID) -> None:
        """A rejected vendor goes back to draft to be corrected and submitted again."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            status, _, _ = self._lock(id)
            if status != "rejected":
                raise invalid_transition("Only a rejected vendor can be revised.")
            self._set_status(id, status, "draft", None)

    # -- availability, suspend, reactivate -----------------------------------------------------

    def request_change(self, id: UUID, body: ChangeCreate) -> ChangeRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            status, _, _ = self._lock(id)
            if status != MOVES[body.kind][0]:
                raise invalid_transition(
                    f"A {status.replace('_', ' ')} vendor cannot take this change."
                )
            self._pending(id)
            payload: dict[str, JsonValue] = {}
            if body.kind == "availability":
                if body.payload is None:
                    raise ProblemError(
                        ErrorCode.VALIDATION_FAILED,
                        detail="Choose the new availability.",
                        errors=(ProblemFieldError(field="payload", message="Required."),),
                    )
                units = self._check_units(body.payload.unit_ids)
                self._shape(body.payload.availability, units)
                payload = {
                    "availability": body.payload.availability,
                    "unit_ids": [str(u) for u in units],
                }
            elif body.payload is not None:
                raise ProblemError(
                    ErrorCode.VALIDATION_FAILED,
                    detail="This change takes no payload.",
                    errors=(ProblemFieldError(field="payload", message="Remove it."),),
                )
            change_id = self._open_request(id, body.kind, payload, body.reason)
            self._start(change_id)
            return self.change(change_id)

    def withdraw_change(self, vendor_id: UUID, change_id: UUID) -> ChangeRead:
        """The requester withdraws while pending. The approval instance is locked first, as a
        decision locks it, so the two cannot deadlock."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._lock_free(vendor_id)
            row = self.change(change_id)
            if row.vendor_id != vendor_id:
                raise ProblemError(ErrorCode.NOT_FOUND)
            requester = self.repo.execute(
                "SELECT requested_by FROM vendor_change WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": change_id},
            ).fetchone()
            if requester is None or requester[0] != self.actor_id:
                raise ProblemError(
                    ErrorCode.FORBIDDEN, detail="Only the requester can withdraw this request."
                )
            if row.status != "pending":
                raise invalid_transition("Only a pending request can be withdrawn.")
            assert self.actor_id is not None
            withdraw(
                self.connection,
                self.scope,
                document_type="vendor",
                document_id=change_id,
                actor_id=self.actor_id,
                reason="Withdrawn by the requester",
            )
            return self.change(change_id)

    def inactivate(self, id: UUID, body: InactivateCreate) -> None:
        """An approver's own act: no approval, audited with the reason."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            status, _, _ = self._lock(id)
            if status not in ("active", "suspended"):
                raise invalid_transition("Only an active or suspended vendor can be inactivated.")
            self._pending(id)
            self.repo.execute(
                "UPDATE vendor SET status = 'inactive', updated_at = now() "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": id},
            )
            self._audit(
                "vendor.status_change",
                id,
                {"status": status},
                {"status": "inactive"},
                reason=body.reason,
            )

    # -- the decision ----------------------------------------------------------------------------

    def apply_outcome(self, change_id: UUID, outcome: Outcome) -> None:
        """Apply the approval's outcome inside the decision transaction (E10 calls it)."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            found = self.repo.execute(
                "SELECT vendor_id FROM vendor_change WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": change_id},
            ).fetchone()
            if found is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            vendor_id = cast(UUID, found[0])
            status, _, _ = self._lock(vendor_id)
            row = self.repo.execute(
                "SELECT kind, payload, status FROM vendor_change "
                "WHERE org_id = %(org_id)s AND id = %(id)s FOR UPDATE",
                {"id": change_id},
            ).fetchone()
            assert row is not None
            kind, payload, current = str(row[0]), cast(dict[str, object], row[1]), str(row[2])
            final = FINAL.get(outcome, "rejected")  # returned for changes is a rejection here
            # One outcome per request: an identical replay is a no-op, a different one is refused.
            if current != "pending":
                if current == final:
                    return
                raise conflict("invalid_transition", "This request was already decided.")
            expected, approved = MOVES[kind]
            to = status
            if outcome == Outcome.APPROVED:
                if status != expected:
                    raise invalid_transition(
                        f"The vendor is {status.replace('_', ' ')} now. Submit a new request."
                    )
                to = approved
                if kind == "availability":
                    units = self._check_units(
                        [UUID(str(u)) for u in cast(list[str], payload["unit_ids"])]
                    )
                    self._set_units(vendor_id, units)
                    self.repo.execute(
                        "UPDATE vendor SET availability = %(a)s, updated_at = now() "
                        "WHERE org_id = %(org_id)s AND id = %(id)s",
                        {"id": vendor_id, "a": payload["availability"]},
                    )
                    if payload["availability"] == "restricted":  # a local disable needs global
                        self.repo.execute(
                            "DELETE FROM vendor_local_disable "
                            "WHERE org_id = %(org_id)s AND vendor_id = %(id)s",
                            {"id": vendor_id},
                        )
            elif kind == "create":
                to = "draft" if outcome == Outcome.WITHDRAWN else "rejected"
            self.repo.execute(
                "UPDATE vendor_change SET status = %(status)s, decided_at = now() "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": change_id, "status": final},
            )
            if to != status or (kind == "availability" and outcome == Outcome.APPROVED):
                self._set_status(vendor_id, status, to, change_id)

    # -- reads for the decision panel --------------------------------------------------------------

    def evidence(self, change_id: UUID) -> dict[str, object]:
        """What an approver needs beside the request (E11-S15 renders it). It rides in the approval
        snapshot's material, so it is dated at the request, not at today, to stay stable."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            change = self.change(change_id)
            out: dict[str, object] = {
                "kind": change.kind,
                "reason": change.reason,
                # a sensitive field never leaves this module raw; revealing is the audited route
                "payload": {
                    k: sensitive.mask_text(v)
                    if k in sensitive.COLUMNS and isinstance(v, str)
                    else v
                    for k, v in change.payload.items()
                },
            }
            if change.kind == "create":
                out["duplicate_matches"] = self._duplicate_matches(change)
                out["document_state"] = [
                    d.model_dump(mode="json")
                    for d in self.document_state(change.vendor_id, change.requested_at.date())
                ]
                out["contacts"] = [
                    {"type": c.type, "name": c.name} for c in self.list_contacts(change.vendor_id)
                ]
            else:
                # ponytail: RFQs (E12) and POs (E13) do not exist yet; take both counts through
                # the link-graph providers when they register, never by importing those modules.
                if change.kind == "availability":
                    units = [str(u) for u in cast(list[str], change.payload.get("unit_ids", []))]
                    current = self.repo.execute(
                        "SELECT v.availability, coalesce(array_agg(u.code ORDER BY u.code) "
                        "FILTER (WHERE u.code IS NOT NULL), '{}') FROM vendor v "
                        "LEFT JOIN vendor_unit vu ON vu.org_id = v.org_id AND vu.vendor_id = v.id "
                        "LEFT JOIN org_unit u ON u.org_id = vu.org_id AND u.id = vu.unit_id "
                        "WHERE v.org_id = %(org_id)s AND v.id = %(id)s GROUP BY v.availability",
                        {"id": change.vendor_id},
                    ).fetchone()
                    assert current is not None
                    out["current_availability"] = str(current[0])
                    out["current_units"] = list(cast(list[str], current[1]))
                    out["new_units"] = [
                        str(r[0])
                        for r in self.repo.execute(
                            "SELECT code FROM org_unit WHERE org_id = %(org_id)s "
                            "AND id::text = ANY(%(ids)s) ORDER BY code",
                            {"ids": units},
                        ).fetchall()
                    ]
                out["open_rfqs"] = 0
                out["issued_purchase_orders"] = 0
            return out

    def _duplicate_matches(self, change: ChangeRead) -> list[dict[str, str]]:
        """Who holds the identifier the override waived, with the identifier masked."""
        if "duplicates" not in change.payload:
            return []
        out = []
        for field, match in duplicates.find(self.repo, vendor_id=change.vendor_id):
            row = self.repo.execute(
                "SELECT number, trading_name FROM vendor WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": match},
            ).fetchone()
            if row is None:
                continue
            if field == "tax_id":
                value = str(sensitive.masked(self.repo, [match]).get(match, {}).get("tax_id") or "")
            else:
                value = sensitive.mask_text(
                    duplicates.stored(self.repo, match).get(field.removeprefix("identifiers."), "")
                )
            out.append(
                {
                    "field": field,
                    "vendor_id": str(match),
                    "vendor_number": str(row[0]),
                    "trading_name": str(row[1]),
                    "masked_value": value,
                }
            )
        return out

    # -- live duplicate check --------------------------------------------------------------------

    def duplicate_check(
        self, tax_id: str | None, country: str | None, identifiers: dict[str, str]
    ) -> DuplicateResult:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            # the same master-code path as create and patch, so "duns" finds stored DUNS
            found = duplicates.find(
                self.repo,
                tax_id=tax_id,
                country=country,
                identifiers=self._identifiers(identifiers),
            )
            may_view = self._holds("vendor.view")
            matches = []
            for field, vendor_id in found:
                if not may_view:
                    matches.append(DuplicateMatch(field=field))
                    continue
                row = self.repo.execute(
                    "SELECT number, trading_name FROM vendor WHERE org_id = %(org_id)s "
                    "AND id = %(id)s",
                    {"id": vendor_id},
                ).fetchone()
                assert row is not None
                matches.append(
                    DuplicateMatch(
                        field=field,
                        vendor_id=vendor_id,
                        vendor_number=str(row[0]),
                        trading_name=str(row[1]),
                    )
                )
            return DuplicateResult(matches=matches)
