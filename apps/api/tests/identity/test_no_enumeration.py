from __future__ import annotations

import json
import logging
import statistics
import time
from dataclasses import asdict
from unittest.mock import patch

import pytest
from argon2 import PasswordHasher, extract_parameters

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


def test_stale_known_hash_and_unknown_email_do_equal_current_cost_work(
    identity_connection: FakeIdentityConnection,
) -> None:
    password = "the known safe passphrase"
    old_provider = build_local_identity_provider(identity_connection, fast_settings(time_cost=1))
    old_provider.create_identity("known@example.test", password)
    provider = build_local_identity_provider(identity_connection, fast_settings(time_cost=2))
    original_verify = PasswordHasher.verify

    with patch.object(
        PasswordHasher,
        "verify",
        autospec=True,
        side_effect=original_verify,
    ) as verify:
        provider.authenticate("unknown@example.test", "an incorrect safe passphrase")
        unknown_work = [extract_parameters(call.args[1]) for call in verify.call_args_list]
        assert len(unknown_work) == 1
        current_parameters = unknown_work[0]
        verify.reset_mock()

        provider.authenticate("known@example.test", "an incorrect safe passphrase")
        known_work = [extract_parameters(call.args[1]) for call in verify.call_args_list]

    assert unknown_work == [current_parameters]
    assert current_parameters.time_cost == 2
    assert current_parameters.memory_cost == 8 * 1024
    assert current_parameters.parallelism == 1
    assert known_work.count(current_parameters) == len(unknown_work)
    assert [parameters.time_cost for parameters in known_work] == [1, 2]


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
