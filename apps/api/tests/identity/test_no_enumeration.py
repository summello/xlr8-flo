from __future__ import annotations

import json
import logging
import statistics
import time
from dataclasses import asdict

import pytest

from flo.kernel.identity import PasswordPolicyError
from flo.kernel.identity.port import IdentityProvider

from .conftest import FakeIdentityConnection


def _failure_wire_bytes(result: object) -> bytes:
    return json.dumps(asdict(result), sort_keys=True, separators=(",", ":")).encode()


def test_unknown_email_and_wrong_password_have_byte_identical_failure_results(
    identity_provider: IdentityProvider,
    identity_connection: FakeIdentityConnection,
) -> None:
    identity_provider.create_identity("known@example.test", "the known safe passphrase")

    wrong_email = identity_provider.authenticate(
        "unknown@example.test", "an incorrect safe passphrase"
    )
    wrong_password = identity_provider.authenticate(
        "known@example.test", "an incorrect safe passphrase"
    )

    assert wrong_email == wrong_password
    assert wrong_email.status == wrong_password.status
    assert _failure_wire_bytes(wrong_email) == _failure_wire_bytes(wrong_password)

    identity_id, _ = identity_connection.identities["known@example.test"]
    identity_connection.identities["known@example.test"] = (identity_id, "invalid hash")
    malformed_hash = identity_provider.authenticate(
        "known@example.test", "an incorrect safe passphrase"
    )
    assert malformed_hash == wrong_email


def test_unknown_email_and_wrong_password_medians_differ_by_less_than_ten_percent(
    identity_provider: IdentityProvider,
) -> None:
    identity_provider.create_identity("known@example.test", "the known safe passphrase")
    unknown_timings: list[int] = []
    wrong_timings: list[int] = []

    for _ in range(100):
        started = time.perf_counter_ns()
        identity_provider.authenticate("unknown@example.test", "an incorrect safe passphrase")
        unknown_timings.append(time.perf_counter_ns() - started)

        started = time.perf_counter_ns()
        identity_provider.authenticate("known@example.test", "an incorrect safe passphrase")
        wrong_timings.append(time.perf_counter_ns() - started)

    unknown_median = statistics.median(unknown_timings)
    wrong_median = statistics.median(wrong_timings)
    relative_difference = abs(unknown_median - wrong_median) / max(
        unknown_median, wrong_median
    )

    assert relative_difference < 0.10


def test_passwords_do_not_appear_in_results_errors_logs_or_stored_rows(
    identity_provider: IdentityProvider,
    identity_connection: FakeIdentityConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive = "never expose this safe passphrase"
    caplog.set_level(logging.DEBUG)
    identity_provider.create_identity("secret@example.test", sensitive)
    failure = identity_provider.authenticate("secret@example.test", sensitive + " wrong")

    with pytest.raises(PasswordPolicyError) as captured:
        identity_provider.create_identity("invalid@example.test", "too short")

    observable = " ".join(
        [
            repr(failure),
            repr(captured.value),
            str(captured.value),
            caplog.text,
            *[repr(row) for row in identity_connection.identities.values()],
        ]
    )
    assert sensitive not in observable
    assert "too short" not in observable
