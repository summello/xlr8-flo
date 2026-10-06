"""Post ledger entries and their rollup on the caller's business transaction."""

from collections.abc import Mapping
from dataclasses import fields
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.db.repo import ScopedRepo
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.money import quantize
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget.ledger_rules import BUCKET_FOR_TYPE, expected_sign
from flo.modules.budget.models import LedgerBucket, LedgerEntry, LedgerType
from flo.modules.budget.schemas import BalanceRead
from flo.modules.org.service import OrgService, assert_postable

ENTRY_FIELDS = tuple(field.name for field in fields(LedgerEntry))
BALANCE_FIELDS = tuple(field.name for field in fields(BalanceRead))
PAYLOAD_FIELDS = (
    "entry_type",
    "bucket",
    "amount",
    "currency",
    "project_id",
    "source_type",
    "source_id",
    "effective_date",
    "transfer_group_id",
    "reverses_entry_id",
    "reason",
    "department_code",
    "ledger_account_code",
)


class InsufficientBudget(ProblemError):
    def __init__(self, available: Decimal, requested: Decimal) -> None:
        self.available = available
        self.requested = requested
        super().__init__(
            ErrorCode.INSUFFICIENT_BUDGET,
            detail="This entry exceeds available funds. Reduce it or allocate more budget.",
            checks={
                "problem": "insufficient_budget",
                "available": str(available),
                "requested": str(requested),
            },
        )


def invalid(
    detail: str,
    label: str = "invalid_ledger_entry",
    *,
    field: str | None = None,
) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail=detail,
        checks={"problem": label},
        errors=(ProblemFieldError(field=field, message=detail),) if field is not None else (),
    )


class LedgerRepository(ScopedRepo[object]):
    def __init__(self, conn: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(conn, scope)
        self.conn = conn

    def execute(
        self,
        query: str,
        params: Mapping[str, object] | None = None,
    ) -> psycopg.Cursor[tuple[object, ...]]:
        return self.conn.execute(query, self.scoped_params(params))

    def balance(self, project_id: UUID, *, lock: bool = False) -> BalanceRead | None:
        row = self.execute(
            f"SELECT {', '.join(BALANCE_FIELDS)} FROM project_balance "
            "WHERE org_id = %(org_id)s AND project_id = %(project_id)s"
            + (" FOR UPDATE" if lock else ""),
            {"project_id": project_id},
        ).fetchone()
        return (
            BalanceRead(**cast(dict[str, Any], dict(zip(BALANCE_FIELDS, row, strict=True))))
            if row
            else None
        )

    def entry(self, clause: str, params: Mapping[str, object]) -> LedgerEntry | None:
        row = self.execute(
            f"SELECT {', '.join(ENTRY_FIELDS)} FROM ledger_entry "
            f"WHERE org_id = %(org_id)s AND {clause}",
            params,
        ).fetchone()
        return read_entry(row) if row else None

    def update_balance(self, project_id: UUID, bucket: LedgerBucket, amount: Decimal) -> None:
        self.execute(
            f"UPDATE project_balance SET {bucket.value} = {bucket.value} + %(amount)s, "
            "version = version + 1, updated_at = clock_timestamp() "
            "WHERE org_id = %(org_id)s AND project_id = %(project_id)s",
            {"project_id": project_id, "amount": amount},
        )


def read_entry(row: tuple[object, ...]) -> LedgerEntry:
    values = dict(zip(ENTRY_FIELDS, row, strict=True))
    values["entry_type"] = LedgerType(cast(str, values["entry_type"]))
    values["bucket"] = LedgerBucket(cast(str, values["bucket"]))
    return LedgerEntry(**cast(dict[str, Any], values))


def get_balance(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
) -> BalanceRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        balance = LedgerRepository(conn, scope).balance(project_id)
        if balance is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return balance


def replay(original: LedgerEntry, payload: Mapping[str, object]) -> LedgerEntry:
    if any(getattr(original, field) != payload[field] for field in PAYLOAD_FIELDS):
        raise ProblemError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            detail="This key already identifies another entry. Use a new key for changed content.",
            checks={"problem": "idempotency_conflict"},
        )
    return original


def post_entry(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    project_id: UUID,
    entry_type: LedgerType,
    amount: Decimal,
    currency: str,
    source_type: str,
    source_id: UUID | None,
    effective_date: date,
    actor_id: UUID,
    department_code: str,
    ledger_account_code: str,
    idempotency_key: str,
    reason: str | None = None,
    bucket: LedgerBucket | None = None,
    transfer_group_id: UUID | None = None,
    reverses_entry_id: int | None = None,
    allow_negative: bool | None = None,
    releases_entry_id: int | None = None,
    skip_period_check: bool = False,
) -> LedgerEntry:
    """Savepoint preserves atomicity while the caller owns the outer transaction."""
    if releases_entry_id is not None:
        raise NotImplementedError("release linkage arrives in E07-S04")
    caller_bucket = bucket
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = LedgerRepository(conn, scope)
        balance = repo.balance(project_id, lock=True)
        if balance is None:
            repo.execute(
                "INSERT INTO project_balance(project_id, org_id, bu_id, currency) "
                "SELECT id, org_id, bu_id, currency FROM project "
                "WHERE org_id = %(org_id)s AND id = %(project_id)s "
                "ON CONFLICT (project_id) DO NOTHING",
                {"project_id": project_id},
            )
            balance = repo.balance(project_id, lock=True)
        if balance is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        existing = repo.entry("idempotency_key = %(key)s", {"key": idempotency_key})
        entry_type = LedgerType(entry_type)
        payload: dict[str, object] = {
            "project_id": project_id,
            "entry_type": entry_type,
            "bucket": bucket,
            "amount": amount,
            "currency": currency,
            "source_type": source_type,
            "source_id": source_id,
            "effective_date": effective_date,
            "transfer_group_id": transfer_group_id,
            "reverses_entry_id": reverses_entry_id,
            "reason": reason,
            "department_code": department_code,
            "ledger_account_code": ledger_account_code,
        }
        if existing is not None and any(
            getattr(existing, field) != payload[field]
            for field in PAYLOAD_FIELDS
            if field not in {"bucket", "amount", "currency"}
        ):
            return replay(existing, payload)
        if entry_type == LedgerType.REVERSAL:
            # Even derived reversal amounts must enter through the Decimal contract.
            quantize(amount, balance.currency)
            if caller_bucket is not None and existing is None:
                raise invalid(
                    "Reversal buckets come from the original entry. Omit the bucket.",
                    field="bucket",
                )
            original = repo.entry("id = %(id)s", {"id": reverses_entry_id})
            if original is None or original.project_id != project_id:
                raise ProblemError(ErrorCode.NOT_FOUND)
            bucket, amount, currency = original.bucket, -original.amount, original.currency
        elif entry_type != LedgerType.RELEASE and bucket is None:
            bucket = next(iter(BUCKET_FOR_TYPE[entry_type]))
        payload.update(bucket=bucket, amount=amount, currency=currency)
        if existing is not None:
            # Compare the persisted currency-rounded payload without checking period/policy.
            if currency == existing.currency:
                payload["amount"] = quantize(amount, currency)
            return replay(existing, payload)
        if entry_type == LedgerType.RELEASE:
            if bucket not in BUCKET_FOR_TYPE[entry_type]:
                raise invalid(
                    "Choose the reserved or committed bucket for a release.", field="bucket"
                )
        elif entry_type != LedgerType.REVERSAL and caller_bucket is not None:
            raise invalid("The entry type determines its bucket. Omit the bucket.", field="bucket")
        assert bucket is not None
        if currency != balance.currency:
            raise invalid(
                "Currency differs from the project. Use its currency.",
                "currency_mismatch",
                field="currency",
            )
        amount = quantize(amount, currency)
        payload["amount"] = amount
        if not skip_period_check:
            assert_postable(conn, scope, effective_date)
        elif entry_type != LedgerType.RELEASE:
            raise invalid(
                "Only releases may skip the period check. Post to an open period.",
                field="skip_period_check",
            )
        if (
            amount == 0
            or (expected_sign(entry_type) == 1 and amount < 0)
            or (expected_sign(entry_type) == -1 and amount > 0)
        ):
            raise invalid(
                "The amount has the wrong sign or rounds to zero. Correct the amount.",
                field="amount",
            )
        projected = (
            balance.available + amount
            if bucket == LedgerBucket.ALLOCATED
            else (balance.available - amount)
        )
        negative_allowed = allow_negative
        if negative_allowed is None:
            negative_allowed = (
                OrgService(conn, scope)
                .effective_setting(
                    balance.bu_id,
                    "allow_negative_budget",
                )
                .value
                is True
            )
        if projected < balance.available and projected < 0 and not negative_allowed:
            raise InsufficientBudget(balance.available, balance.available - projected)
        row = repo.execute(
            "INSERT INTO ledger_entry (org_id, bu_id, actor_id, idempotency_key, "
            + ", ".join(PAYLOAD_FIELDS)
            + ") VALUES (%(org_id)s, %(bu_id)s, %(actor_id)s, "
            "%(idempotency_key)s, "
            + ", ".join(f"%({field})s" for field in PAYLOAD_FIELDS)
            + ") ON CONFLICT (org_id, idempotency_key) DO NOTHING RETURNING "
            + ", ".join(ENTRY_FIELDS),
            payload
            | {"bu_id": balance.bu_id, "actor_id": actor_id, "idempotency_key": idempotency_key},
        ).fetchone()
        if row is None:
            existing = repo.entry("idempotency_key = %(key)s", {"key": idempotency_key})
            assert existing is not None
            return replay(existing, payload)
        entry = read_entry(row)
        repo.update_balance(project_id, bucket, amount)
        after = {
            "entry_id": entry.id,
            "type": entry_type,
            "bucket": bucket,
            "amount": amount,
            "source_type": source_type,
            "source_id": source_id,
        }
        AuditWriter(cast(AuditConnection, conn), scope).write(
            actor=AuditActor(ActorKind.USER, actor_id),
            action="ledger.post",
            target_type="project",
            target_id=project_id,
            bu_id=balance.bu_id,
            outcome=Outcome.SUCCESS,
            after_source=after,
            after_fields=tuple(after),
        )
        return entry
