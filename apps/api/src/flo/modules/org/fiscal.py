"""Fiscal dates and transactional period controls for the organization."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel, ConfigDict, Field, field_validator

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.models import OrgRepository


class CalendarPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_month: int = Field(strict=True, ge=1, le=12, description="Fiscal first month (1–12).")


class ReopenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, description="Explain why this period must be reopened.")

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise ValueError("Provide a reason with at least 10 non-padding characters.")
        return value


class FiscalPeriod(BaseModel):
    id: UUID
    fiscal_year: int
    period_no: int
    starts_on: date
    ends_on: date
    status: Literal["open", "closed"]
    closed_by: UUID | None
    closed_at: datetime | None


class PeriodClosed(ProblemError):
    def __init__(self, period: FiscalPeriod) -> None:
        self.period = period
        super().__init__(
            ErrorCode.CONFLICT,
            detail="The financial period is closed. Reopen it before posting to this date.",
            checks={"problem": "period_closed", "period_id": str(period.id)},
        )


def year_bounds(fiscal_year: int, start_month: int) -> tuple[date, date]:
    start = date(fiscal_year if start_month == 1 else fiscal_year - 1, start_month, 1)
    end = (
        date(fiscal_year, 12, 31)
        if start_month == 1
        else date(fiscal_year, start_month, 1) - timedelta(days=1)
    )
    return start, end


class FiscalService:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        actor_id: UUID | None = None,
    ) -> None:
        self.connection = connection
        self.scope = scope
        self.actor_id = actor_id
        self.repo = OrgRepository(connection, scope)

    def _audit(
        self,
        action: str,
        target_id: UUID,
        before: dict[str, object] | None,
        after: dict[str, object] | None,
    ) -> None:
        AuditWriter(cast(AuditConnection, self.connection), self.scope).write(
            actor=AuditActor(ActorKind.USER, self.actor_id)
            if self.actor_id is not None
            else AuditActor(ActorKind.SYSTEM),
            action=action,
            target_type=action.split(".")[0],
            target_id=target_id,
            outcome=Outcome.SUCCESS,
            reason=cast(str | None, after.get("reason")) if after else None,
            before_source=before,
            before_fields=tuple(before or ()),
            after_source=after,
            after_fields=tuple(after or ()),
        )

    def start_month(self) -> int:
        row = self.repo.execute(
            "SELECT start_month FROM fiscal_calendar WHERE org_id = %(org_id)s"
        ).fetchone()
        return cast(int, row[0]) if row else 1

    def set_calendar(self, body: CalendarPut) -> CalendarPut:
        """Configure before generation; generated history locks the calendar permanently."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.repo.lock_organization()
            if self.repo.execute(
                "SELECT id FROM fiscal_period WHERE org_id = %(org_id)s LIMIT 1"
            ).fetchone():
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="Periods already exist. Keep the calendar used for this history.",
                    checks={"problem": "calendar_locked"},
                )
            before = self.repo.execute(
                "SELECT start_month FROM fiscal_calendar WHERE org_id = %(org_id)s"
            ).fetchone()
            changed = self.repo.execute(
                """INSERT INTO fiscal_calendar(org_id, start_month)
                VALUES (%(org_id)s, %(month)s) ON CONFLICT (org_id) DO UPDATE
                SET start_month = EXCLUDED.start_month, changed_at = now()
                WHERE fiscal_calendar.start_month IS DISTINCT FROM EXCLUDED.start_month
                RETURNING start_month""",
                {"month": body.start_month},
            ).fetchone()
            if changed:
                self._audit(
                    "fiscal_calendar.set",
                    self.scope.org_id,
                    {"start_month": before[0]} if before else None,
                    body.model_dump(),
                )
            return body

    def list_periods(self, fiscal_year: int) -> list[FiscalPeriod]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rows = self.repo.execute(
                """SELECT id, fiscal_year, period_no, starts_on, ends_on, status, closed_by,
                closed_at FROM fiscal_period WHERE org_id = %(org_id)s
                AND fiscal_year = %(year)s ORDER BY period_no""",
                {"year": fiscal_year},
            ).fetchall()
            return [self._read(row) for row in rows]

    @staticmethod
    def _read(row: tuple[object, ...]) -> FiscalPeriod:
        return FiscalPeriod.model_validate(
            dict(
                zip(
                    (
                        "id",
                        "fiscal_year",
                        "period_no",
                        "starts_on",
                        "ends_on",
                        "status",
                        "closed_by",
                        "closed_at",
                    ),
                    row,
                    strict=True,
                )
            )
        )

    def get_period(self, period_id: UUID) -> FiscalPeriod:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                """SELECT id, fiscal_year, period_no, starts_on, ends_on, status, closed_by,
                closed_at FROM fiscal_period WHERE org_id = %(org_id)s AND id = %(id)s""",
                {"id": period_id},
            ).fetchone()
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            return self._read(row)

    def ensure_year(self, fiscal_year: int) -> list[FiscalPeriod]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            # Also serializes configuration against generation when no calendar row exists.
            self.repo.lock_organization()
            start, _ = year_bounds(fiscal_year, self.start_month())
            created = False
            for index in range(12):
                offset = start.month - 1 + index
                first = date(start.year + offset // 12, offset % 12 + 1, 1)
                last = first.replace(day=monthrange(first.year, first.month)[1])
                inserted = self.repo.execute(
                    """INSERT INTO fiscal_period
                    (id, org_id, fiscal_year, period_no, starts_on, ends_on)
                    VALUES (%(id)s, %(org_id)s, %(year)s, %(no)s, %(start)s, %(end)s)
                    ON CONFLICT (org_id, fiscal_year, period_no) DO NOTHING RETURNING id""",
                    {
                        "id": uuid4(),
                        "year": fiscal_year,
                        "no": index + 1,
                        "start": first,
                        "end": last,
                    },
                ).fetchone()
                created = created or inserted is not None
            if created:
                self._audit(
                    "fiscal_year.generate", self.scope.org_id, None, {"fiscal_year": fiscal_year}
                )
            return self.list_periods(fiscal_year)

    def transition(
        self,
        period_id: UUID,
        action: Literal["close", "reopen"],
        reason: str | None = None,
    ) -> FiscalPeriod:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            # Acquire period locks in date order, so ordering checks and transitions serialize.
            # Only period rows are locked; posters hold FOR SHARE until their outer commit.
            rows = self.repo.execute(
                """SELECT id, fiscal_year, period_no, starts_on, ends_on, status, closed_by,
                closed_at FROM fiscal_period WHERE org_id = %(org_id)s
                ORDER BY starts_on FOR UPDATE""",
            ).fetchall()
            periods = [self._read(row) for row in rows]
            before = next((p for p in periods if p.id == period_id), None)
            if before is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            closing = action == "close"
            if before.status == ("closed" if closing else "open"):
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="The period already has this status. Refresh the period list.",
                    checks={"problem": "already_closed" if closing else "already_open"},
                )
            if not closing:
                reason = ReopenBody(reason=reason or "").reason
            previous = next(
                (p for p in periods if p.ends_on == before.starts_on - timedelta(days=1)), None
            )
            if (closing and previous is not None and previous.status != "closed") or (
                not closing
                and any(p.starts_on > before.starts_on and p.status == "closed" for p in periods)
            ):
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="Close the preceding period first."
                    if closing
                    else "Reopen all later closed periods first.",
                    checks={"problem": "period_order"},
                )
            self.repo.execute(
                """SELECT set_config('app.fiscal_actor', %(actor)s, true),
                set_config('app.fiscal_reason', %(reason)s, true)""",
                {"actor": str(self.actor_id) if self.actor_id else "", "reason": reason or ""},
            )
            self.repo.execute(
                """UPDATE fiscal_period SET status = %(status)s, closed_by = %(actor)s,
                closed_at = CASE WHEN %(status)s = 'closed' THEN now() ELSE NULL END
                WHERE org_id = %(org_id)s AND id = %(id)s""",
                {
                    "status": "closed" if closing else "open",
                    "id": period_id,
                    "actor": self.actor_id if closing else None,
                },
            )
            after = self.get_period(period_id)
            self._audit(
                "fiscal_period." + action,
                period_id,
                before.model_dump(),
                after.model_dump() | {"reason": reason},
            )
            return after

    def period_for(self, effective_date: date, *, share: bool = False) -> FiscalPeriod:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):

            def find() -> tuple[object, ...] | None:
                return self.repo.execute(
                    """SELECT id, fiscal_year, period_no, starts_on, ends_on, status, closed_by,
                    closed_at FROM fiscal_period WHERE org_id = %(org_id)s
                    AND starts_on <= %(date)s AND ends_on >= %(date)s"""
                    + (" FOR SHARE" if share else ""),
                    {"date": effective_date},
                ).fetchone()

            row = find()
            if row is None:
                self.repo.lock_organization()
                month = self.start_month()
                year = effective_date.year + (month != 1 and effective_date.month >= month)
                self.ensure_year(year)
                row = find()
            assert row is not None
            return self._read(row)

    def assert_postable(self, effective_date: date) -> None:
        """Caller must own the posting transaction (balance lock before this period lock)."""
        if self.connection.info.transaction_status.name == "IDLE":
            raise RuntimeError("assert_postable requires an active caller transaction")
        period = self.period_for(effective_date, share=True)
        if period.status == "closed":
            raise PeriodClosed(period)

    def range_for(
        self,
        kind: Literal["mtd", "qtd", "ytd", "fiscal_year"],
        as_of: date,
    ) -> tuple[date, date]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            month = self.start_month()
            year = as_of.year + (month != 1 and as_of.month >= month)
            start, end = year_bounds(year, month)
            if kind == "mtd":
                return as_of.replace(day=1), as_of
            if kind == "qtd":
                offset = (as_of.month - month) % 12
                quarter_offset = offset // 3 * 3
                absolute = start.month - 1 + quarter_offset
                return date(start.year + absolute // 12, absolute % 12 + 1, 1), as_of
            return start, end if kind == "fiscal_year" else as_of
