"""Shared, memory-bounded Argon2id operations for standing credentials."""

from __future__ import annotations

import asyncio
from asyncio import AbstractEventLoop
from collections.abc import Callable
from weakref import WeakKeyDictionary

from argon2 import PasswordHasher, Type

from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError

ARGON2_WAIT_TIMEOUT_SECONDS = 2.0
ARGON2_RETRY_AFTER_SECONDS = 2

# One semaphore per running event loop is process-global to password and recovery-code
# hashing on that loop. Weak keys keep short-lived test loops from being retained.
_ARGON2_SEMAPHORES: WeakKeyDictionary[AbstractEventLoop, tuple[int, asyncio.Semaphore]] = (
    WeakKeyDictionary()
)


def build_argon2_hasher(settings: Settings) -> PasswordHasher:
    """Build the single configured Argon2id profile used by local credentials."""

    return PasswordHasher(
        time_cost=settings.identity_argon2_time_cost,
        memory_cost=settings.identity_argon2_memory_cost_kib,
        parallelism=settings.identity_argon2_parallelism,
        type=Type.ID,
    )


def _argon2_semaphore(max_concurrency: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    configured = _ARGON2_SEMAPHORES.get(loop)
    if configured is None:
        semaphore = asyncio.Semaphore(max_concurrency)
        _ARGON2_SEMAPHORES[loop] = (max_concurrency, semaphore)
        return semaphore
    capacity, semaphore = configured
    if capacity != max_concurrency:
        raise RuntimeError("Argon2 concurrency must be configured once per process")
    return semaphore


async def acquire_argon2(semaphore: asyncio.Semaphore) -> None:
    """Wait briefly for bounded hashing capacity or fail safely under overload."""

    try:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=ARGON2_WAIT_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise ProblemError(
            ErrorCode.SERVICE_UNAVAILABLE,
            headers={"Retry-After": str(ARGON2_RETRY_AFTER_SECONDS)},
        ) from exc


async def try_acquire_argon2(semaphore: asyncio.Semaphore) -> bool:
    """Acquire immediately when capacity is free without joining the wait queue."""

    if semaphore.locked():
        return False
    await semaphore.acquire()
    return True


def argon2_semaphore(max_concurrency: int) -> asyncio.Semaphore:
    """Return the process-wide semaphore shared by every credential operation."""

    return _argon2_semaphore(max_concurrency)


async def run_argon2[**P, T](
    max_concurrency: int,
    operation: Callable[P, T],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Run one Argon2 operation off-loop within the configured memory bound."""

    semaphore = _argon2_semaphore(max_concurrency)
    await acquire_argon2(semaphore)
    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
    finally:
        semaphore.release()
