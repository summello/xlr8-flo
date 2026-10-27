"""Evaluation: criteria and weights, conflict declaration first, sealed scores (SRC-011, SRC-013).

Nothing here changes a bid. A score is a whole number 0..10 per evaluator, vendor and criterion;
the weighted score per vendor is Σ(mean over non-recused evaluators × weight / 100), quantized once.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError, conflict
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.service import OrgService
from flo.modules.sourcing.models import RfqRepository
from flo.modules.sourcing.sealing import visible
from flo.modules.sourcing.service import RfqService

TERMINAL = ("awarded", "cancelled")  # sourcing.md:54
EVALUABLE = ("draft", "published", "closed", "under_evaluation")
CENT = Decimal("0.01")
DEFAULT_KEY = "sourcing.default_criteria"


# -- contracts -----------------------------------------------------------------------------


class CriterionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    weight: int = Field(ge=1, le=100)


class CriteriaPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: list[CriterionIn] = Field(min_length=1, max_length=20)


class CriterionRead(BaseModel):
    id: UUID | None
    position: int
    name: str
    weight: int


class CriteriaRead(BaseModel):
    criteria: list[CriterionRead]
    source: Literal["default", "rfq"]
    edit_revision: int


class EvaluatorAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_id: UUID


class EligibleEvaluator(BaseModel):
    identity_id: UUID
    display_name: str
    email: str


class EligibleEvaluatorPage(BaseModel):
    rows: list[EligibleEvaluator]
    has_more: bool


class DeclarationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    conflict: bool
    detail: str | None = Field(default=None, max_length=2000)


class ScoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    vendor_id: UUID
    criterion_id: UUID
    score: int = Field(ge=0, le=10)
    comment: str | None = Field(default=None, max_length=2000)


class ScoresPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cells: list[ScoreIn] = Field(min_length=1, max_length=2000)


class EvaluatorRead(BaseModel):
    identity_id: UUID
    display_name: str
    version: int
    declaration: Literal["none", "clear", "conflict"]
    detail: str | None
    declared_at: datetime | None
    submitted: bool


class MeRead(BaseModel):
    identity_id: UUID
    version: int
    declaration: Literal["none", "clear"]
    submitted: bool


class VendorRef(BaseModel):
    vendor_id: UUID
    vendor_name: str


class EvaluationScoreRead(BaseModel):
    identity_id: UUID
    vendor_id: UUID
    criterion_id: UUID
    score: int
    comment: str | None
    status: Literal["draft", "submitted"]


class WeightedRead(BaseModel):
    vendor_id: UUID
    score: str


class EvaluationRead(BaseModel):
    status: str
    scoring_open: bool
    criteria: list[CriterionRead]
    source: Literal["default", "rfq"]
    edit_revision: int
    me: MeRead | None
    evaluators: list[EvaluatorRead] | None  # the editors' view; the declarations are theirs to see
    evaluator_count: int  # non-recused
    complete: bool
    vendors: list[VendorRef]
    scores: list[EvaluationScoreRead]
    weighted: list[WeightedRead] | None


class EvaluationScoreResult(BaseModel):
    version: int
    saved: int
    submitted: bool


# -- the tally -----------------------------------------------------------------------------


@dataclass
class Tally:
    criteria: list[tuple[UUID, int]]  # (id, weight)
    vendors: list[UUID]  # the current submitted bid per vendor, as the comparison reads them
    state: dict[UUID, bool | None]  # evaluator -> None undeclared, False clear, True recused
    cells: dict[tuple[UUID, UUID, UUID], tuple[int, str | None, str]]  # (who, vendor, criterion)

    @property
    def active(self) -> list[UUID]:
        return [i for i, s in self.state.items() if s is not True]

    def done(self, who: UUID) -> bool:
        return all(
            self.cells.get((who, v, c), (0, None, "draft"))[2] == "submitted"
            for v in self.vendors
            for c, _ in self.criteria
        )

    @property
    def complete(self) -> bool:
        # an empty set can never be submitted, so it never completes
        return bool(self.criteria and self.vendors and self.active) and all(
            self.done(i) for i in self.active
        )

    def weighted(self) -> dict[UUID, Decimal] | None:
        if not self.complete:
            return None
        n = len(self.active)
        return {
            v: sum(
                (
                    Decimal(sum(self.cells[(i, v, c)][0] for i in self.active) * w) / (n * 100)
                    for c, w in self.criteria
                ),
                Decimal(0),
            ).quantize(CENT, ROUND_HALF_UP)
            for v in self.vendors
        }


def _all(repo: RfqRepository, query: str, params: Mapping[str, object]) -> list[tuple[Any, ...]]:
    return cast("list[tuple[Any, ...]]", repo.execute(query, params).fetchall())


def tally(repo: RfqRepository, rfq_id: UUID) -> Tally:
    key = {"rfq": rfq_id}
    criteria = [
        (cast(UUID, r[0]), cast(int, r[1]))
        for r in _all(
            repo,
            "SELECT id, weight FROM rfq_criterion "
            "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s ORDER BY position",
            key,
        )
    ]
    vendors = [
        cast(UUID, r[0])
        for r in _all(
            repo,
            "SELECT b.vendor_id FROM bid b WHERE b.org_id = %(org_id)s AND b.rfq_id = %(rfq)s "
            "AND b.status = 'submitted' AND NOT EXISTS (SELECT 1 FROM bid r "
            "WHERE r.org_id = b.org_id AND r.replaces_bid_id = b.id AND r.status = 'submitted') "
            "ORDER BY b.sent_at, b.id",
            key,
        )
    ]
    # a conflict is permanent and always the later row, so bool_or is the latest state
    state = {
        cast(UUID, r[0]): cast("bool | None", r[1])
        for r in _all(
            repo,
            "SELECT e.identity_id, (SELECT bool_or(d.conflict) FROM rfq_evaluator_declaration d "
            "WHERE d.org_id = e.org_id AND d.rfq_id = e.rfq_id AND d.identity_id = e.identity_id) "
            "FROM rfq_evaluator e WHERE e.org_id = %(org_id)s AND e.rfq_id = %(rfq)s",
            key,
        )
    }
    cells = {
        (r[0], r[1], r[2]): (r[3], r[4], r[5])
        for r in _all(
            repo,
            "SELECT identity_id, vendor_id, criterion_id, score, comment, status FROM rfq_score "
            "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s",
            key,
        )
    }
    return Tally(criteria, vendors, state, cells)


def weighted(repo: RfqRepository, rfq_id: UUID) -> dict[UUID, Decimal] | None:
    """Per vendor, `None` until every non-recused evaluator has submitted every current cell."""
    return tally(repo, rfq_id).weighted()


def assert_may_see_bids(repo: RfqRepository, rfq_id: UUID, identity_id: UUID | None) -> None:
    """Bid content reaches an evaluator only after they declare no conflict. Callers run this
    after their own authorization and only once the bids are unsealed; it never widens access."""
    row = repo.execute(
        "SELECT (SELECT bool_or(d.conflict) FROM rfq_evaluator_declaration d "
        "WHERE d.org_id = e.org_id AND d.rfq_id = e.rfq_id AND d.identity_id = e.identity_id) "
        "FROM rfq_evaluator e WHERE e.org_id = %(org_id)s AND e.rfq_id = %(rfq)s "
        "AND e.identity_id = %(who)s",
        {"rfq": rfq_id, "who": identity_id},
    ).fetchone()
    if row is None:
        return
    if row[0] is None:
        raise conflict("declaration_required", "Declare whether you have a conflict first.")
    if row[0]:
        raise conflict("recused", "You declared a conflict and are recused from this RFQ.")


def _invalid(field: str, message: str, problem: str) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail=message,
        errors=(ProblemFieldError(field=field, message=message),),
        checks={"problem": problem},
    )


# -- the service ---------------------------------------------------------------------------


class EvaluationService(RfqService):
    def _head(self, rfq_id: UUID, *, lock: bool = False) -> tuple[Any, ...]:
        row = self.repo.execute(
            "SELECT status, deadline_at, now(), edit_revision, unit_id FROM rfq "
            "WHERE org_id = %(org_id)s AND id = %(id)s" + (" FOR UPDATE" if lock else ""),
            {"id": rfq_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return row

    def _criteria(self, rfq_id: UUID, head: tuple[Any, ...]) -> CriteriaRead:
        rows = _all(
            self.repo,
            "SELECT id, position, name, weight FROM rfq_criterion "
            "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s ORDER BY position",
            {"rfq": rfq_id},
        )
        if rows or head[0] != "draft":
            return CriteriaRead(
                criteria=[
                    CriterionRead(id=r[0], position=r[1], name=r[2], weight=r[3]) for r in rows
                ],
                source="rfq",
                edit_revision=head[3],
            )
        default = OrgService(self.connection, self.scope).effective_setting(head[4], DEFAULT_KEY)
        items = cast("list[dict[str, Any]]", default.value or [])
        return CriteriaRead(
            criteria=[
                CriterionRead(id=None, position=n, name=i["name"], weight=i["weight"])
                for n, i in enumerate(items, 1)
            ],
            source="default",
            edit_revision=head[3],
        )

    # -- reads -----------------------------------------------------------------------------

    def _evaluators(self, rfq_id: UUID, t: Tally) -> list[EvaluatorRead]:
        out = []
        for r in _all(
            self.repo,
            "SELECT e.identity_id, coalesce(i.display_name, i.email), e.version, "
            "(SELECT d.detail FROM rfq_evaluator_declaration d WHERE d.org_id = e.org_id "
            "AND d.rfq_id = e.rfq_id AND d.identity_id = e.identity_id "
            "ORDER BY d.conflict DESC, d.declared_at DESC LIMIT 1), "
            "(SELECT max(d.declared_at) FROM rfq_evaluator_declaration d WHERE d.org_id = e.org_id "
            "AND d.rfq_id = e.rfq_id AND d.identity_id = e.identity_id) "
            "FROM rfq_evaluator e JOIN identity i ON i.id = e.identity_id "
            "WHERE e.org_id = %(org_id)s AND e.rfq_id = %(rfq)s "
            "ORDER BY e.added_at, e.identity_id",
            {"rfq": rfq_id},
        ):
            state = t.state[r[0]]
            out.append(
                EvaluatorRead(
                    identity_id=r[0],
                    display_name=r[1],
                    version=r[2],
                    declaration="none" if state is None else "conflict" if state else "clear",
                    detail=r[3],
                    declared_at=r[4],
                    submitted=t.done(r[0]),
                )
            )
        return out

    def view(self, rfq_id: UUID, *, can_edit: bool, can_approve: bool) -> EvaluationRead:
        """The caller's view: sealed before the deadline, their own scores until everyone has
        submitted, then everyone's (to evaluators and `rfq.approve`)."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            head = self._head(rfq_id)
            t = tally(self.repo, rfq_id)
            me = self.actor_id if self.actor_id in t.state else None
            if me is not None and t.state[me] is True:
                raise conflict("recused", "You declared a conflict and are recused from this RFQ.")
            opened = visible(head[1], head[2])
            cleared = me is None or t.state[me] is False
            names: dict[UUID, str | None] = {}
            if opened and cleared and t.vendors:
                names = dict(
                    _all(
                        self.repo,
                        "SELECT vendor_id, vendor_snapshot->>'trading_name' FROM rfq_invitee "
                        "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s AND vendor_id = ANY(%(v)s)",
                        {"rfq": rfq_id, "v": t.vendors},
                    )
                )
            everyone = opened and t.complete and (can_approve or me is not None)
            if not opened or not cleared:
                shown = {}
            elif everyone:
                shown = {k: v for k, v in t.cells.items() if t.state[k[0]] is not True}
            else:
                shown = {k: v for k, v in t.cells.items() if k[0] == me}
            weights = t.weighted() if opened and cleared else None
            criteria = self._criteria(rfq_id, head)
            return EvaluationRead(
                status=head[0],
                scoring_open=opened and head[0] not in TERMINAL,
                criteria=criteria.criteria,
                source=criteria.source,
                edit_revision=head[3],
                me=None
                if me is None
                else MeRead(
                    identity_id=me,
                    version=self._version(rfq_id, me),
                    declaration="none" if t.state[me] is None else "clear",
                    submitted=t.done(me),
                ),
                evaluators=self._evaluators(rfq_id, t) if can_edit else None,
                evaluator_count=len(t.active),
                complete=t.complete,
                vendors=[VendorRef(vendor_id=v, vendor_name=n or "") for v, n in names.items()],
                scores=[
                    EvaluationScoreRead(
                        identity_id=k[0],
                        vendor_id=k[1],
                        criterion_id=k[2],
                        score=v[0],
                        comment=v[1],
                        status=cast("Literal['draft', 'submitted']", v[2]),
                    )
                    for k, v in sorted(shown.items(), key=lambda kv: tuple(map(str, kv[0])))
                ],
                weighted=None
                if weights is None
                else [WeightedRead(vendor_id=v, score=format(s, "f")) for v, s in weights.items()],
            )

    def _version(self, rfq_id: UUID, who: UUID) -> int:
        row = self.repo.execute(
            "SELECT version FROM rfq_evaluator "
            "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s AND identity_id = %(who)s",
            {"rfq": rfq_id, "who": who},
        ).fetchone()
        assert row is not None
        return cast(int, row[0])

    # -- criteria --------------------------------------------------------------------------

    def put_criteria(self, rfq_id: UUID, body: CriteriaPut, if_match: str | None) -> CriteriaRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            head = self._head(rfq_id, lock=True)
            self._check_edit_revision(head[3], if_match)
            if head[0] != "draft":
                raise conflict("not_draft", "Only a draft RFQ can change its criteria.")
            before = self._criteria(rfq_id, head)
            self.repo.execute(
                "DELETE FROM rfq_criterion WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s",
                {"rfq": rfq_id},
            )
            for n, c in enumerate(body.criteria, 1):
                self.repo.execute(
                    "INSERT INTO rfq_criterion (id, org_id, rfq_id, position, name, weight) "
                    "VALUES (%(id)s, %(org_id)s, %(rfq)s, %(n)s, %(name)s, %(weight)s)",
                    {"id": uuid4(), "rfq": rfq_id, "n": n, "name": c.name, "weight": c.weight},
                )
            self.repo.execute(
                "UPDATE rfq SET updated_at = now(), edit_revision = edit_revision + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": rfq_id},
            )
            after = self._criteria(rfq_id, (head[0], None, None, head[3] + 1, head[4]))
            self._audit(
                "rfq.criteria.update",
                rfq_id,
                {
                    "source": before.source,
                    "criteria": [
                        c.model_dump(mode="json", exclude={"id"}) for c in before.criteria
                    ],
                },
                {"criteria": [c.model_dump(mode="json", exclude={"id"}) for c in after.criteria]},
            )
            return after

    # -- evaluators ------------------------------------------------------------------------

    def add_evaluator(self, rfq_id: UUID, identity_id: UUID, *, may_view: bool) -> EvaluatorRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            head = self._head(rfq_id, lock=True)
            if head[0] in TERMINAL:
                raise conflict("rfq_terminal", "This RFQ is closed to evaluation changes.")
            active = self.repo.execute(
                "SELECT 1 FROM identity WHERE id = %(who)s AND status = 'active'",
                {"who": identity_id},
            ).fetchone()
            if not may_view or active is None:
                raise _invalid(
                    "identity_id", "An evaluator needs to be able to view this RFQ.", "not_eligible"
                )
            added = self.repo.execute(
                "INSERT INTO rfq_evaluator (org_id, rfq_id, identity_id, added_by) "
                "VALUES (%(org_id)s, %(rfq)s, %(who)s, %(actor)s) ON CONFLICT DO NOTHING "
                "RETURNING 1",
                {"rfq": rfq_id, "who": identity_id, "actor": self.actor_id},
            ).fetchone()
            if added is None:
                raise conflict("already_evaluator", "This person is already an evaluator.")
            self._audit("rfq.evaluator.add", rfq_id, None, {"identity_id": str(identity_id)})
            t = tally(self.repo, rfq_id)
            return next(e for e in self._evaluators(rfq_id, t) if e.identity_id == identity_id)

    def eligible_evaluators(
        self, rfq_id: UUID, holders: list[UUID], q: str, limit: int, offset: int
    ) -> EligibleEvaluatorPage:
        """Active `rfq.view` holders (the router resolves them) not yet on the RFQ, by name."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._head(rfq_id)
            rows = _all(
                self.repo,
                "SELECT i.id, coalesce(i.display_name, i.email), i.email FROM identity i "
                "WHERE i.status = 'active' AND i.id = ANY(%(ids)s) "
                "AND NOT EXISTS (SELECT 1 FROM rfq_evaluator e WHERE e.org_id = %(org_id)s "
                "AND e.rfq_id = %(rfq)s AND e.identity_id = i.id) "
                "AND (strpos(lower(coalesce(i.display_name, '')), lower(%(q)s)) > 0 "
                "OR strpos(lower(i.email), lower(%(q)s)) > 0) "
                "ORDER BY 2, i.id LIMIT %(limit)s OFFSET %(offset)s",
                {"rfq": rfq_id, "ids": holders, "q": q, "limit": limit + 1, "offset": offset},
            )
        return EligibleEvaluatorPage(
            rows=[
                EligibleEvaluator(identity_id=r[0], display_name=r[1], email=r[2])
                for r in rows[:limit]
            ],
            has_more=len(rows) > limit,
        )

    def remove_evaluator(self, rfq_id: UUID, identity_id: UUID, if_match: str | None) -> None:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            head = self._head(rfq_id, lock=True)
            if head[0] in TERMINAL:
                raise conflict("rfq_terminal", "This RFQ is closed to evaluation changes.")
            row = self.repo.execute(
                "SELECT version FROM rfq_evaluator WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                "AND identity_id = %(who)s FOR UPDATE",
                {"rfq": rfq_id, "who": identity_id},
            ).fetchone()
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            self._check_edit_revision(cast(int, row[0]), if_match)
            counts = self.repo.execute(
                "SELECT count(*) FILTER (WHERE status = 'submitted'), count(*) FROM rfq_score "
                "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s AND identity_id = %(who)s",
                {"rfq": rfq_id, "who": identity_id},
            ).fetchone()
            assert counts is not None
            if counts[0]:
                raise conflict(
                    "evaluator_has_scores",
                    "This evaluator has submitted scores and cannot be removed.",
                )
            self.repo.execute(
                "DELETE FROM rfq_score WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                "AND identity_id = %(who)s",
                {"rfq": rfq_id, "who": identity_id},
            )
            self.repo.execute(
                "DELETE FROM rfq_evaluator WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                "AND identity_id = %(who)s",
                {"rfq": rfq_id, "who": identity_id},
            )
            self._audit(
                "rfq.evaluator.remove",
                rfq_id,
                {"identity_id": str(identity_id)},
                {"drafts_dropped": counts[1]},
            )

    # -- declaration -----------------------------------------------------------------------

    def declare(self, rfq_id: UUID, body: DeclarationIn) -> EvaluatorRead:
        if body.conflict and not body.detail:
            raise _invalid("detail", "Describe the conflict.", "detail_required")
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._head(rfq_id)
            row = self.repo.execute(
                "SELECT 1 FROM rfq_evaluator WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                "AND identity_id = %(who)s FOR UPDATE",
                {"rfq": rfq_id, "who": self.actor_id},
            ).fetchone()
            if row is None:
                raise ProblemError(ErrorCode.FORBIDDEN)
            earlier = [
                r[0]
                for r in _all(
                    self.repo,
                    "SELECT conflict FROM rfq_evaluator_declaration WHERE org_id = %(org_id)s "
                    "AND rfq_id = %(rfq)s AND identity_id = %(who)s",
                    {"rfq": rfq_id, "who": self.actor_id},
                )
            ]
            # none yet, or one clear declaration followed once by a conflict; nothing else
            if earlier and not (earlier == [False] and body.conflict):
                raise conflict("already_declared", "You have already made this declaration.")
            self.repo.execute(
                "INSERT INTO rfq_evaluator_declaration "
                "(org_id, rfq_id, identity_id, conflict, detail) "
                "VALUES (%(org_id)s, %(rfq)s, %(who)s, %(conflict)s, %(detail)s)",
                {
                    "rfq": rfq_id,
                    "who": self.actor_id,
                    "conflict": body.conflict,
                    "detail": body.detail,
                },
            )
            self._audit(
                "rfq.evaluator.declare",
                rfq_id,
                None,
                {"identity_id": str(self.actor_id), "conflict": body.conflict},
            )
            t = tally(self.repo, rfq_id)
            return next(e for e in self._evaluators(rfq_id, t) if e.identity_id == self.actor_id)

    # -- scores ----------------------------------------------------------------------------

    def _writable(self, rfq_id: UUID) -> tuple[Tally, int]:
        """Lock the RFQ, then the caller's evaluator row; refuse in the order the packet fixes."""
        head = self._head(rfq_id, lock=True)
        row = self.repo.execute(
            "SELECT version FROM rfq_evaluator WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
            "AND identity_id = %(who)s FOR UPDATE",
            {"rfq": rfq_id, "who": self.actor_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.FORBIDDEN)
        assert_may_see_bids(self.repo, rfq_id, self.actor_id)
        if head[0] in TERMINAL:
            raise conflict("rfq_terminal", "This RFQ is closed to scoring.")
        if not visible(head[1], head[2]):
            raise conflict("scoring_not_open", "Scoring opens at the bidding stop time.")
        t = tally(self.repo, rfq_id)
        if not t.criteria or not t.vendors:
            raise conflict("nothing_to_score", "There is nothing to score yet.")
        return t, cast(int, row[0])

    def _bump_version(self, rfq_id: UUID) -> int:
        row = self.repo.execute(
            "UPDATE rfq_evaluator SET version = version + 1 WHERE org_id = %(org_id)s "
            "AND rfq_id = %(rfq)s AND identity_id = %(who)s RETURNING version",
            {"rfq": rfq_id, "who": self.actor_id},
        ).fetchone()
        assert row is not None
        return cast(int, row[0])

    def save_scores(
        self, rfq_id: UUID, body: ScoresPut, if_match: str | None
    ) -> EvaluationScoreResult:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            t, version = self._writable(rfq_id)
            self._check_edit_revision(version, if_match)
            seen = set()
            for n, c in enumerate(body.cells):
                cell = (c.vendor_id, c.criterion_id)
                field = f"cells/{n}"
                if c.vendor_id not in t.vendors or c.criterion_id not in dict(t.criteria):
                    raise _invalid(
                        field,
                        "That vendor or criterion is not part of this scoring.",
                        "unknown_cell",
                    )
                if cell in seen:
                    raise _invalid(field, "Each cell may appear once.", "duplicate_cell")
                seen.add(cell)
                held = t.cells.get((cast(UUID, self.actor_id), *cell))
                if held and held[2] == "submitted":
                    raise conflict("score_submitted", "A submitted score cannot change.")
            for c in body.cells:
                self.repo.execute(
                    "INSERT INTO rfq_score (org_id, rfq_id, identity_id, vendor_id, criterion_id, "
                    "score, comment) VALUES (%(org_id)s, %(rfq)s, %(who)s, %(vendor)s, %(crit)s, "
                    "%(score)s, %(comment)s) ON CONFLICT (org_id, rfq_id, identity_id, vendor_id, "
                    "criterion_id) DO UPDATE SET score = EXCLUDED.score, "
                    "comment = EXCLUDED.comment, updated_at = now()",
                    {
                        "rfq": rfq_id,
                        "who": self.actor_id,
                        "vendor": c.vendor_id,
                        "crit": c.criterion_id,
                        "score": c.score,
                        "comment": c.comment or None,
                    },
                )
            version = self._bump_version(rfq_id)
            self._audit("rfq.score.save", rfq_id, None, {"cells": len(body.cells)})
            return EvaluationScoreResult(version=version, saved=len(body.cells), submitted=False)

    def submit_scores(self, rfq_id: UUID) -> EvaluationScoreResult:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            t, version = self._writable(rfq_id)
            me = cast(UUID, self.actor_id)
            missing = [
                (v, c) for v in t.vendors for c, _ in t.criteria if (me, v, c) not in t.cells
            ]
            if missing:
                raise _invalid(
                    "cells",
                    f"Score every criterion for every vendor first ({len(missing)} left).",
                    "incomplete_scores",
                )
            changed = _all(
                self.repo,
                "UPDATE rfq_score SET status = 'submitted', updated_at = now() "
                "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s AND identity_id = %(who)s "
                "AND status = 'draft' AND vendor_id = ANY(%(v)s) "
                "AND criterion_id = ANY(%(c)s) RETURNING 1",
                {"rfq": rfq_id, "who": me, "v": t.vendors, "c": [c for c, _ in t.criteria]},
            )
            if changed:
                version = self._bump_version(rfq_id)
                self._audit("rfq.score.submit", rfq_id, None, {"cells": len(changed)})
            return EvaluationScoreResult(version=version, saved=len(changed), submitted=True)
