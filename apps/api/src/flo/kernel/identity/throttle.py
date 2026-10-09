"""Global failed-login windows with hashed, domain-separated keys."""

import asyncio
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity.reset import ResetConnection

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _key(kind: str, value: str) -> str:
    return sha256(f"{kind}:{value}".encode(), usedforsecurity=True).hexdigest()


class LoginThrottle:
    """Count failures before verification; successes reset only the identity window."""

    def __init__(
        self,
        connection: ResetConnection,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = _utc_now,
        rng: Callable[[int, int], int] = random.SystemRandom().randint,
    ) -> None:
        self._connection = connection
        self._settings = settings
        self._clock = clock
        self._rng = rng

    def _count(self, kind: str, key: str) -> int:
        now = self._clock()
        row = self._connection.execute(
            """SELECT count(*) FROM login_attempt
               WHERE kind = %s AND key_hash = %s AND outcome = 'failed'
                 AND occurred_at > %s
                 AND (kind = 'client' OR id > COALESCE(
                     (SELECT max(id) FROM login_attempt
                      WHERE kind = 'identity' AND key_hash = %s
                        AND outcome = 'succeeded'), 0))""",
            (kind, key, now - timedelta(seconds=self._settings.login_window_seconds), key),
        ).fetchone()
        assert row is not None
        return int(str(row[0]))

    def _record(self, kind: str, key: str, outcome: str) -> None:
        self._connection.execute(
            "INSERT INTO login_attempt (kind, key_hash, outcome, occurred_at) "
            "VALUES (%s, %s, %s, %s)",
            (kind, key, outcome, self._clock()),
        )

    def check_client(self, client_value: str) -> bool:
        """Return whether the client has exhausted its failure window (E05-S13)."""
        return self._count("client", _key("client", client_value)) >= (
            self._settings.login_client_max_failures
        )

    def record_client_failure(self, client_value: str) -> None:
        """Persist one client failure without resetting the window (E05-S13)."""
        with self._connection.transaction():
            self._record("client", _key("client", client_value), "failed")
            # ponytail: bounded delete per failure; move to the nightly Worker trigger
            # if the table grows
            self._connection.execute(
                "DELETE FROM login_attempt WHERE id IN (SELECT id FROM login_attempt "
                "WHERE occurred_at < %s - interval '24 hours' LIMIT 100)",
                (self._clock(),),
            )

    async def check(self, email: str, client_value: str) -> None:
        """Reject uniformly before expensive verification, with client-only retry metadata."""
        keys = {
            "identity": _key("identity", email.strip().casefold()),
            "client": _key("client", client_value),
        }
        limits = {
            "identity": self._settings.login_identity_max_failures,
            "client": self._settings.login_client_max_failures,
        }
        # ponytail: unlocked count-then-insert may overshoot during a burst;
        # serialize decisions if bursts become excessive. No failure rows are lost.
        counts = {kind: self._count(kind, key) for kind, key in keys.items()}
        limited = [kind for kind in keys if counts[kind] >= limits[kind]]
        if not limited:
            return
        with self._connection.transaction():
            for kind in limited:
                self._record(kind, keys[kind], "throttled")
                logger.warning(
                    "auth.login_throttled kind=%s key_prefix=%s count=%s",
                    kind,
                    keys[kind][:8],
                    counts[kind],
                )
        await asyncio.sleep(
            self._rng(
                self._settings.login_throttle_jitter_min_ms,
                self._settings.login_throttle_jitter_max_ms,
            )
            / 1000
        )
        headers = (
            {"Retry-After": str(self._settings.login_window_seconds)}
            if ("client" in limited)
            else None
        )
        raise ProblemError(ErrorCode.UNAUTHORIZED, headers=headers)

    def record_failure(self, email: str, client_value: str) -> None:
        """Retain both pieces of failure evidence atomically."""
        with self._connection.transaction():
            self._record("identity", _key("identity", email.strip().casefold()), "failed")
            self.record_client_failure(client_value)

    def record_success(self, email: str) -> None:
        """Reset only the identity counter with an append-only success marker."""
        self._record("identity", _key("identity", email.strip().casefold()), "succeeded")
