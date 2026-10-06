"""Immutable ledger row and database enum values."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class LedgerType(StrEnum):
    ALLOCATION = "allocation"
    RESERVATION = "reservation"
    COMMITMENT = "commitment"
    ACTUAL = "actual"
    RELEASE = "release"
    REVERSAL = "reversal"
    TRANSFER = "transfer"
    ADJUSTMENT = "adjustment"


class LedgerBucket(StrEnum):
    ALLOCATED = "allocated"
    RESERVED = "reserved"
    COMMITTED = "committed"
    ACTUAL = "actual"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    id: int
    org_id: UUID
    bu_id: UUID
    project_id: UUID
    entry_type: LedgerType
    bucket: LedgerBucket
    amount: Decimal
    currency: str
    source_type: str
    source_id: UUID | None
    transfer_group_id: UUID | None
    reverses_entry_id: int | None
    department_code: str
    ledger_account_code: str
    effective_date: date
    posted_at: datetime
    actor_id: UUID
    reason: str | None
    idempotency_key: str | None
