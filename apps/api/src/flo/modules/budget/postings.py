"""Service-only commitments, actuals and explicit append-only corrections."""

from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.money import quantize
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget.ledger import LedgerRepository, invalid, post_entry
from flo.modules.budget.models import LedgerBucket, LedgerEntry, LedgerType
from flo.modules.budget.reservations import release_reservation
from flo.modules.org.service import assert_usable


def conflict(label: str, detail: str) -> ProblemError:
    return ProblemError(ErrorCode.CONFLICT, detail=detail, checks={"problem": label})


def original_entry(repo: LedgerRepository, entry_id: int) -> LedgerEntry:
    entry = repo.entry("id = %(id)s", {"id": entry_id})
    if entry is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    if repo.balance(entry.project_id, lock=True) is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return entry


def remaining(repo: LedgerRepository, entry: LedgerEntry) -> Decimal:
    kind = "reservation" if entry.entry_type == LedgerType.RESERVATION else "commitment"
    row = repo.execute(
        f"SELECT remaining FROM {kind}_remaining "
        f"WHERE org_id = %(org_id)s AND {kind}_entry_id = %(id)s",
        {"id": entry.id},
    ).fetchone()
    assert row is not None
    return cast(Decimal, row[0])


def release_commitment(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    commitment_entry_id: int,
    amount: Decimal | None,
    actor_id: UUID,
    reason: str,
    idempotency_key: str,
    effective_date: date | None = None,
) -> LedgerEntry | None:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = LedgerRepository(conn, scope)
        original = original_entry(repo, commitment_entry_id)
        if original.entry_type != LedgerType.COMMITMENT:
            raise invalid("Select a commitment entry to release.")
        existing = repo.entry("idempotency_key = %(key)s", {"key": idempotency_key})
        if existing is not None:
            released = -existing.amount if amount is None else quantize(amount, original.currency)
        else:
            if repo.entry("reverses_entry_id = %(id)s", {"id": original.id}) is not None:
                raise conflict(
                    "commitment_reversed",
                    "This commitment was reversed. Select an active commitment.",
                )
            held = remaining(repo, original)
            released = held if amount is None else quantize(amount, original.currency)
            if released < 0 or (amount is not None and released == 0):
                raise invalid(
                    "Release amount must be positive. Supply a positive amount.", field="amount"
                )
            if held == 0:
                return None
            if released > held:
                raise conflict(
                    "release_exceeds_commitment",
                    "Release exceeds the remaining commitment. Reduce the amount.",
                )
        return post_entry(
            conn,
            scope,
            project_id=original.project_id,
            entry_type=LedgerType.RELEASE,
            bucket=LedgerBucket.COMMITTED,
            amount=-released,
            currency=original.currency,
            source_type=original.source_type,
            source_id=original.source_id,
            effective_date=effective_date or date.today(),
            actor_id=actor_id,
            department_code=original.department_code,
            ledger_account_code=original.ledger_account_code,
            idempotency_key=idempotency_key,
            reason=reason,
            releases_entry_id=original.id,
            skip_period_check=True,
        )


def _consume(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    project_id: UUID,
    amount: Decimal,
    currency: str,
    source_type: str,
    source_id: UUID | None,
    effective_date: date,
    actor_id: UUID,
    department_code: str,
    ledger_account_code: str,
    idempotency_key: str,
    entry_type: LedgerType,
    converts_entry_id: int | None,
) -> LedgerEntry:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = LedgerRepository(conn, scope)
        # Serialize conversions and their replay before reading a remaining view.
        if repo.balance(project_id, lock=True) is None:
            raise ProblemError(ErrorCode.NOT_FOUND)

        def write() -> LedgerEntry:
            return post_entry(
                conn,
                scope,
                project_id=project_id,
                amount=amount,
                currency=currency,
                source_type=source_type,
                source_id=source_id,
                effective_date=effective_date,
                actor_id=actor_id,
                department_code=department_code,
                ledger_account_code=ledger_account_code,
                idempotency_key=idempotency_key,
                entry_type=entry_type,
                converts_entry_id=converts_entry_id,
            )

        # post_entry compares the immutable payload, including the conversion link.
        if repo.entry("idempotency_key = %(key)s", {"key": idempotency_key}) is not None:
            return write()
        amount = quantize(amount, currency)
        if amount <= 0:
            raise invalid("Amount must be positive. Supply a positive amount.", field="amount")
        if entry_type == LedgerType.ACTUAL:
            for kind, code in (
                ("department", department_code),
                ("ledger_account", ledger_account_code),
            ):
                if not code:
                    raise invalid(
                        "Actuals require department and ledger account codes. Supply the "
                        "missing code.",
                        field=f"{kind}_code",
                    )
                assert_usable(conn, scope, kind, code, effective_date)
        if converts_entry_id is not None:
            original = original_entry(repo, converts_entry_id)
            expected = (
                LedgerType.RESERVATION
                if entry_type == LedgerType.COMMITMENT
                else LedgerType.COMMITMENT
            )
            if original.project_id != project_id:
                raise ProblemError(ErrorCode.NOT_FOUND)
            if original.entry_type != expected:
                raise invalid(f"Select a {expected.value} entry for this conversion.")
            if original.currency != currency:
                raise invalid(
                    "Use the original entry's currency.", "currency_mismatch", field="currency"
                )
            if repo.entry("reverses_entry_id = %(id)s", {"id": original.id}) is not None:
                raise conflict(
                    "conversion_source_reversed",
                    "The conversion source was reversed. Select an active entry.",
                )
            held = remaining(repo, original)
            if entry_type == LedgerType.ACTUAL and amount > held:
                raise conflict(
                    "release_exceeds_commitment",
                    "Actual exceeds the remaining commitment. Reduce the amount.",
                )
            released = min(amount, held)
            if released > 0:
                if expected == LedgerType.RESERVATION:
                    release_reservation(
                        conn,
                        scope,
                        reservation_entry_id=original.id,
                        amount=released,
                        actor_id=actor_id,
                        reason="Converted to commitment",
                        idempotency_key=f"{idempotency_key}:release",
                        effective_date=effective_date,
                    )
                else:
                    release_commitment(
                        conn,
                        scope,
                        commitment_entry_id=original.id,
                        amount=released,
                        actor_id=actor_id,
                        reason="Consumed by actual",
                        idempotency_key=f"{idempotency_key}:release",
                        effective_date=effective_date,
                    )
        return write()


def commit(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    project_id: UUID,
    amount: Decimal,
    currency: str,
    source_type: str,
    source_id: UUID | None,
    effective_date: date,
    actor_id: UUID,
    department_code: str,
    ledger_account_code: str,
    idempotency_key: str,
    from_reservation_entry_id: int | None = None,
) -> LedgerEntry:
    return _consume(
        conn,
        scope,
        project_id=project_id,
        amount=amount,
        currency=currency,
        source_type=source_type,
        source_id=source_id,
        effective_date=effective_date,
        actor_id=actor_id,
        department_code=department_code,
        ledger_account_code=ledger_account_code,
        idempotency_key=idempotency_key,
        entry_type=LedgerType.COMMITMENT,
        converts_entry_id=from_reservation_entry_id,
    )


def record_actual(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    project_id: UUID,
    amount: Decimal,
    currency: str,
    source_type: str,
    source_id: UUID | None,
    effective_date: date,
    actor_id: UUID,
    department_code: str = "",
    ledger_account_code: str = "",
    idempotency_key: str,
    from_commitment_entry_id: int | None = None,
) -> LedgerEntry:
    return _consume(
        conn,
        scope,
        project_id=project_id,
        amount=amount,
        currency=currency,
        source_type=source_type,
        source_id=source_id,
        effective_date=effective_date,
        actor_id=actor_id,
        department_code=department_code,
        ledger_account_code=ledger_account_code,
        idempotency_key=idempotency_key,
        entry_type=LedgerType.ACTUAL,
        converts_entry_id=from_commitment_entry_id,
    )


def reverse_entry(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    entry_id: int,
    actor_id: UUID,
    effective_date: date,
    reason: str,
    idempotency_key: str,
) -> LedgerEntry:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = LedgerRepository(conn, scope)
        original = original_entry(repo, entry_id)
        if original.entry_type == LedgerType.TRANSFER:
            raise conflict(
                "cannot_reverse_transfer",
                "A transfer leg cannot be reversed. Make a new reverse transfer instead.",
            )
        existing = repo.entry("idempotency_key = %(key)s", {"key": idempotency_key})
        if existing is None:
            if original.entry_type == LedgerType.REVERSAL:
                raise conflict(
                    "cannot_reverse_reversal",
                    "A reversal cannot be reversed. Post an explicit replacement.",
                )
            if repo.entry("reverses_entry_id = %(id)s", {"id": original.id}) is not None:
                raise conflict(
                    "entry_already_reversed",
                    "This entry was already reversed. Post an explicit replacement.",
                )
            if (
                original.entry_type in (LedgerType.RESERVATION, LedgerType.COMMITMENT)
                and remaining(repo, original) < original.amount
            ):
                raise conflict(
                    "entry_partly_released",
                    "This entry was partly released. Release its remaining amount instead.",
                )
        return post_entry(
            conn,
            scope,
            project_id=original.project_id,
            entry_type=LedgerType.REVERSAL,
            amount=-original.amount,
            currency=original.currency,
            source_type=original.source_type,
            source_id=original.source_id,
            effective_date=effective_date,
            actor_id=actor_id,
            department_code=original.department_code,
            ledger_account_code=original.ledger_account_code,
            idempotency_key=idempotency_key,
            reason=reason,
            reverses_entry_id=original.id,
        )
