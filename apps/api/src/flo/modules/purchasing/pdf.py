"""Vendor-copy PDF: frozen at issue, reproduced from the stored snapshot (PO-014, DATA-005).

`render` takes no connection, so the PDF can only contain what `VendorCopy` holds (PO-017).
`po-v1` is frozen once merged: a layout change adds `po-v2` beside it and never edits v1.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time
from hashlib import sha256
from importlib.resources import files
from typing import cast
from uuid import UUID

import psycopg
from fpdf import FPDF, FontFace
from fpdf.enums import Align, XPos, YPos
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.purchasing.service import PurchaseOrderService
from flo.modules.purchasing.vendor_copy import (
    VendorCopy,
    VendorCopyAddress,
    email_summary,
    vendor_copy,
)

type Conn = psycopg.Connection[tuple[object, ...]]

logger = logging.getLogger(__name__)

TEMPLATE_VERSION = "po-v1"
_FONT = "NotoSans"


def _address_text(a: VendorCopyAddress) -> str:
    place = " ".join(p for p in (a.postal_code, a.city) if p)
    return "\n".join(p for p in (a.line1, a.line2, place, a.region, a.country) if p)


def _render_v1(copy: VendorCopy) -> bytes:
    assert copy.issued_on is not None
    pdf = FPDF(unit="mm", format="A4")
    pdf.set_creation_date(datetime.combine(copy.issued_on, time(), UTC))
    pdf.set_compression(False)
    pdf.set_producer(f"XLR8 FLO {TEMPLATE_VERSION}")
    pdf.add_font(
        _FONT, "", str(files("flo.modules.purchasing").joinpath("fonts/NotoSans-Regular.ttf"))
    )
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()

    def text(size: int, body: str, *, align: Align = Align.L) -> None:
        pdf.set_font(_FONT, "", size)
        pdf.multi_cell(
            0, size * 0.5, body, align=align, markdown=False, new_x=XPos.LMARGIN, new_y=YPos.NEXT
        )

    if copy.letterhead_address:
        text(10, copy.letterhead_address)
        pdf.ln(4)
    text(18, f"Purchase Order {copy.number}")
    revision = f"Revision {copy.revision}\n" if copy.revision else ""
    issued = copy.issued_on.isoformat()
    needed = f"\nNeeded by: {copy.needed_by.isoformat()}" if copy.needed_by else ""
    text(10, f"{revision}Issued: {issued}{needed}\nCurrency: {copy.currency}")
    pdf.ln(3)
    text(11, "Vendor")
    text(10, f"{copy.vendor_trading_name}\n{copy.vendor_legal_name}")
    pdf.ln(3)
    text(11, "Bill To")
    text(10, _address_text(copy.bill_to))
    if copy.ship_to is not None:
        pdf.ln(3)
        text(11, "Ship To")
        text(10, _address_text(copy.ship_to))
    pdf.ln(4)

    pdf.set_font(_FONT, "", 8)
    with pdf.table(
        col_widths=(8, 52, 16, 22, 22, 18, 18, 24),
        text_align=("RIGHT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"),
        line_height=4.5,
        markdown=False,
        headings_style=FontFace(emphasis=None),  # one regular weight is bundled
    ) as table:
        head = table.row()
        for h in ("#", "Description", "Qty", "Unit price", "Price", "Tax", "Freight", "Total"):
            head.cell(h)
        for x in copy.lines:
            row = table.row()
            for v in (
                str(x.line_no),
                x.description,
                f"{x.quantity} {x.uom_code}",
                x.unit_price,
                x.price,
                x.tax,
                x.freight,
                x.line_total,
            ):
                row.cell(v)
    pdf.ln(4)
    text(
        10,
        f"Price: {copy.price_total} {copy.currency}\nTax: {copy.tax_total} {copy.currency}\n"
        f"Freight: {copy.freight_total} {copy.currency}\nTotal: {copy.total} {copy.currency}",
        align=Align.R,
    )
    if copy.terms:
        pdf.ln(4)
        text(11, "Terms")
        text(9, copy.terms)
    if copy.notes:
        pdf.ln(4)
        text(11, "Notes")
        for note in copy.notes:
            text(9, f"{note.created_on.isoformat()}: {note.body}")
    if copy.standard_terms:
        pdf.ln(4)
        text(11, "Standard terms")
        text(8, copy.standard_terms)
    return bytes(pdf.output())


_TEMPLATES = {"po-v1": _render_v1}


def render(copy: VendorCopy, template_version: str) -> bytes:
    if template_version not in _TEMPLATES or copy.issued_on is None:
        raise ValueError("Unknown template version, or the purchase order is not issued.")
    return _TEMPLATES[template_version](copy)


def freeze(conn: Conn, scope: Scope, po_id: UUID) -> None:
    """Store the issued row's snapshot and PDF hash, once, in the caller's transaction."""
    copy = vendor_copy(conn, scope, po_id)
    snapshot = copy.model_dump(mode="json")
    data = render(VendorCopy.model_validate(snapshot), TEMPLATE_VERSION)  # the path pdf_for takes
    service = PurchaseOrderService(conn, scope)
    version = cast(int, service._row(po_id)["version"])
    # ponytail: renders inside the issue transaction with its locks held; move the render out
    # if issue latency shows it.
    service.repo.execute(
        "INSERT INTO po_version (org_id, po_id, version, template_version, snapshot, pdf_sha256) "
        "VALUES (%(org_id)s, %(id)s, %(version)s, %(template)s, %(snapshot)s, %(sha)s)",
        {
            "id": po_id,
            "version": version,
            "template": TEMPLATE_VERSION,
            "snapshot": Jsonb(snapshot),
            "sha": sha256(data).hexdigest(),
        },
    )


@dataclass(frozen=True)
class PoVersion:
    version: int
    template_version: str
    pdf_sha256: str
    copy: VendorCopy


def frozen(conn: Conn, scope: Scope, po_id: UUID) -> PoVersion:
    service = PurchaseOrderService(conn, scope)
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = service.repo.execute(
            "SELECT version, template_version, pdf_sha256, snapshot FROM po_version "
            "WHERE org_id = %(org_id)s AND po_id = %(id)s",
            {"id": po_id},
        ).fetchone()
    if row is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return PoVersion(
        cast(int, row[0]),
        cast(str, row[1]),
        cast(str, row[2]),
        VendorCopy.model_validate(row[3]),
    )


def pdf_for(conn: Conn, scope: Scope, po_id: UUID) -> bytes:
    """The issued PDF, re-rendered from the snapshot; never bytes that differ from issue."""
    pv = frozen(conn, scope, po_id)
    data = render(pv.copy, pv.template_version)
    if sha256(data).hexdigest() != pv.pdf_sha256:
        logger.error("po_pdf_hash_mismatch po_id=%s", po_id)
        raise ProblemError(ErrorCode.INTERNAL_ERROR)
    return data


def filename(copy: VendorCopy) -> str:
    """The download name (E13-S09 attaches the same name)."""
    suffix = f"-rev{copy.revision}" if copy.revision else ""
    return f"{copy.number}{suffix}.pdf"


class VendorCopyRead(BaseModel):
    vendor_copy: VendorCopy
    email_summary: str
    template_version: str
    issued: bool


def vendor_copy_read(conn: Conn, scope: Scope, po_id: UUID) -> VendorCopyRead:
    try:
        pv = frozen(conn, scope, po_id)
    except ProblemError as error:
        if error.code != ErrorCode.NOT_FOUND:
            raise
    else:
        return VendorCopyRead(
            vendor_copy=pv.copy,
            email_summary=email_summary(pv.copy),
            template_version=pv.template_version,
            issued=True,
        )
    status = PurchaseOrderService(conn, scope)._row(po_id)["status"]
    if status not in ("draft", "approval_pending", "approved"):
        raise ProblemError(ErrorCode.NOT_FOUND)
    copy = vendor_copy(conn, scope, po_id)
    return VendorCopyRead(
        vendor_copy=copy,
        email_summary=email_summary(copy),
        template_version=TEMPLATE_VERSION,
        issued=False,
    )
