"""Vendor record, searchable list, availability and local disable (VEN-001, 008, 009)."""

import base64
import json
from typing import cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.service import MasterCodeUnusable, NumberingService, OrgService, today
from flo.modules.vendors import duplicates
from flo.modules.vendors import repo as sensitive
from flo.modules.vendors.changes import ChangesMixin
from flo.modules.vendors.documents import DocumentsMixin
from flo.modules.vendors.models import (
    ANCESTORS_CTE,
    AVAILABLE_SQL,
    COLUMNS,
    FIELDS,
    FROM,
    VendorRepository,
)
from flo.modules.vendors.onboarding import OnboardingMixin
from flo.modules.vendors.performance import PerformanceMixin
from flo.modules.vendors.questionnaire import QuestionnaireMixin
from flo.modules.vendors.schemas import (
    REQUIRED_CONTACT_TYPES,
    ContactCreate,
    ContactPatch,
    ContactRead,
    DisableCreate,
    Recipient,
    SensitiveMasked,
    SensitivePut,
    SensitiveReveal,
    SensitiveValues,
    SensitiveWritten,
    VendorCreate,
    VendorDetail,
    VendorDisabled,
    VendorPage,
    VendorPatch,
    VendorRead,
    VendorStatus,
    VendorUnit,
    conflict,
    invalid,
)

SETTING = "vendor.allow_local_disable"
CONTACT_COLUMNS = (
    "id, type, name, email, phone, receives_documents, created_at, updated_at, version"
)
CONTACT_FIELDS = tuple(CONTACT_COLUMNS.replace(" ", "").split(","))


def expected_version(header: str | None, thing: str) -> int:
    """Parse If-Match (convention O): missing is 400, a non-integer is 400."""
    if header is None:
        raise ProblemError(
            ErrorCode.BAD_REQUEST,
            detail=f"Supply If-Match with the {thing} version.",
            checks={"problem": "if_match_required"},
        )
    try:
        return int(header.strip('"'))
    except ValueError as exc:
        raise ProblemError(
            ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
        ) from exc


def stale(thing: str) -> ProblemError:
    return ProblemError(
        ErrorCode.CONFLICT,
        detail=f"The {thing} changed. Reload it and retry your update.",
        checks={"problem": "stale_version"},
    )


def _cursor(name: str, id: UUID) -> str:
    return base64.urlsafe_b64encode(json.dumps([name, str(id)]).encode()).decode()


def _decode(cursor: str) -> tuple[str, UUID]:
    try:
        name, id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return str(name), UUID(id)
    except (ValueError, TypeError):
        raise invalid("cursor", "The page cursor is not valid.", "invalid_cursor") from None


class VendorService(
    ChangesMixin, DocumentsMixin, QuestionnaireMixin, OnboardingMixin, PerformanceMixin
):
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        actor_id: UUID | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.connection, self.scope = connection, scope
        self.actor_id, self.settings = actor_id, settings
        self.repo = VendorRepository(connection, scope)

    # -- reads -----------------------------------------------------------------------------

    def _read(self, row: tuple[object, ...]) -> VendorRead:
        return VendorRead.model_validate(dict(zip(FIELDS, row, strict=True)))

    def _row(self, id: UUID) -> VendorRead:
        row = self.repo.execute(
            f"SELECT {COLUMNS} {FROM} WHERE v.org_id = %(org_id)s AND v.id = %(id)s", {"id": id}
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return self._read(row)

    def _unit_codes(self, id: UUID) -> list[VendorUnit]:
        rows = self.repo.execute(
            "SELECT u.id, u.code FROM vendor_unit vu JOIN org_unit u "
            "ON u.org_id = vu.org_id AND u.id = vu.unit_id "
            "WHERE vu.org_id = %(org_id)s AND vu.vendor_id = %(id)s ORDER BY u.code",
            {"id": id},
        ).fetchall()
        return [VendorUnit(unit_id=cast(UUID, r[0]), unit_code=str(r[1])) for r in rows]

    def get(self, id: UUID) -> VendorDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            base = self._row(id).model_dump()
            base["sensitive"] = self._masked([id]).get(id, SensitiveMasked())
            disabled = self.repo.execute(
                "SELECT d.unit_id, u.code, i.email, d.disabled_at, d.reason "
                "FROM vendor_local_disable d JOIN org_unit u "
                "ON u.org_id = d.org_id AND u.id = d.unit_id "
                "JOIN identity i ON i.id = d.disabled_by "
                "WHERE d.org_id = %(org_id)s AND d.vendor_id = %(id)s ORDER BY u.code",
                {"id": id},
            ).fetchall()
            return VendorDetail(
                **base,
                identifiers=duplicates.stored(self.repo, id),
                units=self._unit_codes(id),
                disabled_in=[
                    VendorDisabled.model_validate(
                        dict(
                            zip(
                                ("unit_id", "unit_code", "by_email", "at", "reason"),
                                r,
                                strict=True,
                            )
                        )
                    )
                    for r in disabled
                ],
            )

    def search(
        self,
        *,
        q: str = "",
        statuses: list[VendorStatus] | None = None,
        available_in: UUID | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> VendorPage:
        if not 1 <= limit <= 100:
            raise invalid("limit", "Choose a limit from 1 to 100.")
        where, params = ["v.org_id = %(org_id)s"], cast(dict[str, object], {"limit": limit + 1})
        needle = q.strip().casefold()
        if needle:
            escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append(
                "(lower(v.trading_name) LIKE %(like)s OR lower(v.legal_name) LIKE %(like)s "
                "OR lower(v.number) LIKE %(like)s)"
            )
            params["like"] = "%" + escaped + "%"
        if statuses:
            where.append("v.status = ANY(%(statuses)s)")
            params["statuses"] = list(statuses)
        if cursor:
            name, last = _decode(cursor)
            where.append("(v.trading_name, v.id) > (%(c_name)s, %(c_id)s)")
            params.update(c_name=name, c_id=last)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            prefix = ""
            if available_in is not None:
                OrgService(self.connection, self.scope).get_unit(available_in)  # 404 if foreign
                prefix = ANCESTORS_CTE + " "
                where.append(AVAILABLE_SQL)
                params["unit_id"] = available_in
            rows = self.repo.execute(
                f"{prefix}SELECT {COLUMNS} {FROM} WHERE {' AND '.join(where)} "
                "ORDER BY v.trading_name, v.id LIMIT %(limit)s",
                params,
            ).fetchall()
        page = [self._read(r) for r in rows[:limit]]
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            masks = self._masked([v.id for v in page])
        page = [
            v.model_copy(update={"sensitive": masks.get(v.id, SensitiveMasked())}) for v in page
        ]
        more = len(rows) > limit
        return VendorPage(
            rows=page, next_cursor=_cursor(page[-1].trading_name, page[-1].id) if more else None
        )

    def _masked(self, ids: list[UUID]) -> dict[UUID, SensitiveMasked]:
        return {
            id: SensitiveMasked.model_validate(values)
            for id, values in sensitive.masked(self.repo, ids).items()
        }

    def reveal_sensitive(self, id: UUID, body: SensitiveReveal) -> SensitiveValues:
        """An audited read: the event names the fields and a reason code, never values."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(id)
            if body.note and sensitive.leaks(self.repo, id, body.note):
                raise invalid("note", "Leave account and tax numbers out of the note.")
            groups = list(dict.fromkeys(body.fields))
            reason = body.reason_code + (f": {body.note}" if body.note else "")
            self._audit("vendor.sensitive.reveal", id, None, {"fields": groups}, reason=reason)
            return SensitiveValues.model_validate(sensitive.reveal(self.repo, id, groups))

    def put_sensitive(self, id: UUID, body: SensitivePut, version: str | None) -> SensitiveWritten:
        if version is None:
            raise ProblemError(
                ErrorCode.BAD_REQUEST,
                detail="Supply If-Match with the sensitive record version.",
                checks={"problem": "if_match_required"},
            )
        try:
            expected = int(version.strip().strip('"'))
        except ValueError as exc:
            raise ProblemError(
                ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
            ) from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            if (
                self.repo.execute(
                    "SELECT 1 FROM vendor WHERE org_id = %(org_id)s AND id = %(id)s FOR UPDATE",
                    {"id": id},
                ).fetchone()
                is None
            ):
                raise ProblemError(ErrorCode.NOT_FOUND)
            written = sensitive.put(self.repo, id, body.model_dump(), self.actor_id, expected)
            if written is None:
                raise conflict(
                    "stale_version",
                    "The tax and banking record changed. Reload it and retry your update.",
                )
            changed, new_version = written
            self._audit("vendor.sensitive.update", id, None, {"fields": changed})
            return SensitiveWritten(changed=changed, version=new_version)

    def available_in(self, vendor_id: UUID, unit_id: UUID) -> bool:
        """The one implementation of the availability rule; eligibility (E11-S11) calls this."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            OrgService(self.connection, self.scope).get_unit(unit_id)
            self._row(vendor_id)
            row = self.repo.execute(
                f"{ANCESTORS_CTE} SELECT 1 FROM vendor v WHERE v.org_id = %(org_id)s "
                f"AND v.id = %(vendor_id)s AND {AVAILABLE_SQL}",
                {"vendor_id": vendor_id, "unit_id": unit_id},
            ).fetchone()
            return row is not None

    # -- writes ----------------------------------------------------------------------------

    def _audit(
        self,
        action: str,
        id: UUID,
        before: dict[str, object] | None,
        after: dict[str, object],
        reason: str | None = None,
        target_type: str = "vendor",
    ) -> None:
        fields = tuple(after)
        AuditWriter(cast(AuditConnection, self.connection), self.scope).write(
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type=target_type,
            target_id=id,
            outcome=Outcome.SUCCESS,
            reason=reason,
            before_source=before,
            before_fields=tuple(before or ()),
            after_source=after,
            after_fields=fields,
        )

    def _check_units(self, unit_ids: list[UUID]) -> list[UUID]:
        unique = list(dict.fromkeys(unit_ids))
        found = {
            cast(UUID, r[0])
            for r in self.repo.execute(
                "SELECT id FROM org_unit WHERE org_id = %(org_id)s AND id = ANY(%(ids)s)",
                {"ids": unique},
            ).fetchall()
        }
        if missing := [u for u in unique if u not in found]:
            raise invalid(
                "unit_ids", f"Unknown unit: {missing[0]}. Choose units of this organization."
            )
        return unique

    @staticmethod
    def _shape(availability: str, unit_ids: list[UUID]) -> None:
        if availability == "restricted" and not unit_ids:
            raise invalid("unit_ids", "Choose at least one business or organizational unit.")
        if availability == "global" and unit_ids:
            raise invalid("unit_ids", "A global vendor has no units. Remove them or restrict it.")

    def _set_units(self, id: UUID, unit_ids: list[UUID]) -> None:
        self.repo.execute(
            "DELETE FROM vendor_unit WHERE org_id = %(org_id)s AND vendor_id = %(id)s", {"id": id}
        )
        for unit_id in unit_ids:
            self.repo.execute(
                "INSERT INTO vendor_unit (org_id, vendor_id, unit_id) "
                "VALUES (%(org_id)s, %(id)s, %(unit_id)s)",
                {"id": id, "unit_id": unit_id},
            )

    def _identifiers(self, values: dict[str, str]) -> dict[str, str]:
        """Type codes must be active vendor_identifier_type master codes; values 1..64 once
        normalised. Returns {master code: value}."""
        clean: dict[str, str] = {}
        for code, value in values.items():
            field = f"identifiers.{code}"
            try:
                ref = OrgService(self.connection, self.scope).assert_usable(
                    duplicates.KIND, code, today()
                )
            except MasterCodeUnusable:
                raise invalid(field, "Choose a defined identifier type.") from None
            if not 1 <= len(duplicates.normalise(value)) <= 64:
                raise invalid(field, "Enter 1 to 64 letters or digits.")
            if ref.code in clean:
                raise invalid(field, "Give each identifier type once.")
            clean[ref.code] = value
        return clean

    def create(self, body: VendorCreate) -> VendorDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            identifiers = self._identifiers(body.identifiers)
            self._shape(body.availability, body.unit_ids)
            unit_ids = self._check_units(body.unit_ids)
            id = uuid4()
            # Vendors belong to the organization, so the number is allocated org-wide.
            number = NumberingService(self.settings or Settings(), self.scope).allocate(
                self.scope.org_id, "vendor", today()
            )
            self.repo.execute(
                "INSERT INTO vendor (id, org_id, number, trading_name, legal_name, availability, "
                "created_by) VALUES (%(id)s, %(org_id)s, %(number)s, %(trading)s, %(legal)s, "
                "%(availability)s, %(actor)s)",
                {
                    "id": id,
                    "number": number,
                    "trading": body.trading_name,
                    "legal": body.legal_name,
                    "availability": body.availability,
                    "actor": self.actor_id,
                },
            )
            self._set_units(id, unit_ids)
            duplicates.store(self.repo, id, identifiers)
            self._audit(
                "vendor.create",
                id,
                None,
                {
                    "trading_name": body.trading_name,
                    "legal_name": body.legal_name,
                    "availability": body.availability,
                    "unit_ids": [str(u) for u in unit_ids],
                    "identifier_types": sorted(identifiers),
                },
            )
        return self.get(id)

    def update(self, id: UUID, body: VendorPatch, if_match: str | None) -> VendorDetail:
        expected = expected_version(if_match, "vendor")
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                "SELECT status FROM vendor WHERE org_id = %(org_id)s AND id = %(id)s FOR UPDATE",
                {"id": id},
            ).fetchone()
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            before = self._row(id)
            if before.version != expected:
                raise stale("vendor")
            before_units = [u.unit_id for u in self._unit_codes(id)]
            availability = body.availability or before.availability
            changes_availability = body.availability not in (None, before.availability) or (
                body.unit_ids is not None
            )
            if changes_availability and row[0] != "draft":
                raise conflict(
                    "availability_needs_approval",
                    "Availability changes on a submitted vendor go through approval.",
                )
            if body.identifiers is not None:
                if row[0] != "draft":  # a changed identifier would skip the duplicate check
                    raise conflict(
                        "identifiers_locked",
                        "Identifiers can change only while the vendor is a draft.",
                    )
                duplicates.store(self.repo, id, self._identifiers(body.identifiers))
            units = before_units
            if changes_availability:
                if body.unit_ids is not None:
                    units = self._check_units(body.unit_ids)
                elif availability == "global":
                    units = []
                self._shape(availability, units)
                self._set_units(id, units)
                if availability == "restricted":  # a local disable only means something on global
                    self.repo.execute(
                        "DELETE FROM vendor_local_disable "
                        "WHERE org_id = %(org_id)s AND vendor_id = %(id)s",
                        {"id": id},
                    )
            trading = body.trading_name or before.trading_name
            legal = body.legal_name or before.legal_name
            self.repo.execute(
                "UPDATE vendor SET trading_name = %(trading)s, legal_name = %(legal)s, "
                "availability = %(availability)s, updated_at = now(), version = version + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": id, "trading": trading, "legal": legal, "availability": availability},
            )
            self._audit(
                "vendor.update",
                id,
                {
                    "trading_name": before.trading_name,
                    "legal_name": before.legal_name,
                    "availability": before.availability,
                    "unit_ids": [str(u) for u in before_units],
                },
                {
                    "trading_name": trading,
                    "legal_name": legal,
                    "availability": availability,
                    "unit_ids": [str(u) for u in units],
                },
            )
        return self.get(id)

    def disable_locally(self, id: UUID, unit_id: UUID, body: DisableCreate) -> VendorDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            vendor = self._row(id)
            OrgService(self.connection, self.scope).get_unit(unit_id)
            if vendor.availability != "global":
                raise conflict("not_global", "Only a global vendor can be disabled in a unit.")
            allowed = OrgService(self.connection, self.scope).effective_setting(unit_id, SETTING)
            if allowed.value is not True:
                raise conflict(
                    "local_disable_not_allowed",
                    "Local disable is not allowed for this unit. Ask an administrator "
                    "to turn on vendor.allow_local_disable.",
                )
            inserted = self.repo.execute(
                "INSERT INTO vendor_local_disable "
                "(org_id, vendor_id, unit_id, disabled_by, reason) "
                "VALUES (%(org_id)s, %(id)s, %(unit_id)s, %(actor)s, %(reason)s) "
                "ON CONFLICT (vendor_id, unit_id) DO NOTHING RETURNING 1",
                {"id": id, "unit_id": unit_id, "actor": self.actor_id, "reason": body.reason},
            ).fetchone()
            if inserted is None:
                raise conflict("already_disabled", "This vendor is already disabled in the unit.")
            self._audit(
                "vendor.local_disable", id, None, {"unit_id": str(unit_id)}, reason=body.reason
            )
        return self.get(id)

    def enable_locally(self, id: UUID, unit_id: UUID) -> VendorDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(id)
            OrgService(self.connection, self.scope).get_unit(unit_id)
            deleted = self.repo.execute(
                "DELETE FROM vendor_local_disable WHERE org_id = %(org_id)s "
                "AND vendor_id = %(id)s AND unit_id = %(unit_id)s RETURNING reason",
                {"id": id, "unit_id": unit_id},
            ).fetchone()
            if deleted is None:
                raise conflict("not_disabled", "This vendor is not disabled in the unit.")
            self._audit(
                "vendor.local_enable",
                id,
                {"unit_id": str(unit_id)},
                {"unit_id": str(unit_id)},
                reason=str(deleted[0]),
            )
        return self.get(id)

    # -- typed contacts (E11-S02) ------------------------------------------------------------

    def _contact(self, vendor_id: UUID, contact_id: UUID, lock: bool = False) -> ContactRead:
        row = self.repo.execute(
            f"SELECT {CONTACT_COLUMNS} FROM vendor_contact WHERE org_id = %(org_id)s "
            f"AND vendor_id = %(vendor_id)s AND id = %(id)s{' FOR UPDATE' if lock else ''}",
            {"vendor_id": vendor_id, "id": contact_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return ContactRead.model_validate(dict(zip(CONTACT_FIELDS, row, strict=True)))

    def _contact_audit(
        self, action: str, vendor_id: UUID, before: ContactRead | None, after: ContactRead
    ) -> None:
        # The phone is deliberately not recorded: minimise personal data in the log.
        keep = {"id", "type", "name", "email", "receives_documents"}
        self._audit(
            action,
            vendor_id,
            before.model_dump(mode="json", include=keep) if before else None,
            after.model_dump(mode="json", include=keep),
        )

    def list_contacts(self, vendor_id: UUID) -> list[ContactRead]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            rows = self.repo.execute(
                f"SELECT {CONTACT_COLUMNS} FROM vendor_contact WHERE org_id = %(org_id)s "
                "AND vendor_id = %(vendor_id)s ORDER BY "
                "CASE type WHEN 'billing' THEN 0 WHEN 'service_delivery' THEN 1 ELSE 2 END, "
                "lower(name), id",
                {"vendor_id": vendor_id},
            ).fetchall()
        return [ContactRead.model_validate(dict(zip(CONTACT_FIELDS, r, strict=True))) for r in rows]

    def _contact_type(self, value: str) -> str:
        value = value.strip()
        if value in REQUIRED_CONTACT_TYPES:
            return value
        try:
            return (
                OrgService(self.connection, self.scope)
                .assert_usable("vendor_contact_type", value, today())
                .code
            )
        except MasterCodeUnusable:
            raise invalid(
                "type", "Choose billing, service_delivery or a defined contact type."
            ) from None

    def create_contact(self, vendor_id: UUID, body: ContactCreate) -> ContactRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            type = self._contact_type(body.type)
            id = uuid4()
            receives = (
                body.receives_documents
                if body.receives_documents is not None
                else type == "service_delivery"
            )
            inserted = self.repo.execute(
                "INSERT INTO vendor_contact (id, org_id, vendor_id, type, name, email, phone, "
                "receives_documents) VALUES (%(id)s, %(org_id)s, %(vendor_id)s, %(type)s, "
                "%(name)s, %(email)s, %(phone)s, %(receives)s) "
                "ON CONFLICT (vendor_id, type) WHERE type IN ('billing','service_delivery') "
                "DO NOTHING RETURNING 1",
                {
                    "id": id,
                    "vendor_id": vendor_id,
                    "type": type,
                    "name": body.name,
                    "email": body.email,
                    "phone": body.phone,
                    "receives": receives,
                },
            ).fetchone()
            if inserted is None:
                raise conflict(
                    "contact_type_taken", f"This vendor already has a {type} contact. Edit it."
                )
            created = self._contact(vendor_id, id)
            self._contact_audit("vendor.contact.create", vendor_id, None, created)
        return created

    def update_contact(
        self, vendor_id: UUID, contact_id: UUID, body: ContactPatch, if_match: str | None
    ) -> ContactRead:
        expected = expected_version(if_match, "contact")
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            before = self._contact(vendor_id, contact_id, lock=True)
            if before.version != expected:
                raise stale("contact")
            given = body.model_fields_set
            for field in ("name", "email", "receives_documents"):
                if field in given and getattr(body, field) is None:
                    raise invalid(field, "This field cannot be empty.")
            values = {f: getattr(body, f) for f in given}
            self.repo.execute(
                "UPDATE vendor_contact SET name = %(name)s, email = %(email)s, phone = %(phone)s, "
                "receives_documents = %(receives)s, updated_at = now(), version = version + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {
                    "id": contact_id,
                    "name": values.get("name", before.name),
                    "email": values.get("email", before.email),
                    "phone": values["phone"] if "phone" in given else before.phone,
                    "receives": values.get("receives_documents", before.receives_documents),
                },
            )
            after = self._contact(vendor_id, contact_id)
            self._contact_audit("vendor.contact.update", vendor_id, before, after)
        return after

    def delete_contact(self, vendor_id: UUID, contact_id: UUID) -> None:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            before = self._contact(vendor_id, contact_id, lock=True)
            if before.type in REQUIRED_CONTACT_TYPES:
                raise conflict(
                    "required_contact", f"The {before.type} contact is required. Edit it instead."
                )
            self.repo.execute(
                "DELETE FROM vendor_contact WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": contact_id},
            )
            self._contact_audit("vendor.contact.delete", vendor_id, before, before)

    def contacts_complete(self, vendor_id: UUID) -> list[str]:
        """Names the required contact types the vendor still lacks (E11-S06 calls this)."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            have = {
                str(r[0])
                for r in self.repo.execute(
                    "SELECT type FROM vendor_contact WHERE org_id = %(org_id)s "
                    "AND vendor_id = %(vendor_id)s",
                    {"vendor_id": vendor_id},
                ).fetchall()
            }
        return [t for t in REQUIRED_CONTACT_TYPES if t not in have]

    def document_recipients(self, vendor_id: UUID) -> list[Recipient]:
        """Who gets RFQs and POs: flagged contacts, service_delivery first. May be empty."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            rows = self.repo.execute(
                "SELECT name, email FROM vendor_contact WHERE org_id = %(org_id)s "
                "AND vendor_id = %(vendor_id)s AND receives_documents "
                "ORDER BY (type = 'service_delivery') DESC, lower(name), id",
                {"vendor_id": vendor_id},
            ).fetchall()
        return [Recipient(name=str(r[0]), email=str(r[1])) for r in rows]
