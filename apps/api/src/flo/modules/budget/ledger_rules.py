"""Single Python mirror of the ledger bucket and sign checks."""

from flo.modules.budget.models import LedgerBucket, LedgerType

BUCKET_FOR_TYPE: dict[LedgerType, frozenset[LedgerBucket]] = {
    LedgerType.ALLOCATION: frozenset({LedgerBucket.ALLOCATED}),
    LedgerType.TRANSFER: frozenset({LedgerBucket.ALLOCATED}),
    LedgerType.ADJUSTMENT: frozenset({LedgerBucket.ALLOCATED}),
    LedgerType.RESERVATION: frozenset({LedgerBucket.RESERVED}),
    LedgerType.COMMITMENT: frozenset({LedgerBucket.COMMITTED}),
    LedgerType.ACTUAL: frozenset({LedgerBucket.ACTUAL}),
    LedgerType.RELEASE: frozenset({LedgerBucket.RESERVED, LedgerBucket.COMMITTED}),
    LedgerType.REVERSAL: frozenset(LedgerBucket),
}


def expected_sign(entry_type: LedgerType) -> int | None:
    """Return +1/-1 for constrained signs, or None when either sign is allowed."""
    if entry_type in {
        LedgerType.ALLOCATION,
        LedgerType.RESERVATION,
        LedgerType.COMMITMENT,
        LedgerType.ACTUAL,
    }:
        return 1
    if entry_type == LedgerType.RELEASE:
        return -1
    return None
