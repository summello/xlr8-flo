from __future__ import annotations

import importlib.util
import json
import os
import smtplib
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

from flo.kernel.adapters.resend import ResendEmailSender, create_resend_sender
from flo.kernel.adapters.smtp import SmtpEmailSender
from flo.kernel.config import Settings
from flo.kernel.email import create_email_sender
from flo.kernel.logging import correlation_context
from flo.kernel.outbox import OutboxDispatcher, OutboxStore, email_handler
from flo.kernel.outbox.dispatcher import DispatcherConnection, PermanentOutboxError
from flo.kernel.outbox.store import OutboxConnection, OutboxRecord, OutboxState
from flo.kernel.ports.email import (
    PermanentEmailError,
    TransientEmailError,
)
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction

ROOT = Path(__file__).resolve().parents[4]
AUDIT_MIGRATION = ROOT / "migrations" / "20260825_0005_audit_log.py"
JOBS_MIGRATION = ROOT / "migrations" / "20260825_0007_jobs_outbox.py"


def _load_migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _drop_objects(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    connection.execute("DROP TABLE IF EXISTS outbox")
    connection.execute("DROP TABLE IF EXISTS job")
    rows = connection.execute(
        "SELECT tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname = 'public' AND tablename LIKE 'audit_log_%'"
    ).fetchall()
    for row in rows:
        connection.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(cast(str, row[0])))
        )
    connection.execute("DROP TABLE IF EXISTS audit_log")
    connection.execute("DROP FUNCTION IF EXISTS raise_append_only()")


@pytest.fixture
def outbox_database() -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    configured = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    database_url = configured or "postgresql://flo:flo-local@127.0.0.1:5432/flo_test"
    database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        connection = psycopg.connect(database_url, autocommit=True)
    except psycopg.Error as error:
        if configured:
            pytest.fail(f"configured Postgres is unavailable: {type(error).__name__}")
        pytest.skip("local Postgres is unavailable; run the repository stack")
        raise
    _drop_objects(connection)
    _load_migration(AUDIT_MIGRATION, "outbox_audit_migration").upgrade(connection)
    _load_migration(JOBS_MIGRATION, "outbox_migration").upgrade(connection)
    try:
        yield connection
    finally:
        _drop_objects(connection)
        connection.close()


def _add_email(
    connection: psycopg.Connection[tuple[object, ...]],
    org_id: UUID,
    *,
    key: str = "send-purchase-order-42",
) -> UUID:
    with correlation_context("po-issue-request"), tenant_transaction(
        cast(OutboxConnection, connection), Scope(org_id)
    ):
        return OutboxStore(cast(OutboxConnection, connection), Scope(org_id)).add_email(
            to="buyer@example.invalid",
            template="purchase-order-issued-v1",
            context={"purchase_order": "PO-00042"},
            idempotency_key=key,
        ).id


def test_business_change_and_outbox_row_commit_or_roll_back_together(
    outbox_database: psycopg.Connection[tuple[object, ...]],
) -> None:
    connection = outbox_database
    org_id = uuid4()
    business_table = f"outbox_business_{uuid4().hex[:12]}"
    committed, rolled_back = uuid4(), uuid4()
    connection.execute(
        sql.SQL("CREATE TABLE {} (id uuid PRIMARY KEY)").format(sql.Identifier(business_table))
    )
    try:
        with correlation_context("commit-request"), tenant_transaction(
            cast(OutboxConnection, connection), Scope(org_id)
        ):
            connection.execute(
                sql.SQL("INSERT INTO {} VALUES (%s)").format(sql.Identifier(business_table)),
                (committed,),
            )
            OutboxStore(cast(OutboxConnection, connection), Scope(org_id)).add_email(
                to="vendor@example.invalid",
                template="po-v1",
                context={"id": str(committed)},
                idempotency_key="committed-effect",
            )

        with pytest.raises(RuntimeError, match="forced rollback"), correlation_context(
            "rollback-request"
        ), tenant_transaction(cast(OutboxConnection, connection), Scope(org_id)):
            connection.execute(
                sql.SQL("INSERT INTO {} VALUES (%s)").format(sql.Identifier(business_table)),
                (rolled_back,),
            )
            OutboxStore(cast(OutboxConnection, connection), Scope(org_id)).add_email(
                to="vendor@example.invalid",
                template="po-v1",
                context={"id": str(rolled_back)},
                idempotency_key="rolled-back-effect",
            )
            raise RuntimeError("forced rollback")

        assert connection.execute(
            sql.SQL("SELECT id FROM {}").format(sql.Identifier(business_table))
        ).fetchall() == [(committed,)]
        assert connection.execute(
            "SELECT idempotency_key, correlation_id FROM outbox"
        ).fetchall() == [("committed-effect", "commit-request")]
    finally:
        connection.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(business_table))
        )


class AmbiguousEmailSender:
    def __init__(self) -> None:
        self.delivered: dict[str, str] = {}
        self.calls: list[str] = []

    def send(
        self,
        to: str,
        template: str,
        context: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        assert to == "buyer@example.invalid"
        assert template == "purchase-order-issued-v1"
        assert context == {"purchase_order": "PO-00042"}
        self.calls.append(idempotency_key)
        existing = self.delivered.get(idempotency_key)
        if existing is not None:
            return existing
        message_id = "provider-message-42"
        self.delivered[idempotency_key] = message_id
        raise TransientEmailError("ambiguous provider timeout")


def test_ambiguous_timeout_retry_delivers_once_and_records_provider_id(
    outbox_database: psycopg.Connection[tuple[object, ...]],
) -> None:
    connection = outbox_database
    org_id = uuid4()
    outbox_id = _add_email(connection, org_id)
    sender = AmbiguousEmailSender()
    dispatcher = OutboxDispatcher(
        cast(DispatcherConnection, connection),
        {"email": email_handler(sender)},
        random_fraction=lambda: 0.0,
    )

    first = dispatcher.run()
    assert first.retried == 1
    connection.execute(
        "UPDATE outbox SET run_after = CURRENT_TIMESTAMP WHERE id = %s", (outbox_id,)
    )
    second = dispatcher.run()

    assert second.sent == 1
    assert sender.calls == ["send-purchase-order-42", "send-purchase-order-42"]
    assert sender.delivered == {"send-purchase-order-42": "provider-message-42"}
    assert connection.execute(
        "SELECT state, attempts, provider_message_id, sent_at IS NOT NULL "
        "FROM outbox WHERE id = %s",
        (outbox_id,),
    ).fetchone() == ("sent", 2, "provider-message-42", True)
    assert connection.execute(
        "SELECT action, correlation_id FROM audit_log "
        "WHERE org_id = %s AND target_id = %s ORDER BY occurred_at",
        (org_id, outbox_id),
    ).fetchall() == [
        ("outbox.retry", "po-issue-request"),
        ("outbox.sent", "po-issue-request"),
    ]


def test_permanent_provider_rejection_goes_dead_without_retry(
    outbox_database: psycopg.Connection[tuple[object, ...]],
) -> None:
    connection = outbox_database
    org_id = uuid4()
    outbox_id = _add_email(connection, org_id, key="permanent-rejection")

    class RejectingSender:
        def send(self, *_: object) -> str:
            raise PermanentEmailError("recipient is invalid")

    summary = OutboxDispatcher(
        cast(DispatcherConnection, connection),
        {"email": email_handler(RejectingSender())},
        random_fraction=lambda: 0.0,
    ).run()

    assert summary.dead == 1
    assert summary.retried == 0
    assert connection.execute(
        "SELECT state, attempts, last_error FROM outbox WHERE id = %s", (outbox_id,)
    ).fetchone() == ("dead", 1, "recipient is invalid")
    with tenant_transaction(cast(OutboxConnection, connection), Scope(org_id)):
        dead = OutboxStore(cast(OutboxConnection, connection), Scope(org_id)).list_dead()
    assert [row.id for row in dead] == [outbox_id]


def _outbox_record(payload: dict[str, object]) -> OutboxRecord:
    now = datetime.now(UTC)
    return OutboxRecord(
        id=uuid4(),
        org_id=uuid4(),
        topic="email",
        payload=payload,
        state=OutboxState.PENDING,
        attempts=1,
        max_attempts=5,
        run_after=now,
        locked_at=now,
        last_error=None,
        provider_message_id=None,
        idempotency_key="email-key",
        correlation_id="request-correlation",
    )


def test_deleted_email_payload_field_fails_as_missing_not_by_absence() -> None:
    class UnusedSender:
        def send(self, *_: object) -> str:
            raise AssertionError("provider must not run for an invalid payload")

    deliver = email_handler(UnusedSender())
    with pytest.raises(PermanentOutboxError, match="MISSING.*context"):
        deliver(_outbox_record({"to": "a@example.invalid", "template": "v1"}))


def test_deleted_required_topic_mapping_fails_as_missing() -> None:
    with pytest.raises(RuntimeError, match="MISSING outbox handlers: email"):
        OutboxDispatcher(
            cast(DispatcherConnection, object()),
            {},
            random_fraction=lambda: 0.0,
        )


def test_outbox_write_guards_fail_closed_before_database_access(
    outbox_database: psycopg.Connection[tuple[object, ...]],
) -> None:
    store = OutboxStore(cast(OutboxConnection, outbox_database), Scope(uuid4()))
    with pytest.raises(RuntimeError, match="correlation"):
        store.add("email", {}, idempotency_key="key")
    with correlation_context("guard-request"):
        with pytest.raises(ValueError, match="topic"):
            store.add("", {}, idempotency_key="key")
        with pytest.raises(ValueError, match="idempotency"):
            store.add("email", {}, idempotency_key="")
        with pytest.raises(ValueError, match="between 1 and 5"):
            store.add("email", {}, idempotency_key="key", max_attempts=0)


def test_resend_adapter_passes_idempotency_and_returns_message_id() -> None:
    captured: dict[str, object] = {}

    def request(
        url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> tuple[int, bytes]:
        captured.update(url=url, headers=headers, body=json.loads(body), timeout=timeout)
        return 200, b'{"id":"resend-message-7"}'

    sender = ResendEmailSender(
        "provider-secret",
        "noreply@example.invalid",
        timeout_seconds=3.0,
        requester=request,
    )

    assert sender.send(
        "buyer@example.invalid", "template-v2", {"name": "Buyer"}, "effect-key-7"
    ) == "resend-message-7"
    headers = cast(Mapping[str, str], captured["headers"])
    assert headers["Idempotency-Key"] == "effect-key-7"
    assert headers["Authorization"] == "Bearer provider-secret"
    assert captured["body"] == {
        "from": "noreply@example.invalid",
        "to": ["buyer@example.invalid"],
        "template": {"id": "template-v2", "variables": {"name": "Buyer"}},
    }


@pytest.mark.parametrize(
    ("status", "body", "error"),
    (
        (429, b"{}", TransientEmailError),
        (503, b"{}", TransientEmailError),
        (
            409,
            b'{"name":"concurrent_idempotent_requests"}',
            TransientEmailError,
        ),
        (409, b'{"name":"invalid_idempotent_request"}', PermanentEmailError),
        (422, b"{}", PermanentEmailError),
    ),
)
def test_resend_classifies_http_failures(
    status: int, body: bytes, error: type[RuntimeError]
) -> None:
    sender = ResendEmailSender(
        "provider-secret",
        "noreply@example.invalid",
        requester=lambda *_: (status, body),
    )
    with pytest.raises(error):
        sender.send("buyer@example.invalid", "template", {}, "key")


def test_resend_invalid_success_document_is_transient_and_secret_safe() -> None:
    secret = "provider-secret-not-for-errors"
    sender = ResendEmailSender(
        secret,
        "noreply@example.invalid",
        requester=lambda *_: (200, b"{}"),
    )
    with pytest.raises(TransientEmailError) as failure:
        sender.send("buyer@example.invalid", "template", {}, "key")
    assert secret not in str(failure.value)


class FakeSmtp:
    def __init__(self, refused: Mapping[str, object] | None = None) -> None:
        self.refused = refused or {}
        self.message: EmailMessage | None = None

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def send_message(self, message: EmailMessage) -> Mapping[str, object]:
        self.message = message
        return self.refused


def test_smtp_adapter_passes_a_deterministic_provider_key_header() -> None:
    smtp = FakeSmtp()
    sender = SmtpEmailSender(
        "localhost",
        1025,
        "noreply@xlr8flo.local",
        smtp_factory=lambda *_: cast(smtplib.SMTP, smtp),
    )
    first = sender.send("buyer@example.invalid", "template-v1", {"id": 42}, "same-key")
    second = sender.send("buyer@example.invalid", "template-v1", {"id": 42}, "same-key")

    assert first == second
    assert smtp.message is not None
    assert smtp.message["Resend-Idempotency-Key"] == "same-key"
    assert smtp.message["Message-ID"] == f"<{first}@xlr8flo.local>"


def test_email_provider_is_selected_only_by_configuration() -> None:
    smtp = create_email_sender(Settings(email_provider="smtp"))
    resend = create_email_sender(
        Settings(email_provider="resend", resend_api_key="configured-provider-secret")
    )
    assert isinstance(smtp, SmtpEmailSender)
    assert isinstance(resend, ResendEmailSender)
    assert isinstance(
        create_resend_sender(Settings(resend_api_key="configured-provider-secret")),
        ResendEmailSender,
    )
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        create_resend_sender(Settings())
