"""Transactional reservations. D-M1-12: releases ignore fiscal period closure."""

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


def reserve(
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
) -> LedgerEntry:
    return post_entry(
        conn,
        scope,
        project_id=project_id,
        entry_type=LedgerType.RESERVATION,
        amount=amount,
        currency=currency,
        source_type=source_type,
        source_id=source_id,
        effective_date=effective_date,
        actor_id=actor_id,
        department_code=department_code,
        ledger_account_code=ledger_account_code,
        idempotency_key=idempotency_key,
    )


def release_reservation(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    reservation_entry_id: int,
    amount: Decimal | None,
    actor_id: UUID,
    reason: str,
    idempotency_key: str,
    effective_date: date | None = None,
) -> LedgerEntry | None:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = LedgerRepository(conn, scope)
        original = repo.entry("id = %(id)s", {"id": reservation_entry_id})
        if original is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        if original.entry_type != LedgerType.RESERVATION:
            raise invalid("This entry is not a reservation. Select a reservation entry.")
        balance = repo.balance(original.project_id, lock=True)
        if balance is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        existing = repo.entry("idempotency_key = %(key)s", {"key": idempotency_key})
        posting_date = effective_date or date.today()
        if existing is not None:
            release_amount = (
                -existing.amount if amount is None else quantize(amount, original.currency)
            )
        else:
            if repo.entry("reverses_entry_id = %(id)s", {"id": original.id}) is not None:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="This reservation was reversed. Select an active reservation.",
                    checks={"problem": "reservation_reversed"},
                )
            row = repo.execute(
                "SELECT remaining FROM reservation_remaining "
                "WHERE org_id = %(org_id)s AND reservation_entry_id = %(id)s",
                {"id": original.id},
            ).fetchone()
            assert row is not None
            remaining = cast(Decimal, row[0])
            release_amount = remaining if amount is None else quantize(amount, original.currency)
            if release_amount < 0 or (amount is not None and release_amount == 0):
                raise invalid(
                    "Release amount must be positive. Supply a positive amount.", field="amount"
                )
            if remaining == 0:
                return None
            if release_amount > remaining:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="Release exceeds the remaining reservation. Reduce the release amount.",
                    checks={"problem": "release_exceeds_reservation"},
                )
        return post_entry(
            conn,
            scope,
            project_id=original.project_id,
            entry_type=LedgerType.RELEASE,
            bucket=LedgerBucket.RESERVED,
            amount=-release_amount,
            currency=original.currency,
            source_type=original.source_type,
            source_id=original.source_id,
            effective_date=posting_date,
            actor_id=actor_id,
            department_code=original.department_code,
            ledger_account_code=original.ledger_account_code,
            idempotency_key=idempotency_key,
            reason=reason,
            releases_entry_id=original.id,
            skip_period_check=True,
        )
