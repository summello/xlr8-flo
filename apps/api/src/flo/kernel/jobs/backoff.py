"""Bounded exponential retry delays shared by jobs and the outbox."""

from __future__ import annotations

import random
from collections.abc import Callable

BASE_DELAYS_SECONDS = (1, 4, 16, 64, 256)
MAX_JITTER_FRACTION = 0.25
type RandomFraction = Callable[[], float]


def backoff_seconds(attempt: int, random_fraction: RandomFraction = random.random) -> float:
    """Return the attempt's 4x delay plus up to 25 percent positive jitter."""

    if attempt < 1 or attempt > len(BASE_DELAYS_SECONDS):
        raise ValueError("backoff attempt must be between 1 and 5")
    fraction = random_fraction()
    if fraction < 0 or fraction >= 1:
        raise ValueError("backoff random fraction must be in [0, 1)")
    base = BASE_DELAYS_SECONDS[attempt - 1]
    return base + base * MAX_JITTER_FRACTION * fraction
