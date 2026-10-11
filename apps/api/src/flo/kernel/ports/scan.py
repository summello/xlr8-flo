"""Synchronous basic-check scanning port; full reputation scanning follows at graduation."""

from dataclasses import dataclass
from typing import Literal, Protocol

ScanVerdict = Literal["basic_checks_passed", "rejected"]


@dataclass(frozen=True)
class ScanResult:
    verdict: ScanVerdict
    media_type: str | None
    reason: str | None


class Scanner(Protocol):
    def scan(self, data: bytes, *, filename: str) -> ScanResult: ...
