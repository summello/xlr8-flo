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
from flo.modules.org.service import NumberingService, OrgService, today
from flo.modules.vendors.models import (
    ANCESTORS_CTE,
    AVAILABLE_SQL,
    COLUMNS,
    FIELDS,
    FROM,
    VendorRepository,
)
from flo.modules.vendors.schemas import (
    DisableCreate,
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


def _cursor(name: str, id: UUID) -> str:
    return base64.urlsafe_b64encode(json.dumps([name, str(id)]).encode()).decode()


def _decode(cursor: str) -> tuple[str, UUID]:
    try:
        name, id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return str(name), UUID(id)
    except (ValueError, TypeError):
        raise invalid("cursor", "The page cursor is not valid.", "invalid_cursor") from None


class VendorService:
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
            base = self._row(id)
            disabled = self.repo.execute(
                "SELECT d.unit_id, u.code, i.email, d.disabled_at, d.reason "
                "FROM vendor_local_disable d JOIN org_unit u "
                "ON u.org_id = d.org_id AND u.id = d.unit_id "
                "JOIN identity i ON i.id = d.disabled_by "
                "WHERE d.org_id = %(org_id)s AND d.vendor_id = %(id)s ORDER BY u.code",
                {"id": id},
            ).fetchall()
            return VendorDetail(
                **base.model_dump(),
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
        more = len(rows) > limit
        return VendorPage(
            rows=page, next_cursor=_cursor(page[-1].trading_name, page[-1].id) if more else None
        )

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
    ) -> None:
        fields = tuple(after)
        AuditWriter(cast(AuditConnection, self.connection), self.scope).write(
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type="vendor",
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

    def create(self, body: VendorCreate) -> VendorDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
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
            self._audit(
                "vendor.create",
                id,
                None,
                {
                    "trading_name": body.trading_name,
                    "legal_name": body.legal_name,
                    "availability": body.availability,
                    "unit_ids": [str(u) for u in unit_ids],
                },
            )
        return self.get(id)

    def update(self, id: UUID, body: VendorPatch) -> VendorDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                "SELECT status FROM vendor WHERE org_id = %(org_id)s AND id = %(id)s FOR UPDATE",
                {"id": id},
            ).fetchone()
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            before = self._row(id)
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
                "availability = %(availability)s, updated_at = now() "
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
