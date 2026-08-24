from __future__ import annotations

import json
import logging
import statistics
import time
from dataclasses import asdict
from unittest.mock import patch

import pytest
from argon2 import PasswordHasher

from flo.kernel.identity import PasswordPolicyError, build_local_identity_provider
from flo.kernel.identity.port import IdentityProvider

from .conftest import FakeIdentityConnection, fast_settings


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


@pytest.mark.parametrize(
    ("path", "expected_authenticated"),
    [
        ("unknown", False),
        ("current_mismatch", False),
        ("stale_mismatch", False),
        ("malformed", False),
        ("current_success", True),
        ("stale_success", True),
    ],
)
def test_each_authentication_path_performs_exactly_one_verification(
    identity_connection: FakeIdentityConnection,
    path: str,
    expected_authenticated: bool,
) -> None:
    password = "the known safe passphrase"
    if path.startswith("stale"):
        old_provider = build_local_identity_provider(
            identity_connection, fast_settings(time_cost=1)
        )
        old_provider.create_identity("known@example.test", password)
    elif path != "unknown":
        current_provider = build_local_identity_provider(
            identity_connection, fast_settings(time_cost=2)
        )
        current_provider.create_identity("known@example.test", password)

    provider = build_local_identity_provider(identity_connection, fast_settings(time_cost=2))
    if path == "malformed":
        identity_id, _ = identity_connection.identities["known@example.test"]
        identity_connection.identities["known@example.test"] = (identity_id, "invalid hash")

    email = "unknown@example.test" if path == "unknown" else "known@example.test"
    attempted_password = password if path.endswith("success") else "an incorrect safe passphrase"
    original_verify = PasswordHasher.verify

    with patch.object(
        PasswordHasher,
        "verify",
        autospec=True,
        side_effect=original_verify,
    ) as verify:
        result = provider.authenticate(email, attempted_password)

    assert result.authenticated is expected_authenticated
    assert verify.call_count == 1


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
