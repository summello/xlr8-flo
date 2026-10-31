"""Tax and freight apart on every PO line, one rounding rule, the budget amount (E13-S02)."""

from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from flo.kernel.errors import ErrorCode
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from flo.modules.purchasing.pricing import landed, spread
from flo.modules.purchasing.schemas import PoLinePatch, PoPatch
from tests.purchasing.test_purchase_orders import address, create, pos
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_award import scene
from tests.sourcing.test_cancel_supersede import approved_award
from tests.sourcing.test_rfq import refused, sql

# -- the arithmetic ----------------------------------------------------------------------


def test_document_freight_is_shared_pro_rata_and_the_cent_goes_to_the_largest_line():
    assert spread(D("100.00"), [D(10)] * 3, "USD") == [D("33.34"), D("33.33"), D("33.33")]
    assert spread(D("0.100"), [D(10)] * 3, "KWD") == [D("0.034"), D("0.033"), D("0.033")]
    # the largest line (index 1) takes the remainder, shares sum exactly, input order kept
    shares = spread(D("0.05"), [D(1), D(2), D(1)], "USD")
    assert shares == [D("0.01"), D("0.03"), D("0.01")] and sum(shares) == D("0.05")


def test_freight_takes_the_rounding_residual_so_the_total_is_the_awards_landed_line():
    # three separate quantizes: 1.00 + 1.00 + 1.00 = 3.00; the award quantizes the sum once: 3.01
    f = landed(D("1.004"), D(1), D("1.004"), D("1.004"), D(1), "USD")
    assert (f.price, f.tax, f.freight, f.line_total) == (D("1.00"), D("1.00"), D("1.01"), D("3.01"))
    half = landed(D(80), D(5), D(40), D(20), D(10), "USD")  # half the quantity, half the tax
    assert (half.price, half.tax, half.freight, half.line_total) == (
        D(400), D(20), D(10), D(430),
    )  # fmt: skip


def test_a_negative_freight_residual_is_absorbed_by_tax_never_below_zero():
    f = landed(D("0.0050"), D(1), D("0.0050"), D(0), D(1), "USD")
    assert (f.price, f.tax, f.freight, f.line_total) == (D("0.01"), D("0.00"), D("0.00"), D("0.01"))


def test_the_budget_amount_leaves_out_what_the_policy_excludes():
    f = landed(D(80), D(10), D(40), D(20), D(10), "USD")
    assert f.budget(tax_on=True, freight_on=True) == D(860)
    assert f.budget(tax_on=False, freight_on=True) == D(820)
    assert f.budget(tax_on=True, freight_on=False) == D(840)
    assert f.budget(tax_on=False, freight_on=False) == D(800)


def test_negative_or_non_decimal_amounts_are_refused_by_the_contract():
    for bad in ("-1", "1e3", "abc", "1.00001", ""):
        with pytest.raises(ValidationError):
            PoLinePatch(line_no=1, tax=bad)
        with pytest.raises(ValidationError):
            PoPatch(document_freight=bad)
    assert PoLinePatch(line_no=1, freight="0").freight == "0"


# -- on a draft --------------------------------------------------------------------------


def taxed(db, qty="10", awarded="10", price="80", **bid):
    """An approved award of `awarded` units: 80 each, tax 40 and freight 20 on the bid line."""
    bid = {"tax": "40", "freight": "20"} | bid
    rfq, row, _, [a, _b] = scene(db, qty=qty, prices=(price, "99"), **bid)
    award = approved_award(db, rfq, a, awarded)
    unit_id = sql(db, "SELECT unit_id FROM rfq WHERE id=%s", (rfq.id,))[0][0]
    address(db, unit_id)
    return rfq, a, award, unit_id


def patch(db, po, version, **fields):
    with correlation_context("po"):
        return pos(db).update(po.id, PoPatch(**fields), f'"{version}"')


def figures(po):
    [x] = po.lines
    return x.price, x.tax, x.freight, x.line_total


def test_a_whole_quantity_line_equals_the_awards_landed_line_and_half_is_half(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, _ = taxed(db)
    whole = create(db, rfq, a, award.id, "10")
    assert figures(whole) == ("800.00", "40.00", "20.00", "860.00")
    assert whole.lines[0].line_total == award.lines[0].amount_vendor == "860.00"
    assert (whole.price_total, whole.tax_total, whole.freight_total, whole.total) == (
        "800.00", "40.00", "20.00", "860.00",
    )  # fmt: skip
    assert whole.freight_mode == "line" and whole.document_freight is None
    patch(db, whole, 1, lines=[PoLinePatch(line_no=1, quantity="5")])
    assert figures(pos(db).get(whole.id)) == ("400.00", "20.00", "10.00", "430.00")


def test_the_rounding_edge_where_three_quantizes_differ_by_a_minor_unit(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, _ = taxed(db, "1", "1", "1.004", tax="1.004", freight="1.004")
    po = create(db, rfq, a, award.id, "1")
    assert figures(po) == ("1.00", "1.00", "1.01", "3.01")


def test_freight_mode_switches_keep_or_respread_and_explicit_values_win(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, _ = taxed(db)
    po = create(db, rfq, a, award.id, "10")
    # line -> document: document freight = sum of the line freights
    doc = patch(db, po, 1, freight_mode="document")
    assert (doc.freight_mode, doc.document_freight, doc.lines[0].freight) == (
        "document", "20.00", "20.00",
    )  # fmt: skip
    # explicit document freight wins over the sum (Decided 5), in the same PATCH as the switch
    line = patch(db, po, 2, freight_mode="line")
    assert (line.freight_mode, line.document_freight, line.lines[0].freight) == (
        "line", None, "20.00",
    )  # fmt: skip
    explicit = patch(db, po, 3, freight_mode="document", document_freight="7")
    assert (explicit.document_freight, explicit.lines[0].freight) == ("7.00", "7.00")
    assert explicit.lines[0].line_total == "847.00"
    # document -> line with an explicit line freight keeps that value
    back = patch(db, po, 4, freight_mode="line", lines=[PoLinePatch(line_no=1, freight="3")])
    assert (back.document_freight, back.lines[0].freight) == (None, "3.00")
    # the wrong mode for the field is a 422
    for fields in (
        {"document_freight": "1"},
        {"freight_mode": "document", "lines": [PoLinePatch(line_no=1, freight="1")]},
    ):
        error = refused(lambda f=fields: patch(db, po, 5, **f), ErrorCode.VALIDATION_FAILED)
        assert error.checks["problem"] == "freight_mode_mismatch"


def test_document_freight_over_three_lines_and_a_three_decimal_currency(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, _ = taxed(db)
    po = create(db, rfq, a, award.id, "10")
    sql(
        db,
        "INSERT INTO po_line SELECT org_id, po_id, n, award_id, rfq_line_id, vendor_id, "
        "requisition_id, requisition_line_id, description, uom_code, quantity, unit_price, "
        "project_id, department_code, ledger_account_code, price, tax, freight, line_total, "
        "budget_amount, budget_amount_project FROM po_line, generate_series(2,3) n RETURNING 1",
    )
    sql(db, "UPDATE po_line SET unit_price=10, quantity=1 RETURNING 1")
    done = patch(db, po, 1, freight_mode="document", document_freight="100")
    assert [x.freight for x in done.lines] == ["33.34", "33.33", "33.33"]
    assert done.freight_total == "100.00"
    sql(db, "UPDATE purchase_order SET currency='KWD' RETURNING 1")
    kwd = patch(db, po, 2, document_freight="0.1")
    assert [x.freight for x in kwd.lines] == ["0.034", "0.033", "0.033"]


def test_totals_are_the_sum_of_the_rounded_lines_not_a_document_rounding(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, _ = taxed(db)
    po = create(db, rfq, a, award.id, "10")
    sql(
        db,
        "INSERT INTO po_line SELECT org_id, po_id, n, award_id, rfq_line_id, vendor_id, "
        "requisition_id, requisition_line_id, description, uom_code, quantity, unit_price, "
        "project_id, department_code, ledger_account_code, price, tax, freight, line_total, "
        "budget_amount, budget_amount_project FROM po_line, generate_series(2,3) n RETURNING 1",
    )
    # three lines of 0.005 each: 0.01 each rounded, 0.03 in all; one document rounding gives 0.02
    sql(
        db,
        "UPDATE po_line SET unit_price='0.005', quantity=1, price='0.01', tax=0, freight=0, "
        "line_total='0.01' RETURNING 1",
    )
    read = pos(db).get(po.id)
    assert (read.price_total, read.total) == ("0.03", "0.03")


def test_a_quantity_edit_rescales_from_the_quote_and_drops_a_manual_edit(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, _ = taxed(db)
    po = create(db, rfq, a, award.id, "10")
    manual = patch(db, po, 1, lines=[PoLinePatch(line_no=1, tax="5", freight="1")])
    assert figures(manual) == ("800.00", "5.00", "1.00", "806.00")
    lowered = patch(db, po, 2, lines=[PoLinePatch(line_no=1, quantity="4")])
    assert figures(lowered) == ("320.00", "16.00", "8.00", "344.00")  # x 4 / 10, manual gone
    # explicit values in the same PATCH win over the rescale, field by field
    mixed = patch(db, po, 3, lines=[PoLinePatch(line_no=1, quantity="2", tax="9")])
    assert figures(mixed) == ("160.00", "9.00", "4.00", "173.00")
    both = patch(db, po, 4, lines=[PoLinePatch(line_no=1, quantity="6", freight="2")])
    assert figures(both) == ("480.00", "24.00", "2.00", "506.00")
    # in document mode the unchanged document freight re-spreads over the new prices
    patch(db, po, 5, freight_mode="document", document_freight="20")
    redone = patch(db, po, 6, lines=[PoLinePatch(line_no=1, quantity="3")])
    assert (redone.document_freight, redone.lines[0].freight) == ("20.00", "20.00")
    assert redone.lines[0].tax == "12.00"


def test_the_budget_amount_follows_the_units_settings_on_every_read(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, unit_id = taxed(db)
    po = create(db, rfq, a, award.id, "10")
    assert po.lines[0].budget_amount == po.budget_total == "860.00"
    assert po.lines[0].budget_amount_project == "860.00"
    org = OrgService(db[0], Scope(db[1]), db[3])
    org.set_setting("tax.consumes_budget", SettingPut(value=False, unit_id=unit_id))
    read = pos(db).get(po.id)  # no write: the version stands
    assert (read.version, read.lines[0].budget_amount, read.lines[0].tax) == (1, "820.00", "40.00")
    org.set_setting("freight.consumes_budget", SettingPut(value=False, unit_id=unit_id))
    read = pos(db).get(po.id)
    assert (read.lines[0].budget_amount, read.lines[0].freight, read.total) == (
        "800.00", "20.00", "860.00",
    )  # fmt: skip
    org.set_setting("tax.consumes_budget", SettingPut(value=True, unit_id=unit_id))
    # the policy applies on every pre-issue status; only issued orders keep the stored figure
    for status, expected in (("approval_pending", "840.00"), ("approved", "840.00")):
        sql(db, "UPDATE purchase_order SET status=%s RETURNING 1", (status,))
        org.set_setting("tax.consumes_budget", SettingPut(value=False, unit_id=unit_id))
        assert pos(db).get(po.id).lines[0].budget_amount == "800.00", status
        org.set_setting("tax.consumes_budget", SettingPut(value=True, unit_id=unit_id))
        assert pos(db).get(po.id).lines[0].budget_amount == expected, status
    sql(db, "UPDATE purchase_order SET status='draft' RETURNING 1")
    again = patch(db, po, 1, terms="x")  # a write stores what the read shows
    assert again.budget_total == "840.00"
    assert sql(db, "SELECT budget_amount FROM po_line") == [(D("840.0000"),)]


def test_a_one_cent_price_bid_keeps_its_freight_through_the_award_to_the_po(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, _ = taxed(db, price="0.01", tax="0", freight="0.10")
    po = create(db, rfq, a, award.id, "10")
    assert po.lines[0].freight == "0.10" and po.lines[0].line_total == "0.20"


def tiny(db):
    """A 10 000-unit award at 0.0001: the whole line is 1.00, forty units price to 0.00."""
    return taxed(db, "10000", "10000", "0.0001", tax="0", freight="0")


def test_po_creation_refuses_a_line_that_rounds_to_zero(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, _ = tiny(db)
    error = refused(lambda: create(db, rfq, a, award.id, "40"), ErrorCode.VALIDATION_FAILED)
    assert error.errors[0].field == "lines/1/quantity" and error.checks["problem"] == "zero_line"
    assert sql(db, "SELECT count(*) FROM purchase_order") == [(0,)]


def test_po_draft_quantity_edit_refuses_a_line_that_rounds_to_zero(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, _ = tiny(db)
    po = create(db, rfq, a, award.id, "10000")
    error = refused(
        lambda: patch(db, po, 1, lines=[PoLinePatch(line_no=1, quantity="40")]),
        ErrorCode.VALIDATION_FAILED,
    )
    assert error.errors[0].field == "lines/1/quantity" and error.checks["problem"] == "zero_line"
    assert sql(db, "SELECT quantity FROM po_line") == [(D("10000.0000"),)]


def test_a_zero_quantity_is_a_validation_error_on_both_paths():
    from flo.modules.purchasing.schemas import PoLineIn

    with pytest.raises(ValidationError):
        PoLineIn(rfq_line_id="00000000-0000-0000-0000-000000000001", quantity="0")
    with pytest.raises(ValidationError):
        PoLinePatch(line_no=1, quantity="0")
