"""Transactional unit structure and nearest-scope settings resolution."""

from datetime import UTC, date, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from flo.kernel import setting_guards
from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.identity.service import (
    IdentityAuthorizationConnection,
    register_business_unit_scope,
)
from flo.modules.org.fiscal import FiscalPeriod
from flo.modules.org.fiscal import FiscalService as FiscalService
from flo.modules.org.fiscal import PeriodClosed as PeriodClosed
from flo.modules.org.master_kinds import known_kind, validate_attributes
from flo.modules.org.models import OrgRepository
from flo.modules.org.numbering import NumberingService as NumberingService
from flo.modules.org.schemas import (
    CurrencyRead,
    EffectiveSetting,
    MasterCreate,
    MasterPage,
    MasterPatch,
    MasterRef,
    OrgAddressClose,
    OrgAddressCreate,
    OrgAddressRead,
    OrgUnitCreate,
    OrgUnitPage,
    OrgUnitPatch,
    OrgUnitRead,
    SettingPut,
    SettingSource,
)
from flo.modules.org.settings import SETTING_DEFAULTS, SETTING_VALIDATORS


def known_key(key: str) -> None:
    if key not in SETTING_DEFAULTS:
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED,
            detail="Unknown setting key. Use a key from the settings registry.",
            errors=(ProblemFieldError(field="key", message="Unknown setting key."),),
            checks={"problem": "unknown_key"},
        )


def valid_value(key: str, value: JsonValue) -> None:
    validator = SETTING_VALIDATORS.get(key)
    if validator is not None and not validator(value):
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED,
            detail="This value is not allowed for this setting. Use a value of the right type.",
            errors=(ProblemFieldError(field="value", message="Invalid value for this setting."),),
            checks={"problem": "invalid_setting_value"},
        )


def today() -> date:
    return datetime.now(UTC).date()


class OrgService:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        actor_id: UUID | None = None,
    ) -> None:
        self.connection = connection
        self.scope = scope
        self.repo = OrgRepository(connection, scope)
        self.actor_id = actor_id

    def _audit(
        self,
        action: str,
        target_id: UUID,
        before: dict[str, object] | None,
        after: dict[str, object] | None,
    ) -> None:
        AuditWriter(cast(AuditConnection, self.connection), self.scope).write(
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type=action.split(".")[0],
            target_id=target_id,
            outcome=Outcome.SUCCESS,
            before_source=before,
            before_fields=tuple(before or ()),
            after_source=after,
            after_fields=tuple(after or ()),
        )

    def _unit(self, unit_id: UUID) -> dict[str, object]:
        row = self.repo.unit(unit_id)
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return row

    def get_unit(self, unit_id: UUID) -> OrgUnitRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return OrgUnitRead.model_validate(self._unit(unit_id))

    def create_unit(self, body: OrgUnitCreate) -> OrgUnitRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.repo.lock_organization()
            if body.parent_id is not None:
                self._unit(body.parent_id)
                depth = self.repo.execute(
                    """
                    WITH RECURSIVE parents AS (
                      SELECT id, parent_id, 1 AS depth FROM org_unit
                       WHERE org_id = %(org_id)s AND id = %(parent_id)s
                      UNION ALL
                      SELECT u.id, u.parent_id, p.depth + 1
                        FROM org_unit u JOIN parents p ON u.id = p.parent_id
                       WHERE u.org_id = %(org_id)s)
                    SELECT max(depth) FROM parents
                    """,
                    {"parent_id": body.parent_id},
                ).fetchone()
                if depth is not None and cast(int, depth[0]) >= 5:
                    raise ProblemError(
                        ErrorCode.VALIDATION_FAILED,
                        detail="Units support at most 5 levels. Choose a shallower parent.",
                        errors=(ProblemFieldError(field="parent_id", message="Depth limit is 5."),),
                        checks={"problem": "depth_limit"},
                    )
            unit_id = uuid4()
            inserted = self.repo.execute(
                """
                INSERT INTO org_unit (id, org_id, parent_id, code, name, kind)
                VALUES (%(id)s, %(org_id)s, %(parent_id)s, %(code)s, %(name)s, %(kind)s)
                ON CONFLICT (org_id, code) DO NOTHING RETURNING id
                """,
                {
                    "id": unit_id,
                    "parent_id": body.parent_id,
                    "code": body.code.upper(),
                    "name": body.name,
                    "kind": body.kind,
                },
            ).fetchone()
            if inserted is None:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="This unit code already exists. Choose a different code.",
                    checks={"problem": "duplicate_code"},
                )
            register_business_unit_scope(
                cast(IdentityAuthorizationConnection, self.connection), self.scope, unit_id
            )
            row = self._unit(unit_id)
            self._audit("org_unit.create", unit_id, None, row)
            return OrgUnitRead.model_validate(row)

    def list_units(
        self,
        *,
        kind: Literal["bu", "ou"] | None,
        active: bool | None,
        cursor: UUID | None,
        page_size: int,
    ) -> OrgUnitPage:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rows = self.repo.execute(
                """
                SELECT id FROM org_unit WHERE org_id = %(org_id)s
                AND (%(kind)s::text IS NULL OR kind = %(kind)s)
                AND (%(active)s::boolean IS NULL OR active = %(active)s)
                AND (%(cursor)s::uuid IS NULL OR id > %(cursor)s)
                ORDER BY id LIMIT %(limit)s
                """,
                {"kind": kind, "active": active, "cursor": cursor, "limit": page_size + 1},
            ).fetchall()
            units = [
                OrgUnitRead.model_validate(self._unit(cast(UUID, row[0])))
                for row in rows[:page_size]
            ]
            return OrgUnitPage(
                rows=units, next_cursor=units[-1].id if len(rows) > page_size else None
            )

    def update_unit(self, unit_id: UUID, body: OrgUnitPatch) -> OrgUnitRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.repo.lock_organization()
            before = self._unit(unit_id)
            if body.active is False:
                children = self.repo.execute(
                    """SELECT code FROM org_unit
                    WHERE org_id = %(org_id)s AND parent_id = %(id)s AND active
                    ORDER BY code""",
                    {"id": unit_id},
                ).fetchall()
                if children:
                    raise ProblemError(
                        ErrorCode.CONFLICT,
                        detail="Deactivate the active child units first: "
                        + ", ".join(cast(str, row[0]) for row in children)
                        + ".",
                        checks={"problem": "active_children"},
                    )
            self.repo.execute(
                """UPDATE org_unit
                SET name = COALESCE(%(name)s, name), active = COALESCE(%(active)s, active)
                WHERE org_id = %(org_id)s AND id = %(id)s""",
                {"id": unit_id, "name": body.name, "active": body.active},
            )
            after = self._unit(unit_id)
            self._audit(
                "org_unit.deactivate"
                if before["active"] and body.active is False
                else "org_unit.update",
                unit_id,
                before,
                after,
            )
            return OrgUnitRead.model_validate(after)

    def set_setting(self, key: str, body: SettingPut) -> SettingPut:
        known_key(key)
        valid_value(key, body.value)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.repo.lock_organization()
            if body.unit_id is not None:
                self._unit(body.unit_id)
            before = self.repo.execute(
                """SELECT id, value FROM org_setting
                WHERE org_id = %(org_id)s AND unit_id IS NOT DISTINCT FROM %(unit_id)s::uuid
                AND key = %(key)s""",
                {"unit_id": body.unit_id, "key": key},
            ).fetchone()
            if before is None or before[1] != body.value:
                setting_guards.check(key, self.connection, self.scope, body.unit_id, body.value)
            result = self.repo.execute(
                """
                INSERT INTO org_setting (id, org_id, unit_id, key, value, updated_by)
                VALUES (%(id)s, %(org_id)s, %(unit_id)s, %(key)s, %(value)s, %(actor)s)
                ON CONFLICT (org_id, unit_id, key) DO UPDATE
                SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()
                WHERE org_setting.value IS DISTINCT FROM EXCLUDED.value RETURNING id
                """,
                {
                    "id": uuid4(),
                    "unit_id": body.unit_id,
                    "key": key,
                    "value": Jsonb(body.value),
                    "actor": self.actor_id,
                },
            ).fetchone()
            if result is not None:
                self._audit(
                    "org_setting.set",
                    cast(UUID, result[0]),
                    {"key": key, "unit_id": body.unit_id, "value": before[1]} if before else None,
                    {"key": key, "unit_id": body.unit_id, "value": body.value},
                )
            return body

    def clear_setting(self, key: str, unit_id: UUID | None) -> None:
        known_key(key)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.repo.lock_organization()
            if unit_id is not None:
                self._unit(unit_id)
            present = self.repo.execute(
                "SELECT 1 FROM org_setting WHERE org_id=%(org_id)s "
                "AND unit_id IS NOT DISTINCT FROM %(unit_id)s::uuid AND key=%(key)s",
                {"unit_id": unit_id, "key": key},
            ).fetchone()
            if present:
                setting_guards.check(key, self.connection, self.scope, unit_id, None)
            row = self.repo.execute(
                """DELETE FROM org_setting
                WHERE org_id = %(org_id)s AND unit_id IS NOT DISTINCT FROM %(unit_id)s::uuid
                AND key = %(key)s RETURNING id, value""",
                {"unit_id": unit_id, "key": key},
            ).fetchone()
            if row is not None:
                self._audit(
                    "org_setting.clear",
                    cast(UUID, row[0]),
                    {"key": key, "unit_id": unit_id, "value": row[1]},
                    None,
                )

    def effective_setting(self, unit_id: UUID, key: str) -> EffectiveSetting:
        known_key(key)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                """
                WITH RECURSIVE ancestors AS (
                  SELECT id, parent_id, code, 0 AS distance FROM org_unit
                   WHERE org_id = %(org_id)s AND id = %(unit_id)s
                  UNION ALL
                  SELECT u.id, u.parent_id, u.code, a.distance + 1
                    FROM org_unit u JOIN ancestors a ON u.id = a.parent_id
                   WHERE u.org_id = %(org_id)s
                ), candidates AS (
                  SELECT s.value, 'unit' AS source, a.id, a.code, a.distance
                    FROM ancestors a JOIN org_setting s ON s.unit_id = a.id
                   WHERE s.org_id = %(org_id)s AND s.key = %(key)s
                  UNION ALL
                  SELECT value, 'org', NULL::uuid, NULL::text, 6 FROM org_setting
                   WHERE org_id = %(org_id)s AND unit_id IS NULL AND key = %(key)s
                  UNION ALL
                  SELECT %(default)s::jsonb, 'default', NULL::uuid, NULL::text, 7)
                SELECT value, source, id, code FROM candidates
                 WHERE EXISTS (SELECT 1 FROM ancestors) ORDER BY distance LIMIT 1
                """,
                {"unit_id": unit_id, "key": key, "default": Jsonb(SETTING_DEFAULTS[key])},
            ).fetchone()
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            return EffectiveSetting(
                value=cast(JsonValue, row[0]),
                source=SettingSource(
                    scope=cast(Literal["unit", "org", "default"], row[1]),
                    unit_id=cast(UUID | None, row[2]),
                    unit_code=cast(str | None, row[3]),
                ),
            )

    def get_address(self, unit_id: UUID, address_id: UUID) -> OrgAddressRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._unit(unit_id)
            row = self.repo.address(unit_id, address_id)
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            return OrgAddressRead.model_validate(row)

    def create_address(self, unit_id: UUID, body: OrgAddressCreate) -> OrgAddressRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.repo.lock_organization()
            self._unit(unit_id)
            conflict = self.repo.execute(
                """SELECT id FROM org_address WHERE org_id = %(org_id)s
                AND unit_id = %(unit_id)s AND kind = %(kind)s
                AND daterange(effective_from, effective_to, '[]') &&
                    daterange(%(effective_from)s, %(effective_to)s, '[]')""",
                body.model_dump() | {"unit_id": unit_id},
            ).fetchone()
            if conflict:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=(
                        f"Address overlaps existing row {conflict[0]}. "
                        "Close it or choose non-overlapping dates."
                    ),
                    checks={"problem": "address_overlap", "address_id": str(conflict[0])},
                )
            address_id = uuid4()
            self.repo.execute(
                """INSERT INTO org_address
                (id, org_id, unit_id, kind, line1, line2, city, region, postal_code,
                 country, effective_from, effective_to, created_by)
                VALUES (%(id)s, %(org_id)s, %(unit_id)s, %(kind)s, %(line1)s,
                %(line2)s, %(city)s, %(region)s, %(postal_code)s, %(country)s,
                %(effective_from)s, %(effective_to)s, %(actor)s)""",
                body.model_dump() | {"id": address_id, "unit_id": unit_id, "actor": self.actor_id},
            )
            row = self.repo.address(unit_id, address_id)
            self._audit("org_address.create", address_id, None, row)
            return OrgAddressRead.model_validate(row)

    def list_addresses(
        self, unit_id: UUID, kind: Literal["bill_to", "ship_to"] | None, as_of: date | None
    ) -> list[OrgAddressRead]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._unit(unit_id)
            rows = self.repo.execute(
                """SELECT id FROM org_address WHERE org_id = %(org_id)s
                AND unit_id = %(unit_id)s AND (%(kind)s::text IS NULL OR kind = %(kind)s)
                AND daterange(effective_from, effective_to, '[]') @> %(as_of)s::date
                ORDER BY kind, id""",
                {"unit_id": unit_id, "kind": kind, "as_of": as_of or today()},
            ).fetchall()
            return [
                OrgAddressRead.model_validate(self.repo.address(unit_id, row[0])) for row in rows
            ]

    def close_address(
        self, unit_id: UUID, address_id: UUID, body: OrgAddressClose
    ) -> OrgAddressRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.repo.lock_organization()
            self._unit(unit_id)
            before = self.repo.address(unit_id, address_id)
            if before is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            if before["effective_to"] is not None:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=(
                        "This address is already closed. Create a new address to change history."
                    ),
                    checks={"problem": "address_closed"},
                )
            if body.effective_to < cast(date, before["effective_from"]):
                raise ProblemError(
                    ErrorCode.VALIDATION_FAILED,
                    detail=(
                        "Closing date precedes the start. Choose a date on or after effective_from."
                    ),
                    errors=(
                        ProblemFieldError(
                            field="effective_to", message="Must be on or after effective_from."
                        ),
                    ),
                )
            self.repo.execute(
                """UPDATE org_address SET effective_to = %(effective_to)s
                WHERE org_id = %(org_id)s AND unit_id = %(unit_id)s AND id = %(id)s""",
                {"unit_id": unit_id, "id": address_id, "effective_to": body.effective_to},
            )
            after = self.repo.address(unit_id, address_id)
            self._audit("org_address.close", address_id, before, after)
            return OrgAddressRead.model_validate(after)

    def get_master(self, kind: str, record_id: UUID) -> MasterRef:
        known_kind(kind)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.master(kind, record_id)
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            return MasterRef.model_validate(row)

    def create_master(self, kind: str, body: MasterCreate) -> MasterRef:
        validate_attributes(kind, dict(body.attributes))
        validate_master_dates(body.effective_from, body.effective_to)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            record_id = uuid4()
            inserted = self.repo.execute(
                """INSERT INTO master_record
                (id, org_id, kind, code, name, attributes, effective_from, effective_to, active)
                VALUES (%(id)s, %(org_id)s, %(kind)s, %(code)s, %(name)s, %(attributes)s,
                %(effective_from)s, %(effective_to)s, %(active)s)
                ON CONFLICT (org_id, kind, code) DO NOTHING RETURNING id""",
                body.model_dump()
                | {
                    "id": record_id,
                    "kind": kind,
                    "code": body.code.strip().upper(),
                    "attributes": Jsonb(body.attributes),
                },
            ).fetchone()
            if inserted is None:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="This master code already exists. Choose a different code.",
                    checks={"problem": "duplicate_code"},
                )
            row = self.repo.master(kind, record_id)
            self._audit("master_record.create", record_id, None, row)
            return MasterRef.model_validate(row)

    def update_master(self, kind: str, record_id: UUID, body: MasterPatch) -> MasterRef:
        known_kind(kind)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            before = self.repo.master(kind, record_id, lock=True)
            if before is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            after = before | body.model_dump(exclude_unset=True)
            validate_attributes(kind, cast(dict[str, object], after["attributes"]))
            validate_master_dates(
                cast(date, after["effective_from"]), cast(date | None, after["effective_to"])
            )
            if after != before:
                self.repo.execute(
                    """UPDATE master_record SET name = %(name)s, attributes = %(attributes)s,
                    effective_from = %(effective_from)s, effective_to = %(effective_to)s,
                    active = %(active)s, updated_at = now()
                    WHERE org_id = %(org_id)s AND kind = %(kind)s AND id = %(id)s""",
                    after | {"attributes": Jsonb(after["attributes"])},
                )
                after = cast(dict[str, object], self.repo.master(kind, record_id))
                self._audit("master_record.update", record_id, before, after)
            return MasterRef.model_validate(after)

    def deactivate_master(self, kind: str, record_id: UUID) -> MasterRef:
        return self.update_master(kind, record_id, MasterPatch(active=False))

    def list_master(
        self,
        kind: str,
        *,
        q: str = "",
        active: bool | None = None,
        as_of: date | None = None,
        cursor: UUID | None = None,
        page_size: int = 50,
    ) -> MasterPage:
        known_kind(kind)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            prefix = (
                q.strip().upper().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            rows = self.repo.execute(
                """SELECT id FROM master_record WHERE org_id = %(org_id)s AND kind = %(kind)s
                AND (code LIKE %(prefix)s OR upper(name) LIKE %(prefix)s)
                AND (%(active)s::boolean IS NULL OR active = %(active)s)
                AND (%(as_of)s::date IS NULL OR (effective_from <= %(as_of)s
                  AND (effective_to IS NULL OR effective_to >= %(as_of)s)))
                AND (%(cursor)s::uuid IS NULL OR id > %(cursor)s)
                ORDER BY id LIMIT %(limit)s""",
                {
                    "kind": kind,
                    "prefix": prefix,
                    "active": active,
                    "as_of": as_of,
                    "cursor": cursor,
                    "limit": min(50, max(1, page_size)) + 1,
                },
            ).fetchall()
            size = min(50, max(1, page_size))
            records = [
                MasterRef.model_validate(self.repo.master(kind, row[0])) for row in rows[:size]
            ]
            return MasterPage(
                rows=records, next_cursor=records[-1].id if len(rows) > size else None
            )

    def assert_usable(self, kind: str, code: str, on_date: date) -> MasterRef:
        known_kind(kind)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                """SELECT id FROM master_record WHERE org_id = %(org_id)s
                AND kind = %(kind)s AND code = %(code)s""",
                {"kind": kind, "code": code.strip().upper()},
            ).fetchone()
            if row is None:
                raise MasterCodeUnusable(code, "unknown")
            ref = MasterRef.model_validate(self.repo.master(kind, row[0]))
            if not ref.active:
                raise MasterCodeUnusable(code, "inactive")
            if on_date < ref.effective_from or (
                ref.effective_to is not None and on_date > ref.effective_to
            ):
                raise MasterCodeUnusable(code, "not_effective")
            return ref


class MasterCodeUnusable(ValueError):
    def __init__(self, code: str, reason: Literal["unknown", "inactive", "not_effective"]) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"Master code {code} is {reason}")


def validate_master_dates(start: date, end: date | None) -> None:
    if end is not None and end < start:
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED,
            detail="Effective end precedes the start. Choose an end on or after the start.",
            errors=(
                ProblemFieldError(
                    field="effective_to", message="Must be on or after effective_from."
                ),
            ),
        )


def assert_usable(
    connection: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    kind: str,
    code: str,
    on_date: date,
) -> MasterRef:
    """Scoped service entry point for transaction consumers."""
    return OrgService(connection, scope).assert_usable(kind, code, on_date)


def list_currencies(q: str = "") -> list[CurrencyRead]:
    import json
    from importlib.resources import files

    data = json.loads(files("flo.kernel.data").joinpath("iso4217.json").read_text())
    return [
        CurrencyRead(code=code, exponent=exponent)
        for code, exponent in sorted(data.items())
        if code.startswith(q.strip().upper())
    ]


def period_for(
    connection: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    effective_date: date,
) -> FiscalPeriod:
    return FiscalService(connection, scope).period_for(effective_date)


def assert_postable(
    connection: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    effective_date: date,
) -> None:
    FiscalService(connection, scope).assert_postable(effective_date)


def range_for(
    connection: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    kind: Literal["mtd", "qtd", "ytd", "fiscal_year"],
    as_of: date,
) -> tuple[date, date]:
    return FiscalService(connection, scope).range_for(kind, as_of)


def create_organization(
    conn: psycopg.Connection[tuple[object, ...]], *, name: str, base_currency: str | None = None
) -> UUID:
    """Create the organization under its own RLS context; defaults remain in code."""
    if not name.strip():
        raise ValueError("Organization name must be non-empty.")
    if base_currency is not None and base_currency not in {c.code for c in list_currencies()}:
        raise ValueError("Choose a valid ISO 4217 base currency.")
    org_id = uuid4()
    with tenant_transaction(cast(RlsSession, conn), Scope(org_id)):
        conn.execute(
            "INSERT INTO organization (id, name, base_currency) VALUES (%s, %s, %s)",
            (org_id, name.strip(), base_currency),
        )
    return org_id
