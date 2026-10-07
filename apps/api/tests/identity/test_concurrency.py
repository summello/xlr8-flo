from __future__ import annotations

import asyncio
import statistics
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import patch

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import FastAPI

from flo.kernel.config import Settings
from flo.kernel.errors import install_problem_details
from flo.kernel.identity import build_local_identity_provider

from .conftest import FakeIdentityConnection


def _settings(max_concurrency: int) -> Settings:
    return Settings(
        identity_argon2_time_cost=1,
        identity_argon2_memory_cost_kib=8 * 1024,
        identity_argon2_parallelism=1,
        identity_argon2_max_concurrency=max_concurrency,
    )


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


@dataclass
class _InFlight:
    current: int = 0
    peak: int = 0


@contextmanager
def _tracked_verifications(delay_seconds: float):
    original_verify = PasswordHasher.verify
    in_flight = _InFlight()
    lock = threading.Lock()

    def tracked(hasher: PasswordHasher, password_hash: str, password: str) -> bool:
        with lock:
            in_flight.current += 1
            in_flight.peak = max(in_flight.peak, in_flight.current)
        try:
            time.sleep(delay_seconds)
            return original_verify(hasher, password_hash, password)
        finally:
            with lock:
                in_flight.current -= 1

    with patch.object(PasswordHasher, "verify", autospec=True, side_effect=tracked):
        yield in_flight


def test_32_logins_never_exceed_the_configured_argon2_bound() -> None:
    async def scenario() -> int:
        connection = FakeIdentityConnection()
        settings = _settings(max_concurrency=4)
        provider = await build_local_identity_provider(connection, settings)

        with _tracked_verifications(delay_seconds=0.02) as in_flight:
            results = await asyncio.gather(
                *(
                    provider.authenticate(
                        f"unknown-{number}@example.test",
                        "an incorrect safe passphrase",
                    )
                    for number in range(32)
                )
            )

        assert all(not result.authenticated for result in results)
        return in_flight.peak

    assert _run(scenario) <= 4


def test_saturated_unknown_and_known_paths_remain_timing_equivalent() -> None:
    async def scenario() -> tuple[list[int], list[int]]:
        connection = FakeIdentityConnection()
        provider = await build_local_identity_provider(connection, _settings(max_concurrency=1))
        await provider.create_identity("known@example.test", "the known safe passphrase")
        unknown_timings: list[int] = []
        wrong_timings: list[int] = []
        blocker_password = "a dedicated blocking passphrase"

        for email, timings in (
            ("unknown@example.test", unknown_timings),
            ("known@example.test", wrong_timings),
        ) * 20:
            entered = threading.Event()
            release = threading.Event()

            def verify(
                _hasher: PasswordHasher, _password_hash: str, password: str
            ) -> bool:
                if password == blocker_password:
                    entered.set()
                    release.wait(timeout=5)
                time.sleep(0.005)
                raise VerifyMismatchError

            with patch.object(PasswordHasher, "verify", autospec=True, side_effect=verify):
                blocker = asyncio.create_task(
                    provider.authenticate("blocker@example.test", blocker_password)
                )
                assert await asyncio.to_thread(entered.wait, 1)
                started = time.perf_counter_ns()
                target = asyncio.create_task(
                    provider.authenticate(email, "an incorrect safe passphrase")
                )
                await asyncio.sleep(0.01)
                release.set()
                await target
                timings.append(time.perf_counter_ns() - started)
                await blocker

        return unknown_timings, wrong_timings

    unknown, wrong = _run(scenario)
    relative_difference = abs(statistics.median(unknown) - statistics.median(wrong)) / max(
        statistics.median(unknown), statistics.median(wrong)
    )
    assert relative_difference < 0.10


def test_saturated_rehash_is_skipped_without_rejecting_valid_credentials() -> None:
    async def scenario() -> tuple[bool, str, str]:
        connection = FakeIdentityConnection()
        old_provider = await build_local_identity_provider(
            connection, _settings(max_concurrency=1)
        )
        password = "the valid rehash passphrase"
        identity_id = await old_provider.create_identity("rehash@example.test", password)
        old_hash = connection.identities["rehash@example.test"][1]
        provider = await build_local_identity_provider(
            connection,
            Settings(
                identity_argon2_time_cost=2,
                identity_argon2_memory_cost_kib=8 * 1024,
                identity_argon2_parallelism=1,
                identity_argon2_max_concurrency=1,
            ),
        )
        target_entered = threading.Event()
        release_target = threading.Event()
        blocker_entered = threading.Event()
        release_blocker = threading.Event()
        original_verify = PasswordHasher.verify

        def verify(
            hasher: PasswordHasher, password_hash: str, candidate: str
        ) -> bool:
            if candidate == password:
                target_entered.set()
                release_target.wait(timeout=5)
                return original_verify(hasher, password_hash, candidate)
            blocker_entered.set()
            release_blocker.wait(timeout=5)
            raise VerifyMismatchError

        with patch.object(PasswordHasher, "verify", autospec=True, side_effect=verify):
            target = asyncio.create_task(provider.authenticate("rehash@example.test", password))
            assert await asyncio.to_thread(target_entered.wait, 1)
            blocker = asyncio.create_task(
                provider.authenticate("blocker@example.test", "a blocking passphrase")
            )
            await asyncio.sleep(0)
            release_target.set()
            assert await asyncio.to_thread(blocker_entered.wait, 1)
            try:
                result = await target
            finally:
                release_blocker.set()
                await blocker

        assert result.identity_id == identity_id
        return result.authenticated, old_hash, connection.identities["rehash@example.test"][1]

    authenticated, old_hash, stored_hash = _run(scenario)

    assert authenticated
    assert stored_hash == old_hash


def test_33rd_login_times_out_as_503_with_retry_after() -> None:
    async def scenario() -> httpx.Response:
        connection = FakeIdentityConnection()
        provider = await build_local_identity_provider(connection, _settings(max_concurrency=32))
        release = threading.Event()
        all_permits_taken = asyncio.Event()
        lookups = 0
        original_execute = connection.execute

        def execute(query: str, params: tuple[object, ...] = ()):
            nonlocal lookups
            result = original_execute(query, params)
            if query.startswith("SELECT"):
                lookups += 1
                if lookups == 32:
                    all_permits_taken.set()
            return result

        def verify(
            _hasher: PasswordHasher, _password_hash: str, _password: str
        ) -> bool:
            release.wait(timeout=5)
            raise VerifyMismatchError

        app = FastAPI()
        install_problem_details(app)

        @app.post("/login")
        async def login() -> dict[str, str]:
            await provider.authenticate("request-33@example.test", "wrong safe passphrase")
            return {"status": "unexpected"}

        with (
            patch.object(connection, "execute", side_effect=execute),
            patch.object(PasswordHasher, "verify", autospec=True, side_effect=verify),
        ):
            occupying = [
                asyncio.create_task(
                    provider.authenticate(
                        f"request-{number}@example.test", "wrong safe passphrase"
                    )
                )
                for number in range(32)
            ]
            await asyncio.wait_for(all_permits_taken.wait(), timeout=1)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    response = await client.post("/login")
            finally:
                release.set()
                await asyncio.gather(*occupying)

        assert lookups == 32
        return response

    response = _run(scenario)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.json()["type"] == "https://xlr8flo.app/errors/service-unavailable"
