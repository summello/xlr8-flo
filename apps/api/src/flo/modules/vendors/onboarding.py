"""New-vendor onboarding request: answers against a pinned questionnaire version (VEN-004)."""

from typing import TYPE_CHECKING, cast
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.vendors.models import VendorRepository
from flo.modules.vendors.questionnaire import expected_version, stale
from flo.modules.vendors.schemas import (
    AnswersPut,
    Choice,
    OnboardingRead,
    QuestionPublic,
    ScoreResult,
    VendorCreate,
    VendorDetail,
    conflict,
)
from flo.modules.vendors.scoring import (
    CHOICE_TYPES,
    Question,
    answered,
    clean_answer,
    score_answers,
)

ONBOARDING_COLUMNS = (
    "o.version_id, o.answers, o.row_version, o.requested_by, v.status, v.number, "
    "q.number, q.threshold"
)
ONBOARDING_FROM = (
    "FROM vendor_onboarding o JOIN vendor v ON v.org_id = o.org_id AND v.id = o.vendor_id "
    "JOIN vendor_questionnaire_version q ON q.org_id = o.org_id AND q.id = o.version_id"
)


class OnboardingMixin:
    connection: psycopg.Connection[tuple[object, ...]]
    scope: Scope
    actor_id: UUID | None
    repo: VendorRepository

    if TYPE_CHECKING:

        def create(self, body: VendorCreate) -> VendorDetail: ...

        def _audit(
            self,
            action: str,
            id: UUID,
            before: dict[str, object] | None,
            after: dict[str, object],
            reason: str | None = None,
            target_type: str = "vendor",
        ) -> None: ...

    def _request(self, vendor_id: UUID, *, owner: bool, lock: bool = False) -> tuple[object, ...]:
        """The vendor's onboarding row. `owner` limits it to the requester: anyone else gets 404."""
        row = self.repo.execute(
            f"SELECT {ONBOARDING_COLUMNS} {ONBOARDING_FROM} WHERE o.org_id = %(org_id)s "
            "AND o.vendor_id = %(id)s AND (NOT %(owner)s OR o.requested_by = %(actor)s)"
            + (" FOR UPDATE OF o" if lock else ""),
            {"id": vendor_id, "owner": owner, "actor": self.actor_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return row

    def _questions_of(self, version_id: UUID) -> list[Question]:
        rows = self.repo.execute(
            "SELECT key, prompt, type, required, weight, options FROM vendor_question "
            "WHERE org_id = %(org_id)s AND version_id = %(vid)s ORDER BY position",
            {"vid": version_id},
        ).fetchall()
        return [Question(*cast(tuple[str, str, str, bool, int, JsonValue], r)) for r in rows]

    @staticmethod
    def _public(position: int, q: Question) -> QuestionPublic:
        choices = None
        if q.type in CHOICE_TYPES and isinstance(q.options, list):
            choices = [
                Choice(value=str(c["value"]), label=str(c["label"]))
                for c in q.options
                if isinstance(c, dict)
            ]
        return QuestionPublic(
            key=q.key,
            position=position,
            prompt=q.prompt,
            type=q.type,  # type: ignore[arg-type]
            required=q.required,
            choices=choices,
        )

    def _onboarding_read(self, vendor_id: UUID, row: tuple[object, ...]) -> OnboardingRead:
        version_id = cast(UUID, row[0])
        return OnboardingRead(
            vendor_id=vendor_id,
            vendor_number=str(row[5]),
            vendor_status=cast(str, row[4]),  # type: ignore[arg-type]
            version_number=cast(int, row[6]),
            questions=[
                self._public(i, q) for i, q in enumerate(self._questions_of(version_id), start=1)
            ],
            answers=cast(dict[str, JsonValue], row[1]),
            row_version=cast(int, row[2]),
        )

    def create_onboarding(self, body: VendorCreate) -> OnboardingRead:
        """A draft vendor plus its request, pinned to the questionnaire published right now."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            published = self.repo.execute(
                "SELECT id FROM vendor_questionnaire_version WHERE org_id = %(org_id)s "
                "AND status = 'published'"
            ).fetchone()
            if published is None:
                raise conflict(
                    "no_published_questionnaire",
                    "No onboarding questionnaire is published yet. "
                    "Ask an administrator to publish one.",
                )
            vendor = self.create(body)
            self.repo.execute(
                "INSERT INTO vendor_onboarding (vendor_id, org_id, version_id, requested_by) "
                "VALUES (%(id)s, %(org_id)s, %(version)s, %(actor)s)",
                {"id": vendor.id, "version": published[0], "actor": self.actor_id},
            )
            self._audit(
                "vendor.onboarding.request", vendor.id, None, {"version_id": str(published[0])}
            )
            return self._onboarding_read(vendor.id, self._request(vendor.id, owner=True))

    def get_onboarding(self, vendor_id: UUID) -> OnboardingRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return self._onboarding_read(vendor_id, self._request(vendor_id, owner=True))

    def put_answers(
        self, vendor_id: UUID, body: AnswersPut, if_match: str | None
    ) -> OnboardingRead:
        expected = expected_version(if_match, "onboarding request")
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self._request(vendor_id, owner=True, lock=True)
            if row[4] != "draft":
                raise conflict(
                    "not_draft", "This request was submitted. Its answers can no longer change."
                )
            if row[2] != expected:
                raise stale("onboarding request")
            questions = {q.key: q for q in self._questions_of(cast(UUID, row[0]))}
            stored: dict[str, JsonValue] = {}
            errors: list[ProblemFieldError] = []
            for key, value in body.answers.items():
                question = questions.get(key)
                if question is None:
                    errors.append(
                        ProblemFieldError(
                            field=f"answers.{key}",
                            message="This question is not on your questionnaire.",
                        )
                    )
                elif value is not None:
                    try:
                        stored[key] = clean_answer(question, value)
                    except ValueError as error:
                        errors.append(ProblemFieldError(field=f"answers.{key}", message=str(error)))
                    else:
                        if question.type == "file" and not self._own_file(
                            vendor_id, str(stored[key])
                        ):
                            del stored[key]
                            errors.append(
                                ProblemFieldError(
                                    field=f"answers.{key}",
                                    message="Upload the file to this vendor first, then choose it.",
                                )
                            )
            if errors:
                raise ProblemError(
                    ErrorCode.VALIDATION_FAILED,
                    detail="Some answers need attention. Your other answers were kept on screen.",
                    errors=tuple(errors),
                    checks={"problem": "invalid_answers"},
                )
            self.repo.execute(
                "UPDATE vendor_onboarding SET answers = %(answers)s, score = NULL, "
                "breakdown = NULL, "
                "row_version = row_version + 1 WHERE org_id = %(org_id)s AND vendor_id = %(id)s",
                {"id": vendor_id, "answers": Jsonb(stored)},
            )
            # Answers can hold personal data, so the log records which questions, not what.
            self._audit(
                "vendor.onboarding.answers",
                vendor_id,
                {"answered": sorted(cast(dict[str, object], row[1]))},
                {"answered": sorted(stored)},
            )
            return self._onboarding_read(vendor_id, self._request(vendor_id, owner=True))

    def _own_file(self, vendor_id: UUID, attachment_id: str) -> bool:
        return (
            self.repo.execute(
                "SELECT 1 FROM attachment WHERE org_id = %(org_id)s AND id = %(id)s "
                "AND document_type = 'vendor' AND document_id = %(vendor)s AND deleted_at IS NULL",
                {"id": attachment_id, "vendor": vendor_id},
            ).fetchone()
            is not None
        )

    def answers_complete(self, vendor_id: UUID) -> list[str]:
        """Names the required questions still unanswered (E11-S06 calls this at submit)."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self._request(vendor_id, owner=False)
            answers = cast(dict[str, JsonValue], row[1])
            return [
                q.key
                for q in self._questions_of(cast(UUID, row[0]))
                if q.required and not answered(answers.get(q.key))
            ]

    def score(self, vendor_id: UUID) -> ScoreResult:
        """Score the answers against the request's own version and keep the breakdown with it.

        A score under the threshold is a fact on the result, never a refusal (decision 6h-6).
        """
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self._request(vendor_id, owner=False, lock=True)
            result = score_answers(
                self._questions_of(cast(UUID, row[0])),
                cast(dict[str, JsonValue], row[1]),
                cast(int, row[7]),
            )
            self.repo.execute(
                "UPDATE vendor_onboarding SET score = %(score)s, breakdown = %(breakdown)s "
                "WHERE org_id = %(org_id)s AND vendor_id = %(id)s",
                {
                    "id": vendor_id,
                    "score": result.score,
                    "breakdown": Jsonb([line.model_dump(mode="json") for line in result.breakdown]),
                },
            )
            return result
