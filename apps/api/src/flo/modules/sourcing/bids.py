"""Bids keyed by the buyer from the vendor's own paper, in the vendor's currency (SRC-006).

Nothing here converts: a bid keeps the currency it was sent in (SRC-010; E12-S06 compares).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError, conflict
from flo.kernel.money import currency_exponent, quantize
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.sourcing.evaluation import assert_may_see_bids
from flo.modules.sourcing.schemas import (
    BidCreate,
    BidLate,
    BidLineIn,
    BidLineRead,
    BidPatch,
    BidRead,
    BidReplace,
    BidRequirementRead,
    BidSubmit,
    BidTotals,
    SealedBidRead,
    invalid,
)
from flo.modules.sourcing.sealing import visible
from flo.modules.sourcing.service import RfqService

COLUMNS = (
    "id, rfq_id, vendor_id, rfq_version, currency, sent_at, evidence_attachment_id, keyed_by, "
    "keyed_at, freight, lead_time_days, valid_until, delivery_terms, warranty, exceptions, "
    "status, submitted_at, replaces_bid_id, row_version, late_accepted_by, late_accepted_at, "
    "late_reason"
)
PLAIN = ("lead_time_days", "valid_until", "delivery_terms", "warranty", "exceptions")


def validate_currency(currency: str) -> None:
    try:
        currency_exponent(currency)
    except ValueError:
        raise invalid("currency", "Unknown currency code.", "unknown_currency") from None


def _text(amount: Any) -> str | None:
    return None if amount is None else format(amount, "f")


class BidService(RfqService):
    # -- helpers ---------------------------------------------------------------------------

    def _lock_rfq(self, id: UUID) -> tuple[object, ...]:
        """Lock the RFQ first, always: status, whether the bidding stop time is ahead, the stop
        time itself and the database clock (the sealing and lateness checks use these two)."""
        row = self.repo.execute(
            "SELECT status, now() < deadline_at, deadline_at, now() FROM rfq "
            "WHERE org_id = %(org_id)s AND id = %(id)s FOR UPDATE",
            {"id": id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return row

    @staticmethod
    def _unsealed(rfq_row: tuple[object, ...]) -> bool:
        return visible(cast("datetime | None", rfq_row[2]), cast(datetime, rfq_row[3]))

    def _lock_bid(self, rfq_id: UUID, bid_id: UUID) -> tuple[object, ...]:
        row = self.repo.execute(
            f"SELECT {COLUMNS} FROM bid "
            "WHERE org_id = %(org_id)s AND id = %(bid)s AND rfq_id = %(rfq)s FOR UPDATE",
            {"bid": bid_id, "rfq": rfq_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)  # another RFQ's bid is just an unknown id
        return row

    def _own_draft(
        self,
        row: tuple[object, ...],
        late_lead: tuple[object, ...] | None = None,
    ) -> None:
        """Only the keyer changes a draft. `late_lead` (the locked RFQ row) lets a lead submit
        another user's draft, and only when that draft is late (SRC-008)."""
        if row[7] != self.actor_id and late_lead is None:
            raise ProblemError(ErrorCode.FORBIDDEN)
        if row[15] != "draft":
            raise conflict("not_draft", "A submitted bid cannot change. Key a replacement.")
        if (
            late_lead is not None
            and row[7] != self.actor_id
            and cast(datetime, row[5]) <= cast(datetime, late_lead[2])
        ):
            raise ProblemError(ErrorCode.FORBIDDEN)  # a lead accepts late bids, not on-time ones

    @staticmethod
    def _fail(errors: list[ProblemFieldError], problem: str = "invalid_bid") -> ProblemError:
        return ProblemError(
            ErrorCode.VALIDATION_FAILED,
            detail=errors[0].message,
            errors=tuple(errors),
            checks={"problem": problem},
        )

    def _assert_not_awarded(self, rfq_row: tuple[object, ...]) -> None:
        # E12-S08 adds its award-record check here.
        if rfq_row[0] == "awarded":
            raise conflict("rfq_awarded", "This RFQ is awarded. Its bids can no longer change.")

    def _before_submit(
        self,
        rfq_row: tuple[object, ...],
        bid_row: tuple[object, ...],
        body: BidSubmit,
        may_accept_late: bool,
    ) -> dict[str, object]:
        """The extra `SET` values of the submit UPDATE: none when on time (SRC-008).

        Lateness is the vendor's `sent_at` against the RFQ's deadline, never `keyed_at`
        (decision 145). A late bid needs a reason (409 without) and `rfq.approve` (403 without).
        """
        if cast(datetime, bid_row[5]) <= cast(datetime, rfq_row[2]):
            return {}
        if body.accept_late_reason is None:
            raise conflict(
                "late_bid", "The vendor sent this bid after the bidding stop time. Enter a reason."
            )
        if not may_accept_late:
            raise ProblemError(ErrorCode.FORBIDDEN)
        return {
            "late_accepted_by": self.actor_id,
            "late_accepted_at": rfq_row[3],
            "late_reason": body.accept_late_reason,
        }

    def _quantities(self, rfq_id: UUID, version: int) -> dict[UUID, Decimal]:
        """Each RFQ line's quantity as the answered version sent it, not as amended since."""
        return {
            cast(UUID, r[0]): cast(Decimal, r[1])
            for r in self.repo.execute(
                "SELECT l.id, (v.snapshot->'lines'->(l.requisition_line_id::text)->>'quantity')"
                "::numeric FROM rfq_line l JOIN rfq_version v ON v.rfq_id = l.rfq_id "
                "AND v.version = %(version)s WHERE l.org_id = %(org_id)s AND l.rfq_id = %(rfq)s",
                {"rfq": rfq_id, "version": version},
            ).fetchall()
        }

    def _write_lines(
        self, bid_id: UUID, rfq_id: UUID, version: int, currency: str, lines: list[BidLineIn]
    ) -> None:
        quantities = self._quantities(rfq_id, version)
        errors = []
        for line in lines:
            if line.quoted and line.tax is None:
                errors.append(
                    ProblemFieldError(
                        field=f"lines/{line.rfq_line_id}",
                        message="Enter the tax on this line, or 0 if none.",
                    )
                )
            elif not line.quoted and line.tax is not None:
                errors.append(
                    ProblemFieldError(
                        field=f"lines/{line.rfq_line_id}",
                        message="A line not quoted has no tax.",
                    )
                )
            if line.rfq_line_id not in quantities:
                errors.append(
                    ProblemFieldError(
                        field=f"lines/{line.rfq_line_id}", message="This is not a line of the RFQ."
                    )
                )
            elif line.quoted != (line.unit_price is not None):
                errors.append(
                    ProblemFieldError(
                        field=f"lines/{line.rfq_line_id}",
                        message="A quoted line needs a unit price; a line not quoted has none.",
                    )
                )
        if errors:
            raise self._fail(errors)
        self.repo.execute(
            "DELETE FROM bid_line WHERE org_id = %(org_id)s AND bid_id = %(bid)s", {"bid": bid_id}
        )
        for line in lines:
            quantity = quantities[line.rfq_line_id]
            price = None if line.unit_price is None else Decimal(line.unit_price)
            self.repo.execute(
                "INSERT INTO bid_line (org_id, bid_id, rfq_line_id, quantity, quoted, unit_price, "
                "tax, line_total) VALUES (%(org_id)s, %(bid)s, %(line)s, %(quantity)s, %(quoted)s, "
                "%(price)s, %(tax)s, %(total)s)",
                {
                    "bid": bid_id,
                    "line": line.rfq_line_id,
                    "quantity": quantity,
                    "quoted": line.quoted,
                    "price": price,
                    "tax": Decimal(line.tax or "0"),
                    "total": None if price is None else quantize(price * quantity, currency),
                },
            )

    def _evidence_ok(self, bid_id: UUID, attachment_id: UUID) -> bool:
        return (
            self.repo.execute(
                "SELECT 1 FROM attachment WHERE org_id = %(org_id)s AND id = %(id)s "
                "AND document_type = 'bid' AND document_id = %(bid)s AND deleted_at IS NULL",
                {"id": attachment_id, "bid": bid_id},
            ).fetchone()
            is not None
        )

    def _late(self, row: tuple[object, ...]) -> BidLate | None:
        if row[19] is None:
            return None
        person = self.repo.execute(
            "SELECT display_name, email FROM identity WHERE id = %(id)s", {"id": row[19]}
        ).fetchone()
        return BidLate(
            accepted_by_name=cast(str, person[0]) if person else None,
            accepted_by_email=cast(str, person[1]) if person else None,
            at=cast(datetime, row[20]),
            reason=cast(str, row[21]),
        )

    def _replaced_by(self, bid_id: UUID) -> UUID | None:
        replaced = self.repo.execute(
            "SELECT id FROM bid WHERE org_id = %(org_id)s AND replaces_bid_id = %(bid)s "
            "AND status = 'submitted'",
            {"bid": bid_id},
        ).fetchone()
        return cast(UUID, replaced[0]) if replaced else None

    def _sealed(self, row: tuple[object, ...]) -> SealedBidRead:
        keep = (
            "id, rfq_id, vendor_id, keyed_by, status, sent_at, keyed_at, submitted_at, "
            "replaces_bid_id, row_version"
        ).split(", ")
        values = dict(zip(COLUMNS.split(", "), row, strict=True))
        return SealedBidRead(
            **{k: values[k] for k in keep},  # type: ignore[arg-type]
            replaced_by_bid_id=self._replaced_by(cast(UUID, row[0])),
            late=self._late(row),
        )

    def _present(self, deadline_passed: bool, row: tuple[object, ...]) -> BidRead | SealedBidRead:
        """The one door every bid leaves through. A draft is its keyer's alone, before and after
        the deadline; anything else is sealed until the deadline (SRC-007)."""
        if row[15] == "draft":
            open_to_caller = row[7] == self.actor_id
        else:
            open_to_caller = deadline_passed
        return self._read(row) if open_to_caller else self._sealed(row)

    def _read(self, row: tuple[object, ...]) -> BidRead:
        """The full shape. Private: only `_present`, and E12-S06/S07 after their own `visible`."""
        bid_id = cast(UUID, row[0])
        lines: list[tuple[Any, ...]] = self.repo.execute(
            "SELECT l.rfq_line_id, l.quantity, l.quoted, l.unit_price, l.tax, l.line_total "
            "FROM bid_line l JOIN rfq_line r ON r.id = l.rfq_line_id "
            "WHERE l.org_id = %(org_id)s AND l.bid_id = %(bid)s ORDER BY r.line_no",
            {"bid": bid_id},
        ).fetchall()
        answers: list[tuple[Any, ...]] = self.repo.execute(
            "SELECT b.requirement_id, b.met, b.note FROM bid_requirement b "
            "JOIN rfq_requirement r ON r.id = b.requirement_id "
            "WHERE b.org_id = %(org_id)s AND b.bid_id = %(bid)s ORDER BY r.position",
            {"bid": bid_id},
        ).fetchall()
        replaced = self._replaced_by(bid_id)
        fields = COLUMNS.split(", ")
        values = dict(zip(fields, row, strict=True))
        for late in ("late_accepted_by", "late_accepted_at", "late_reason"):
            del values[late]
        currency = cast(str, values["currency"])
        line_sum = sum((r[5] for r in lines if r[2]), Decimal(0))
        tax_sum = sum((r[4] for r in lines if r[2]), Decimal(0))
        freight = quantize(cast(Decimal, row[9]), currency)
        lines_total, tax_total = quantize(line_sum, currency), quantize(tax_sum, currency)
        values["freight"] = _text(cast(Decimal, values["freight"]))
        return BidRead(
            **values,  # type: ignore[arg-type]
            totals=BidTotals(
                lines=format(lines_total, "f"),
                tax=format(tax_total, "f"),
                freight=format(freight, "f"),
                total=format(lines_total + tax_total + freight, "f"),
            ),
            lines=[
                BidLineRead(
                    rfq_line_id=r[0],
                    quantity=_text(r[1]) or "",
                    quoted=r[2],
                    unit_price=_text(r[3]),
                    tax=_text(r[4]) or "",
                    line_total=_text(r[5]),
                )
                for r in lines
            ],
            requirements=[
                BidRequirementRead(requirement_id=r[0], met=r[1], note=r[2]) for r in answers
            ],
            replaced_by_bid_id=replaced,
            current=row[15] == "submitted" and replaced is None,
            late=self._late(row),
        )

    def _bid_audit(self, action: str, rfq_id: UUID, after: dict[str, object]) -> None:
        self._audit(action, rfq_id, None, after)

    # -- reads -----------------------------------------------------------------------------

    def _deadline_passed(self, rfq_id: UUID) -> bool:
        row = self.repo.execute(
            "SELECT deadline_at, now() FROM rfq WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": rfq_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return visible(cast("datetime | None", row[0]), cast(datetime, row[1]))

    def list_bids(self, rfq_id: UUID, *, all_bids: bool) -> list[BidRead | SealedBidRead]:
        """`all_bids` (rfq.approve) sees every bid; otherwise only those the caller keyed."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            passed = self._deadline_passed(rfq_id)
            if passed:
                assert_may_see_bids(self.repo, rfq_id, self.actor_id)
            rows = self.repo.execute(
                f"SELECT {COLUMNS} FROM bid WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s"
                + ("" if all_bids else " AND keyed_by = %(actor)s")
                + " ORDER BY keyed_at, id",
                {"rfq": rfq_id, "actor": self.actor_id},
            ).fetchall()
            return [self._present(passed, r) for r in rows]

    def read(self, rfq_id: UUID, bid_id: UUID, *, all_bids: bool) -> BidRead | SealedBidRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            passed = self._deadline_passed(rfq_id)
            row = self.repo.execute(
                f"SELECT {COLUMNS} FROM bid "
                "WHERE org_id = %(org_id)s AND id = %(bid)s AND rfq_id = %(rfq)s",
                {"bid": bid_id, "rfq": rfq_id},
            ).fetchone()
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            if not all_bids and row[7] != self.actor_id:
                raise ProblemError(ErrorCode.FORBIDDEN)
            if passed:
                assert_may_see_bids(self.repo, rfq_id, self.actor_id)
            return self._present(passed, row)

    # -- writes ----------------------------------------------------------------------------

    def capture(self, rfq_id: UUID, body: BidCreate) -> BidRead | SealedBidRead:
        validate_currency(body.currency)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rfq = self._lock_rfq(rfq_id)
            if rfq[0] not in ("published", "closed"):
                raise conflict("not_open", "Bids are keyed once the RFQ is published.")
            if not self.repo.execute(
                "SELECT 1 FROM rfq_invitee WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                "AND vendor_id = %(vendor)s",
                {"rfq": rfq_id, "vendor": body.vendor_id},
            ).fetchone():
                raise invalid("vendor_id", "Only an invited vendor can bid.", "invalid_bid")
            if not self.repo.execute(
                "SELECT 1 FROM rfq_version WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                "AND version = %(version)s",
                {"rfq": rfq_id, "version": body.rfq_version},
            ).fetchone():
                raise invalid("rfq_version", "The RFQ has no such version.", "invalid_bid")
            original = self.repo.execute(
                "SELECT id FROM bid WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                "AND vendor_id = %(vendor)s AND status = 'submitted' AND replaces_bid_id IS NULL",
                {"rfq": rfq_id, "vendor": body.vendor_id},
            ).fetchone()
            if original:
                raise conflict(
                    "bid_exists",
                    f"This vendor's bid is already captured (bid {original[0]}). "
                    "Replace it to key a correction.",
                )
            if self.repo.execute(
                "SELECT 1 FROM bid WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                "AND vendor_id = %(vendor)s AND status = 'draft'",
                {"rfq": rfq_id, "vendor": body.vendor_id},
            ).fetchone():
                raise conflict("draft_exists", "This vendor already has a draft bid. Continue it.")
            bid_id = uuid4()
            self.repo.execute(
                "INSERT INTO bid (id, org_id, rfq_id, vendor_id, rfq_version, currency, sent_at, "
                "keyed_by) VALUES (%(id)s, %(org_id)s, %(rfq)s, %(vendor)s, %(version)s, "
                "%(currency)s, %(sent)s, %(actor)s)",
                {
                    "id": bid_id,
                    "rfq": rfq_id,
                    "vendor": body.vendor_id,
                    "version": body.rfq_version,
                    "currency": body.currency,
                    "sent": body.sent_at,
                    "actor": self.actor_id,
                },
            )
            self._bid_audit(
                "bid.create", rfq_id, {"bid_id": str(bid_id), "vendor_id": str(body.vendor_id)}
            )
            return self._present(self._unsealed(rfq), self._lock_bid(rfq_id, bid_id))

    def patch(
        self, rfq_id: UUID, bid_id: UUID, body: BidPatch, if_match: str | None
    ) -> BidRead | SealedBidRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rfq = self._lock_rfq(rfq_id)
            row = self._lock_bid(rfq_id, bid_id)
            self._own_draft(row)
            self._check_edit_revision(cast(int, row[18]), if_match)
            sent = body.model_fields_set
            version = body.rfq_version or cast(int, row[3])
            currency = body.currency or cast(str, row[4])
            if body.currency:
                validate_currency(currency)
            if (
                body.rfq_version
                and not self.repo.execute(
                    "SELECT 1 FROM rfq_version WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                    "AND version = %(version)s",
                    {"rfq": rfq_id, "version": version},
                ).fetchone()
            ):
                raise invalid("rfq_version", "The RFQ has no such version.", "invalid_bid")
            if body.evidence_attachment_id and not self._evidence_ok(
                bid_id, body.evidence_attachment_id
            ):
                raise invalid(
                    "evidence_attachment_id",
                    "Upload the vendor's email or file to this bid first, then choose it.",
                    "invalid_bid",
                )
            sets = {
                k: getattr(body, k)
                for k in (*PLAIN, "sent_at", "currency", "rfq_version", "evidence_attachment_id")
                if k in sent
            }
            if "freight" in sent:
                sets["freight"] = Decimal(cast(str, body.freight))
            # A new version or currency changes every line's quantity or rounding: redo them all.
            if body.lines is not None:
                self._write_lines(bid_id, rfq_id, version, currency, body.lines)
            elif body.rfq_version or body.currency:
                existing: list[tuple[Any, ...]] = self.repo.execute(
                    "SELECT rfq_line_id, quoted, unit_price, tax FROM bid_line "
                    "WHERE org_id = %(org_id)s AND bid_id = %(bid)s",
                    {"bid": bid_id},
                ).fetchall()
                self._write_lines(
                    bid_id,
                    rfq_id,
                    version,
                    currency,
                    [
                        BidLineIn(
                            rfq_line_id=r[0],
                            quoted=r[1],
                            unit_price=_text(r[2]),
                            tax=_text(r[3]) if r[1] else None,
                        )
                        for r in existing
                    ],
                )
            if body.requirements is not None:
                self._write_answers(bid_id, rfq_id, body)
            self.repo.execute(
                "UPDATE bid SET "
                + "".join(f"{k} = %({k})s, " for k in sets)
                + "row_version = row_version + 1 WHERE org_id = %(org_id)s AND id = %(bid)s",
                {**sets, "bid": bid_id},
            )
            return self._present(self._unsealed(rfq), self._lock_bid(rfq_id, bid_id))

    def _write_answers(self, bid_id: UUID, rfq_id: UUID, body: BidPatch) -> None:
        open_ids = {
            cast(UUID, r[0])
            for r in self.repo.execute(
                "SELECT id FROM rfq_requirement WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                "AND NOT waived",
                {"rfq": rfq_id},
            ).fetchall()
        }
        errors = [
            ProblemFieldError(
                field=f"requirements/{a.requirement_id}",
                message="This is not an open requirement of the RFQ.",
            )
            for a in body.requirements or []
            if a.requirement_id not in open_ids
        ]
        if errors:
            raise self._fail(errors)
        self.repo.execute(
            "DELETE FROM bid_requirement WHERE org_id = %(org_id)s AND bid_id = %(bid)s",
            {"bid": bid_id},
        )
        for a in body.requirements or []:
            self.repo.execute(
                "INSERT INTO bid_requirement (org_id, bid_id, requirement_id, met, note) "
                "VALUES (%(org_id)s, %(bid)s, %(req)s, %(met)s, %(note)s)",
                {"bid": bid_id, "req": a.requirement_id, "met": a.met, "note": a.note},
            )

    def submit(
        self,
        rfq_id: UUID,
        bid_id: UUID,
        body: BidSubmit,
        if_match: str | None,
        may_accept_late: bool = False,
    ) -> BidRead | SealedBidRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rfq = self._lock_rfq(rfq_id)
            self._assert_not_awarded(rfq)
            if rfq[0] == "cancelled":
                raise conflict(
                    "rfq_cancelled", "This RFQ is cancelled. Its bids can no longer change."
                )
            if rfq[0] == "draft":
                raise conflict("rfq_not_published", "This RFQ is not published yet.")
            row = self._lock_bid(rfq_id, bid_id)
            self._own_draft(row, rfq if may_accept_late else None)
            self._check_edit_revision(cast(int, row[18]), if_match)
            errors = [
                ProblemFieldError(
                    field=f"lines/{r[0]}", message="Key a row for this line, quoted or not."
                )
                for r in self.repo.execute(
                    "SELECT l.id FROM rfq_line l WHERE l.org_id = %(org_id)s "
                    "AND l.rfq_id = %(rfq)s "
                    "AND NOT EXISTS (SELECT 1 FROM bid_line b WHERE b.bid_id = %(bid)s "
                    "AND b.rfq_line_id = l.id) ORDER BY l.line_no",
                    {"rfq": rfq_id, "bid": bid_id},
                ).fetchall()
            ]
            errors += [
                ProblemFieldError(
                    field=f"requirements/{r[0]}", message="Answer this requirement, met or not."
                )
                for r in self.repo.execute(
                    "SELECT q.id FROM rfq_requirement q WHERE q.org_id = %(org_id)s "
                    "AND q.rfq_id = %(rfq)s AND NOT q.waived AND NOT EXISTS "
                    "(SELECT 1 FROM bid_requirement a WHERE a.bid_id = %(bid)s "
                    "AND a.requirement_id = q.id) ORDER BY q.position",
                    {"rfq": rfq_id, "bid": bid_id},
                ).fetchall()
            ]
            evidence = cast(UUID | None, row[6])
            if evidence is None or not self._evidence_ok(bid_id, evidence):
                errors.append(
                    ProblemFieldError(
                        field="evidence_attachment_id",
                        message="Attach the vendor's email or file as evidence.",
                    )
                )
            if errors:
                raise self._fail(errors, "cannot_submit")
            late = self._before_submit(rfq, row, body, may_accept_late)
            self.repo.execute(  # `late` has fixed column names; its values are bound parameters
                "UPDATE bid SET status = 'submitted', submitted_at = now(), "
                + "".join(f"{k} = %({k})s, " for k in late)
                + "row_version = row_version + 1 WHERE org_id = %(org_id)s AND id = %(bid)s",
                {**late, "bid": bid_id},
            )
            self._bid_audit("bid.submit", rfq_id, {"bid_id": str(bid_id), "vendor_id": str(row[2])})
            if late:
                self._bid_audit(
                    "bid.late_override",
                    rfq_id,
                    {
                        "bid_id": str(bid_id),
                        "vendor_id": str(row[2]),
                        "reason": body.accept_late_reason,
                    },
                )
            return self._present(self._unsealed(rfq), self._lock_bid(rfq_id, bid_id))

    def replace(self, rfq_id: UUID, bid_id: UUID, body: BidReplace) -> BidRead | SealedBidRead:
        """A correction is a new draft with the original's structure and none of its figures;
        the original is never touched."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rfq = self._lock_rfq(rfq_id)
            original = self._lock_bid(rfq_id, bid_id)
            if not rfq[1]:  # now() < deadline_at, strictly
                raise conflict("deadline_passed", "The bidding stop time has passed.")
            self._assert_not_awarded(rfq)
            if original[15] != "submitted":
                raise conflict("not_submitted", "Only a submitted bid can be replaced.")
            if self.repo.execute(
                "SELECT 1 FROM bid WHERE org_id = %(org_id)s AND replaces_bid_id = %(bid)s",
                {"bid": bid_id},
            ).fetchone():
                raise conflict("already_replaced", "This bid already has a replacement.")
            new = uuid4()
            params = {"new": new, "bid": bid_id, "actor": self.actor_id, "sent": body.sent_at}
            self.repo.execute(
                "INSERT INTO bid (id, org_id, rfq_id, vendor_id, rfq_version, currency, sent_at, "
                "keyed_by, replaces_bid_id) SELECT %(new)s, org_id, rfq_id, vendor_id, "
                "rfq_version, currency, %(sent)s, %(actor)s, id FROM bid "
                "WHERE org_id = %(org_id)s AND id = %(bid)s",
                params,
            )
            # Structure only: the keyer re-keys every price and answers every requirement again.
            self.repo.execute(
                "INSERT INTO bid_line (org_id, bid_id, rfq_line_id, quantity, quoted) "
                "SELECT org_id, %(new)s, rfq_line_id, quantity, false FROM bid_line "
                "WHERE org_id = %(org_id)s AND bid_id = %(bid)s",
                params,
            )
            self._bid_audit(
                "bid.replace",
                rfq_id,
                {
                    "bid_id": str(new),
                    "replaces_bid_id": str(bid_id),
                    "vendor_id": str(original[2]),
                    "reason": body.reason,
                },
            )
            return self._present(self._unsealed(rfq), self._lock_bid(rfq_id, new))
