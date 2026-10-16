"""Vendor performance: append-only scores, disputes and adjustments (VEN-007, VEN-015)."""

import base64
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.service import CORE_SCORE_CATEGORIES, MasterCodeUnusable, OrgService, today
from flo.modules.vendors.models import VendorRepository
from flo.modules.vendors.schemas import (
    CategoryAverage,
    DisputeCreate,
    DisputeRead,
    DisputeResolve,
    PerformanceRead,
    ScoreCreate,
    ScorePage,
    ScoreRead,
    VendorRead,
    conflict,
    invalid,
)

KIND = "vendor_score_category"
SCORE_COLUMNS = (
    "s.id, s.category_code, s.score, s.period, s.source_type, s.source_ref, s.rationale, "
    "i.email, s.scored_at, s.supersedes, "
    "(SELECT n.id FROM vendor_score n WHERE n.org_id = s.org_id AND n.supersedes = s.id)"
)
SCORE_FIELDS = (
    "id",
    "category_code",
    "score",
    "period",
    "source_type",
    "source_ref",
    "rationale",
    "scored_by_email",
    "scored_at",
    "supersedes",
    "superseded_by",
)
DISPUTE_COLUMNS = (
    "d.id, d.score_id, r.email, d.raised_at, d.reason, d.resolution, rb.email, d.resolved_at, "
    "d.resolution_note"
)
DISPUTE_FIELDS = (
    "id",
    "score_id",
    "raised_by_email",
    "raised_at",
    "reason",
    "resolution",
    "resolved_by_email",
    "resolved_at",
    "resolution_note",
)
DISPUTE_FROM = (
    "FROM vendor_score_dispute d JOIN identity r ON r.id = d.raised_by "
    "LEFT JOIN identity rb ON rb.id = d.resolved_by"
)
# A row is current when no other row supersedes it.
CURRENT = (
    "NOT EXISTS (SELECT 1 FROM vendor_score n WHERE n.org_id = s.org_id AND n.supersedes = s.id)"
)
ONE_PLACE = Decimal("0.1")


def _cursor(period: date, id: UUID) -> str:
    return base64.urlsafe_b64encode(json.dumps([period.isoformat(), str(id)]).encode()).decode()


def _decode(cursor: str) -> tuple[date, UUID]:
    try:
        period, id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return date.fromisoformat(period), UUID(id)
    except (ValueError, TypeError):
        raise invalid("cursor", "This page link is not valid. Reload the list.") from None


class PerformanceMixin:
    connection: psycopg.Connection[tuple[object, ...]]
    scope: Scope
    actor_id: UUID | None
    repo: VendorRepository

    if TYPE_CHECKING:

        def _row(self, id: UUID) -> VendorRead: ...

        def _audit(
            self,
            action: str,
            id: UUID,
            before: dict[str, object] | None,
            after: dict[str, object],
            reason: str | None = None,
            target_type: str = "vendor",
        ) -> None: ...

    def _lock_vendor(self, vendor_id: UUID) -> None:
        """Serializes score writes for one vendor; also the 404 for a foreign or unknown id."""
        self._row(vendor_id)
        self.repo.execute(
            "SELECT 1 FROM vendor WHERE org_id = %(org_id)s AND id = %(id)s FOR UPDATE",
            {"id": vendor_id},
        ).fetchone()

    def _seed_categories(self) -> None:
        # ponytail: seeded on the first score write; a category list read before any score shows
        # only what the org created. Seed at org bootstrap if that matters.
        for code in CORE_SCORE_CATEGORIES:
            self.repo.execute(
                "INSERT INTO master_record (id, org_id, kind, code, name, attributes, "
                "effective_from, active) VALUES (%(id)s, %(org_id)s, %(kind)s, %(code)s, "
                "%(name)s, '{}'::jsonb, DATE '2000-01-01', true) "
                "ON CONFLICT (org_id, kind, code) DO NOTHING",
                {"id": uuid4(), "kind": KIND, "code": code, "name": code.capitalize()},
            )

    def _category(self, value: str) -> str:
        try:
            return OrgService(self.connection, self.scope).assert_usable(KIND, value, today()).code
        except MasterCodeUnusable:
            raise invalid(
                "category", "Choose quality, pricing, delivery, service or a defined category."
            ) from None

    def _score(self, vendor_id: UUID, id: UUID) -> ScoreRead:
        row = self.repo.execute(
            f"SELECT {SCORE_COLUMNS} FROM vendor_score s JOIN identity i ON i.id = s.scored_by "
            "WHERE s.org_id = %(org_id)s AND s.vendor_id = %(vendor_id)s AND s.id = %(id)s",
            {"vendor_id": vendor_id, "id": id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return self._scores([row])[0]

    def _scores(self, rows: list[tuple[object, ...]]) -> list[ScoreRead]:
        ids = [r[0] for r in rows]
        disputes = self.repo.execute(
            f"SELECT {DISPUTE_COLUMNS} {DISPUTE_FROM} WHERE d.org_id = %(org_id)s "
            "AND d.score_id = ANY(%(ids)s) ORDER BY d.raised_at, d.id",
            {"ids": ids},
        ).fetchall()
        by_score: dict[UUID, list[DisputeRead]] = {}
        for d in disputes:
            read = DisputeRead.model_validate(dict(zip(DISPUTE_FIELDS, d, strict=True)))
            by_score.setdefault(read.score_id, []).append(read)
        return [
            ScoreRead.model_validate(
                dict(zip(SCORE_FIELDS, r, strict=True))
                | {"disputes": by_score.get(cast(UUID, r[0]), [])}
            )
            for r in rows
        ]

    def _insert_score(
        self,
        vendor_id: UUID,
        category: str,
        score: int,
        period: date,
        source_type: str,
        source_ref: str,
        rationale: str,
        supersedes: UUID | None = None,
    ) -> UUID:
        id = uuid4()
        self.repo.execute(
            "INSERT INTO vendor_score (id, org_id, vendor_id, category_code, score, period, "
            "source_type, source_ref, rationale, scored_by, supersedes) VALUES (%(id)s, "
            "%(org_id)s, %(vendor_id)s, %(category)s, %(score)s, %(period)s, %(source_type)s, "
            "%(source_ref)s, %(rationale)s, %(actor)s, %(supersedes)s)",
            {
                "id": id,
                "vendor_id": vendor_id,
                "category": category,
                "score": score,
                "period": period,
                "source_type": source_type,
                "source_ref": source_ref,
                "rationale": rationale,
                "actor": self.actor_id,
                "supersedes": supersedes,
            },
        )
        return id

    def _score_audit(self, action: str, id: UUID, vendor_id: UUID, **extra: object) -> None:
        keep = self._score(vendor_id, id).model_dump(
            mode="json", include={"category_code", "score", "period", "source_ref", "supersedes"}
        )
        self._audit(action, id, None, keep | extra, target_type="vendor_score")

    def add_score(self, vendor_id: UUID, body: ScoreCreate) -> ScoreRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._lock_vendor(vendor_id)
            self._seed_categories()
            category = self._category(body.category)
            period = body.period.replace(day=1)
            duplicate = self.repo.execute(
                f"SELECT 1 FROM vendor_score s WHERE s.org_id = %(org_id)s "
                "AND s.vendor_id = %(vendor_id)s AND s.category_code = %(category)s "
                f"AND s.period = %(period)s AND s.source_ref = %(source_ref)s AND {CURRENT}",
                {
                    "vendor_id": vendor_id,
                    "category": category,
                    "period": period,
                    "source_ref": body.source_ref,
                },
            ).fetchone()
            if duplicate is not None:
                raise conflict(
                    "already_scored",
                    "This order is already scored for that category and period. "
                    "Dispute the score to change it.",
                )
            id = self._insert_score(
                vendor_id,
                category,
                body.score,
                period,
                body.source_type,
                body.source_ref,
                body.rationale,
            )
            self._score_audit("vendor.score.add", id, vendor_id)
            return self._score(vendor_id, id)

    def list_scores(
        self,
        vendor_id: UUID,
        category: str | None,
        start: date | None,
        end: date | None,
        cursor: str | None,
        limit: int = 50,
    ) -> ScorePage:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            after = _decode(cursor) if cursor else None
            rows = self.repo.execute(
                f"SELECT {SCORE_COLUMNS} FROM vendor_score s JOIN identity i ON i.id = s.scored_by "
                "WHERE s.org_id = %(org_id)s AND s.vendor_id = %(vendor_id)s "
                "AND (%(category)s::text IS NULL OR s.category_code = %(category)s) "
                "AND (%(start)s::date IS NULL OR s.period >= %(start)s) "
                "AND (%(end)s::date IS NULL OR s.period <= %(end)s) "
                "AND (%(after_period)s::date IS NULL "
                "OR (s.period, s.id) < (%(after_period)s, %(after_id)s)) "
                "ORDER BY s.period DESC, s.id DESC LIMIT %(limit)s",
                {
                    "vendor_id": vendor_id,
                    "category": category.strip().upper() if category else None,
                    "start": start,
                    "end": end,
                    "after_period": after[0] if after else None,
                    "after_id": after[1] if after else None,
                    "limit": limit + 1,
                },
            ).fetchall()
            page = self._scores(rows[:limit])
            more = len(rows) > limit
            return ScorePage(
                rows=page, next_cursor=_cursor(page[-1].period, page[-1].id) if more else None
            )

    def performance(self, vendor_id: UUID, start: date | None, end: date | None) -> PerformanceRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(vendor_id)
            rows = self.repo.execute(
                "SELECT s.category_code, sum(s.score), count(*) FROM vendor_score s "
                "WHERE s.org_id = %(org_id)s AND s.vendor_id = %(vendor_id)s "
                "AND (%(start)s::date IS NULL OR s.period >= %(start)s) "
                "AND (%(end)s::date IS NULL OR s.period <= %(end)s) "
                f"AND {CURRENT} GROUP BY s.category_code ORDER BY s.category_code",
                {"vendor_id": vendor_id, "start": start, "end": end},
            ).fetchall()
        # Overall is the mean of the unrounded category averages, rounded once.
        exact = [(str(r[0]), Decimal(str(r[1])) / Decimal(str(r[2])), int(str(r[2]))) for r in rows]
        return PerformanceRead(
            categories=[
                CategoryAverage(
                    category_code=code,
                    average=str(mean.quantize(ONE_PLACE, ROUND_HALF_UP)),
                    count=count,
                )
                for code, mean, count in exact
            ],
            overall=(
                str(
                    (sum((m for _, m, _ in exact), Decimal(0)) / len(exact)).quantize(
                        ONE_PLACE, ROUND_HALF_UP
                    )
                )
                if exact
                else None
            ),
            count=sum(c for _, _, c in exact),
        )

    def _dispute(self, id: UUID, lock: bool = False) -> tuple[object, ...]:
        row = self.repo.execute(
            f"SELECT {DISPUTE_COLUMNS}, d.raised_by {DISPUTE_FROM} "
            "WHERE d.org_id = %(org_id)s AND d.id = %(id)s" + (" FOR UPDATE OF d" if lock else ""),
            {"id": id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return row

    def dispute_score(self, vendor_id: UUID, score_id: UUID, body: DisputeCreate) -> DisputeRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._lock_vendor(vendor_id)
            score = self._score(vendor_id, score_id)
            if score.superseded_by is not None:
                raise conflict(
                    "score_superseded",
                    "This score was already adjusted. Dispute the adjusted score instead.",
                )
            open_dispute = self.repo.execute(
                "SELECT 1 FROM vendor_score_dispute WHERE org_id = %(org_id)s "
                "AND score_id = %(score_id)s AND resolution IS NULL",
                {"score_id": score_id},
            ).fetchone()
            if open_dispute is not None:
                raise conflict(
                    "dispute_open", "This score already has an open dispute. Wait for its decision."
                )
            id = uuid4()
            self.repo.execute(
                "INSERT INTO vendor_score_dispute (id, org_id, score_id, raised_by, reason) "
                "VALUES (%(id)s, %(org_id)s, %(score_id)s, %(actor)s, %(reason)s)",
                {"id": id, "score_id": score_id, "actor": self.actor_id, "reason": body.reason},
            )
            self._audit(
                "vendor.score.dispute",
                score_id,
                None,
                {"dispute_id": str(id), "reason": body.reason},
                target_type="vendor_score",
            )
            return DisputeRead.model_validate(
                dict(zip(DISPUTE_FIELDS, self._dispute(id)[:-1], strict=True))
            )

    def resolve_dispute(
        self, vendor_id: UUID, score_id: UUID, dispute_id: UUID, body: DisputeResolve
    ) -> DisputeRead:
        upheld = body.resolution == "upheld"
        if upheld and (body.adjusted_score is None or body.adjusted_rationale is None):
            raise invalid(
                "adjusted_score" if body.adjusted_score is None else "adjusted_rationale",
                "An upheld dispute needs the adjusted score and its rationale.",
                "adjustment_required",
            )
        if not upheld and (body.adjusted_score is not None or body.adjusted_rationale is not None):
            raise invalid(
                "adjusted_score",
                "A dismissed dispute changes nothing. Remove the adjusted values.",
                "adjustment_not_allowed",
            )
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._lock_vendor(vendor_id)
            original = self._score(vendor_id, score_id)
            dispute = self._dispute(dispute_id, lock=True)
            if dispute[1] != score_id:
                raise ProblemError(ErrorCode.NOT_FOUND)
            if dispute[5] is not None:
                raise conflict("already_resolved", "This dispute was already decided.")
            if dispute[-1] == self.actor_id:
                raise conflict(
                    "separation_of_duty", "You raised this dispute, so someone else must decide it."
                )
            self.repo.execute(
                "UPDATE vendor_score_dispute SET resolution = %(resolution)s, "
                "resolved_by = %(actor)s, resolved_at = now(), resolution_note = %(note)s "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {
                    "id": dispute_id,
                    "resolution": body.resolution,
                    "actor": self.actor_id,
                    "note": body.note,
                },
            )
            extra: dict[str, object] = {
                "dispute_id": str(dispute_id),
                "resolution": body.resolution,
            }
            if upheld:
                assert body.adjusted_score is not None and body.adjusted_rationale is not None
                adjustment = self._insert_score(
                    vendor_id,
                    original.category_code,
                    body.adjusted_score,
                    original.period,
                    original.source_type,
                    original.source_ref,
                    body.adjusted_rationale,
                    supersedes=score_id,
                )
                extra["adjustment_id"] = str(adjustment)
                self._score_audit("vendor.score.add", adjustment, vendor_id)
            self._audit("vendor.score.resolve", score_id, None, extra, target_type="vendor_score")
            return DisputeRead.model_validate(
                dict(zip(DISPUTE_FIELDS, self._dispute(dispute_id)[:-1], strict=True))
            )
