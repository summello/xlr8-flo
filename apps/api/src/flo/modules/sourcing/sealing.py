"""Sealed bids (SRC-007): no bid figure leaves the server before the bidding stop time."""

from datetime import datetime


def visible(deadline_at: datetime | None, now: datetime) -> bool:
    """Bid content may be read once `now` reaches the RFQ's deadline; no deadline stays sealed.

    Every read path that returns bid content calls this (bids list/detail, comparison,
    evaluation, exports, the link graph). `deadline_at` and `now` both come from the database.
    """
    return deadline_at is not None and now >= deadline_at
