"""The vendor copy of a purchase order: a whitelist of what the supplier may see (E13-S08).

Decision 170: no chartfield, no budget figure, no internal note, no file. Every leaf is built
field by field from the PO, so a column added to `PoRead` later cannot reach a supplier by
accident. A field added here later must have a default; none is ever removed, because stored
snapshots must keep validating.
"""

from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.collaboration.service import external_notes_for
from flo.modules.org.service import OrgService
from flo.modules.purchasing.service import PurchaseOrderService

type Conn = psycopg.Connection[tuple[object, ...]]


class _Copy(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VendorCopyAddress(_Copy):
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str | None = None


class VendorCopyLine(_Copy):
    line_no: int
    description: str
    quantity: str
    uom_code: str
    unit_price: str
    price: str
    tax: str
    freight: str
    line_total: str


class VendorCopyNote(_Copy):
    body: str
    created_on: date


class VendorCopy(_Copy):
    number: str
    revision: int
    issued_on: date | None
    needed_by: date | None
    currency: str
    vendor_trading_name: str
    vendor_legal_name: str
    bill_to: VendorCopyAddress
    ship_to: VendorCopyAddress | None
    terms: str | None
    letterhead_address: str | None
    standard_terms: str | None
    lines: list[VendorCopyLine]
    price_total: str
    tax_total: str
    freight_total: str
    total: str
    notes: list[VendorCopyNote] = []  # shareable notes of this revision and its ancestors (E13-S11)


def _address(source: dict[str, Any]) -> VendorCopyAddress:
    return VendorCopyAddress(**{k: source.get(k) for k in VendorCopyAddress.model_fields})


def _ship_to(conn: Conn, scope: Scope, unit_id: UUID, address_id: UUID) -> VendorCopyAddress:
    org = OrgService(conn, scope)
    for unit in org.unit_chain(unit_id):
        try:
            # no date filter: an address closed since the draft still resolves (DATA-005)
            found = org.get_address(unit, address_id)
        except ProblemError as error:
            if error.code != ErrorCode.NOT_FOUND:
                raise
            continue
        return _address(found.model_dump())
    raise ProblemError(ErrorCode.CONFLICT, checks={"problem": "ship_to_missing"})


def vendor_copy(conn: Conn, scope: Scope, po_id: UUID) -> VendorCopy:
    """Build the copy from live data in the caller's transaction; takes no lock."""
    service = PurchaseOrderService(conn, scope)
    po = service.get(po_id)
    issued = service.repo.execute(
        "SELECT issued_at FROM purchase_order WHERE org_id = %(org_id)s AND id = %(id)s",
        {"id": po_id},
    ).fetchone()
    issued_at = cast(datetime | None, None if issued is None else issued[0])
    org = OrgService(conn, scope)

    def setting(key: str) -> str | None:
        return cast(str | None, org.effective_setting(po.unit_id, key).value)

    chain = [
        cast(UUID, r[0])
        for r in service.repo.execute(
            "WITH RECURSIVE chain AS (SELECT id, supersedes_id FROM purchase_order "
            "WHERE org_id = %(org_id)s AND id = %(id)s UNION ALL SELECT p.id, p.supersedes_id "
            "FROM purchase_order p JOIN chain c "
            "ON p.org_id = %(org_id)s AND p.id = c.supersedes_id) SELECT id FROM chain",
            {"id": po_id},
        ).fetchall()
    ]
    return VendorCopy(
        number=po.number,
        revision=po.revision,
        issued_on=None if issued_at is None else issued_at.astimezone(UTC).date(),
        needed_by=po.needed_by,
        currency=po.currency,
        vendor_trading_name=po.vendor_snapshot["trading_name"],
        vendor_legal_name=po.vendor_snapshot["legal_name"],
        bill_to=_address(po.bill_to),
        ship_to=None
        if po.ship_to_address_id is None
        else _ship_to(conn, scope, po.unit_id, po.ship_to_address_id),
        terms=po.terms,
        letterhead_address=setting("po.letterhead_address"),
        standard_terms=setting("po.standard_terms"),
        lines=[
            VendorCopyLine(**{k: getattr(x, k) for k in VendorCopyLine.model_fields})
            for x in po.lines
        ],
        price_total=po.price_total,
        tax_total=po.tax_total,
        freight_total=po.freight_total,
        total=po.total,
        notes=[
            VendorCopyNote(body=n.body, created_on=n.created_at.date())
            for n in external_notes_for(conn, scope, "purchase_order", chain)
        ],
    )


def email_summary(copy: VendorCopy) -> str:
    """The email body, built only from the copy (E13-S09 sends it)."""
    revision = f" revision {copy.revision}" if copy.revision else ""
    needed = f"Needed by: {copy.needed_by.isoformat()}\n" if copy.needed_by else ""
    return (
        f"Purchase order {copy.number}{revision} for {copy.vendor_trading_name}\n"
        f"{needed}Lines: {len(copy.lines)}\n"
        f"Total: {copy.total} {copy.currency}\n\n"
        "The purchase order is attached as a PDF."
    )
