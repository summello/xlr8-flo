"""Catalogue items are derived from events and never written by an HTTP route."""

from typing import cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.service import OrgService
from flo.modules.requisitions.models import RequisitionRepository
from flo.modules.requisitions.schemas import CatalogueItemRead, CatalogueRows, invalid

Connection = psycopg.Connection[tuple[object, ...]]
# The packet's normalisation, kept in SQL so storage and search agree byte for byte.
NORMALIZE = "lower(regexp_replace(trim({}), '\\s+', ' ', 'g'))"
ITEM_FIELDS = (
    "id",
    "line_type",
    "description",
    "uom_code",
    "category_code",
    "last_unit_price::text",
    "currency",
    "last_event",
    "last_event_at",
    "last_vendor_id",
    "bu_id",
)
EVENTS = ("requested", "sourced", "purchased", "supplied", "serviced")


def record_event(
    conn: Connection,
    scope: Scope,
    *,
    bu_id: UUID,
    line_type: str,
    description: str,
    uom_code: str,
    category_code: str | None,
    event: str,
    source_type: str,
    source_id: UUID,
    quantity: str,
    unit_price: str,
    currency: str,
    vendor_id: UUID | None = None,
    catalogue_item_id: UUID | None = None,
) -> UUID:
    """Upsert the item and append one event; a repeated source records nothing new."""
    if event not in EVENTS:
        raise ValueError(f"Unknown catalogue event {event}")
    repo = RequisitionRepository(conn, scope)
    with tenant_transaction(cast(RlsSession, conn), scope):
        item_id = catalogue_item_id
        if item_id is None:
            key = {
                "bu_id": bu_id,
                "line_type": line_type,
                "description": description,
                "uom_code": uom_code,
            }
            lookup = (
                "SELECT id FROM catalogue_item WHERE org_id=%(org_id)s AND bu_id=%(bu_id)s "
                "AND line_type=%(line_type)s AND normalized="
                + NORMALIZE.format("%(description)s")
                + " AND uom_code=%(uom_code)s"
            )
            row = repo.execute(lookup, key).fetchone()
            if row is None:
                repo.execute(
                    "INSERT INTO catalogue_item (id,org_id,bu_id,line_type,description,normalized,"
                    "uom_code,category_code,last_unit_price,currency,last_event,last_event_at,"
                    "last_vendor_id) VALUES (%(id)s,%(org_id)s,%(bu_id)s,%(line_type)s,"
                    "%(description)s," + NORMALIZE.format("%(description)s") + ",%(uom_code)s,"
                    "%(category_code)s,%(unit_price)s,%(currency)s,%(event)s,now(),%(vendor_id)s) "
                    "ON CONFLICT ON CONSTRAINT catalogue_item_key DO NOTHING",
                    key
                    | {
                        "id": uuid4(),
                        "category_code": category_code,
                        "unit_price": unit_price,
                        "currency": currency,
                        "event": event,
                        "vendor_id": vendor_id,
                    },
                )
                row = repo.execute(lookup, key).fetchone()
            assert row is not None
            item_id = cast(UUID, row[0])
        inserted = repo.execute(
            "INSERT INTO catalogue_event (org_id,catalogue_item_id,event,source_type,source_id,"
            "vendor_id,quantity,unit_price,currency) VALUES (%(org_id)s,%(item)s,%(event)s,"
            "%(source_type)s,%(source_id)s,%(vendor_id)s,%(quantity)s,%(unit_price)s,%(currency)s)"
            " ON CONFLICT ON CONSTRAINT catalogue_event_once DO NOTHING RETURNING id",
            {
                "item": item_id,
                "event": event,
                "source_type": source_type,
                "source_id": source_id,
                "vendor_id": vendor_id,
                "quantity": quantity,
                "unit_price": unit_price,
                "currency": currency,
            },
        ).fetchone()
        if inserted is not None:
            repo.execute(
                "UPDATE catalogue_item SET last_unit_price=%(unit_price)s,currency=%(currency)s,"
                "last_event=%(event)s,last_event_at=now(),"
                "last_vendor_id=COALESCE(%(vendor_id)s,last_vendor_id),"
                "times_requested=times_requested+%(requested)s "
                "WHERE org_id=%(org_id)s AND id=%(item)s",
                {
                    "item": item_id,
                    "unit_price": unit_price,
                    "currency": currency,
                    "event": event,
                    "vendor_id": vendor_id,
                    "requested": 1 if event == "requested" else 0,
                },
            )
        return item_id


def record_requested(conn: Connection, scope: Scope, requisition_id: UUID) -> int:
    """One `requested` event per line; E09-S05 calls this inside the submit transaction."""
    repo = RequisitionRepository(conn, scope)
    with tenant_transaction(cast(RlsSession, conn), scope):
        header = repo.execute(
            "SELECT bu_id FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s",
            {"id": requisition_id},
        ).fetchone()
        if header is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        lines = repo.execute(
            "SELECT id,line_type,description,uom_code,category_code,quantity::text,"
            "unit_price::text,currency,catalogue_item_id FROM requisition_line "
            "WHERE org_id=%(org_id)s AND requisition_id=%(id)s ORDER BY line_no",
            {"id": requisition_id},
        ).fetchall()
        for line in lines:
            record_event(
                conn,
                scope,
                bu_id=cast(UUID, header[0]),
                line_type=cast(str, line[1]),
                description=cast(str, line[2]),
                uom_code=cast(str, line[3]),
                category_code=cast(str | None, line[4]),
                event="requested",
                source_type="requisition_line",
                source_id=cast(UUID, line[0]),
                quantity=cast(str, line[5]),
                unit_price=cast(str, line[6]),
                currency=cast(str, line[7]),
                catalogue_item_id=cast(UUID | None, line[8]),
            )
        return len(lines)


class CatalogueService:
    def __init__(self, connection: Connection, scope: Scope) -> None:
        self.connection, self.scope = connection, scope
        self.repo = RequisitionRepository(connection, scope)

    def visible_units(self, bu_id: UUID) -> list[UUID]:
        """The unit and its ancestors: never a sibling or a descendant."""
        return OrgService(self.connection, self.scope).unit_chain(bu_id)

    def item(self, item_id: UUID, bu_id: UUID) -> CatalogueItemRead | None:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                "SELECT " + ",".join(ITEM_FIELDS) + " FROM catalogue_item "
                "WHERE org_id=%(org_id)s AND id=%(id)s AND bu_id=ANY(%(units)s)",
                {"id": item_id, "units": self.visible_units(bu_id)},
            ).fetchone()
            return self._read(row) if row else None

    def search(self, bu_id: UUID, q: str, line_type: str | None, limit: int) -> CatalogueRows:
        if not 2 <= len(" ".join(q.split())) <= 100:
            raise invalid("q", "Search with 2 to 100 characters.")
        if not 1 <= limit <= 20:
            raise invalid("limit", "Choose a limit from 1 to 20.")
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rows = self.repo.execute(
                "SELECT " + ",".join(ITEM_FIELDS) + " FROM catalogue_item "
                "WHERE org_id=%(org_id)s AND bu_id=ANY(%(units)s) "
                "AND (%(line_type)s::text IS NULL OR line_type=%(line_type)s) "
                "AND normalized LIKE '%%' || "
                + NORMALIZE.format("%(q)s")
                + " || '%%' ORDER BY last_event_at DESC,id LIMIT %(limit)s",
                {
                    "units": self.visible_units(bu_id),
                    "line_type": line_type,
                    "q": escaped,
                    "limit": limit,
                },
            ).fetchall()
            return CatalogueRows(rows=[self._read(row) for row in rows])

    @staticmethod
    def _read(row: tuple[object, ...]) -> CatalogueItemRead:
        names = (
            "id",
            "line_type",
            "description",
            "uom_code",
            "category_code",
            "last_unit_price",
            "currency",
            "last_event",
            "last_event_at",
            "last_vendor_id",
            "bu_id",
        )
        return CatalogueItemRead.model_validate(
            dict(zip(names, row, strict=True)) | {"last_vendor_name": None}
        )
