"""NIST-aligned password normalization, length, and breach checks."""

from __future__ import annotations

import hashlib
import unicodedata
from collections import defaultdict
from functools import lru_cache
from importlib.resources import files

from flo.kernel.identity.port import PolicyResult, PolicyViolation

PASSWORD_ONLY_MINIMUM = 15
MFA_MINIMUM = 8
PASSWORD_MAXIMUM = 256
_HASH_PREFIX_LENGTH = 5


def normalize_password(password: str) -> str:
    """Normalize Unicode without trimming or otherwise changing whitespace."""

    return unicodedata.normalize("NFKC", password)


class BreachBlocklist:
    """Local k-anonymity-style SHA-1 prefix index of compromised passwords."""

    def __init__(self, entries: dict[str, frozenset[str]]) -> None:
        self._entries = entries

    @classmethod
    def bundled(cls) -> BreachBlocklist:
        """Load the bundled top-100k compromised-password hash index."""

        grouped: defaultdict[str, set[str]] = defaultdict(set)
        resource = files("flo.kernel.identity.data").joinpath("blocklist.txt")
        for line in resource.read_text(encoding="ascii").splitlines():
            if not line or line.startswith("#"):
                continue
            prefix, separator, suffix = line.partition(":")
            if separator != ":" or len(prefix) != _HASH_PREFIX_LENGTH or len(suffix) != 35:
                raise RuntimeError("invalid bundled password blocklist")
            grouped[prefix].add(suffix)
        return cls({prefix: frozenset(suffixes) for prefix, suffixes in grouped.items()})

    def contains(self, password: str) -> bool:
        """Check a password by matching its SHA-1 suffix only within its prefix bucket."""

        digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
        return digest[_HASH_PREFIX_LENGTH:] in self._entries.get(
            digest[:_HASH_PREFIX_LENGTH], ()
        )


@lru_cache(maxsize=1)
def bundled_blocklist() -> BreachBlocklist:
    """Return the process-wide immutable bundled blocklist."""

    return BreachBlocklist.bundled()


class PasswordPolicy:
    """Apply length and compromise checks without composition or rotation rules."""

    def __init__(
        self,
        *,
        mfa_enrolled: bool = False,
        blocklist: BreachBlocklist | None = None,
    ) -> None:
        self._minimum = MFA_MINIMUM if mfa_enrolled else PASSWORD_ONLY_MINIMUM
        self._blocklist = blocklist or bundled_blocklist()

    def verify(self, password: str) -> PolicyResult:
        """Return every policy violation for the NFKC-normalized password."""

        normalized = normalize_password(password)
        violations: list[PolicyViolation] = []
        if len(normalized) < self._minimum:
            violations.append(PolicyViolation.TOO_SHORT)
        if len(normalized) > PASSWORD_MAXIMUM:
            violations.append(PolicyViolation.TOO_LONG)
        if self._blocklist.contains(normalized):
            violations.append(PolicyViolation.COMPROMISED)
        return PolicyResult(tuple(violations))
