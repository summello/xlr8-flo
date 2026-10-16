"""Versioned onboarding questionnaire: draft, edit, reorder, publish, copy (VEN-006, CUS-007)."""

from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.vendors.models import VendorRepository
from flo.modules.vendors.schemas import (
    QuestionAdmin,
    QuestionCreate,
    QuestionMove,
    QuestionPatch,
    VersionDetail,
    VersionPatch,
    VersionRead,
    conflict,
    invalid,
)
from flo.modules.vendors.scoring import clean_options

TARGET = "vendor_questionnaire_version"
VERSION_COLUMNS = (
    "v.id, v.number, v.status, v.threshold, v.row_version, v.published_at, "
    "(SELECT count(*) FROM vendor_question q WHERE q.version_id = v.id), "
    "(SELECT COALESCE(sum(q.weight), 0) FROM vendor_question q WHERE q.version_id = v.id)"
)
VERSION_FIELDS = (
    "id",
    "number",
    "status",
    "threshold",
    "row_version",
    "published_at",
    "question_count",
    "weight_total",
)
QUESTION_COLUMNS = "key, position, prompt, type, required, weight, options, row_version"
QUESTION_FIELDS = tuple(QUESTION_COLUMNS.replace(" ", "").split(","))


def expected_version(header: str | None, thing: str) -> int:
    """`If-Match` carries the integer version (m2-conventions O)."""
    if header is None:
        raise ProblemError(
            ErrorCode.BAD_REQUEST, detail=f"Supply If-Match with the {thing} version."
        )
    try:
        return int(header.strip().strip('"'))
    except ValueError:
        raise ProblemError(
            ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
        ) from None


def stale(thing: str) -> ProblemError:
    return conflict("stale_version", f"The {thing} changed. Reload it and retry.")


class QuestionnaireMixin:
    connection: psycopg.Connection[tuple[object, ...]]
    scope: Scope
    actor_id: UUID | None
    repo: VendorRepository

    if TYPE_CHECKING:

        def _audit(
            self,
            action: str,
            id: UUID,
            before: dict[str, object] | None,
            after: dict[str, object],
            reason: str | None = None,
            target_type: str = "vendor",
        ) -> None: ...

    # -- reads -----------------------------------------------------------------------------

    def _questions(self, version_id: UUID) -> list[QuestionAdmin]:
        rows = self.repo.execute(
            f"SELECT {QUESTION_COLUMNS} FROM vendor_question WHERE org_id = %(org_id)s "
            "AND version_id = %(vid)s ORDER BY position",
            {"vid": version_id},
        ).fetchall()
        return [
            QuestionAdmin.model_validate(dict(zip(QUESTION_FIELDS, r, strict=True))) for r in rows
        ]

    def _version(self, version_id: UUID, lock: bool = False) -> VersionRead:
        if lock:  # the status read below must not race a concurrent publish
            self.repo.execute(
                "SELECT 1 FROM vendor_questionnaire_version WHERE org_id = %(org_id)s "
                "AND id = %(id)s FOR UPDATE",
                {"id": version_id},
            )
        row = self.repo.execute(
            f"SELECT {VERSION_COLUMNS} FROM vendor_questionnaire_version v "
            "WHERE v.org_id = %(org_id)s AND v.id = %(id)s",
            {"id": version_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return VersionRead.model_validate(dict(zip(VERSION_FIELDS, row, strict=True)))

    def _detail(self, version_id: UUID) -> VersionDetail:
        return VersionDetail(
            **self._version(version_id).model_dump(), questions=self._questions(version_id)
        )

    def list_questionnaires(self) -> list[VersionRead]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rows = self.repo.execute(
                f"SELECT {VERSION_COLUMNS} FROM vendor_questionnaire_version v "
                "WHERE v.org_id = %(org_id)s ORDER BY v.number DESC"
            ).fetchall()
        return [VersionRead.model_validate(dict(zip(VERSION_FIELDS, r, strict=True))) for r in rows]

    def get_questionnaire(self, version_id: UUID) -> VersionDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return self._detail(version_id)

    # -- writes ----------------------------------------------------------------------------

    def _draft(self, version_id: UUID) -> VersionRead:
        """The version, locked, and only while it is still a draft (409 otherwise)."""
        version = self._version(version_id, lock=True)
        if version.status != "draft":
            raise conflict(
                "version_immutable",
                f"Version {version.number} is {version.status} and cannot change. "
                "Copy it to a new draft to make changes.",
            )
        return version

    def _next_number(self) -> int:
        """Serialised on the one root row, so two new drafts never share a number."""
        self.repo.execute(
            "INSERT INTO vendor_questionnaire (org_id) VALUES (%(org_id)s) ON CONFLICT DO NOTHING"
        )
        self.repo.execute("SELECT 1 FROM vendor_questionnaire WHERE org_id = %(org_id)s FOR UPDATE")
        row = self.repo.execute(
            "SELECT COALESCE(max(number), 0) + 1 FROM vendor_questionnaire_version "
            "WHERE org_id = %(org_id)s"
        ).fetchone()
        return int(cast(int, row[0])) if row else 1

    def _new_draft(self, threshold: int) -> UUID:
        id = uuid4()
        self.repo.execute(
            "INSERT INTO vendor_questionnaire_version (id, org_id, number, threshold, created_by) "
            "VALUES (%(id)s, %(org_id)s, %(number)s, %(threshold)s, %(actor)s)",
            {
                "id": id,
                "number": self._next_number(),
                "threshold": threshold,
                "actor": self.actor_id,
            },
        )
        return id

    def create_questionnaire(self) -> VersionDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            id = self._new_draft(0)
            detail = self._detail(id)
            self._audit(
                "vendor.questionnaire.create",
                id,
                None,
                {"number": detail.number},
                target_type=TARGET,
            )
        return detail

    def update_questionnaire(
        self, version_id: UUID, body: VersionPatch, if_match: str | None
    ) -> VersionDetail:
        expected = expected_version(if_match, "questionnaire")
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            before = self._draft(version_id)
            if before.row_version != expected:
                raise stale("questionnaire")
            self.repo.execute(
                "UPDATE vendor_questionnaire_version SET threshold = %(threshold)s, "
                "row_version = row_version + 1 WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": version_id, "threshold": body.threshold},
            )
            self._audit(
                "vendor.questionnaire.update",
                version_id,
                {"threshold": before.threshold},
                {"threshold": body.threshold},
                target_type=TARGET,
            )
            return self._detail(version_id)

    def _question(self, version_id: UUID, key: str) -> QuestionAdmin:
        row = self.repo.execute(
            f"SELECT {QUESTION_COLUMNS} FROM vendor_question WHERE org_id = %(org_id)s "
            "AND version_id = %(vid)s AND key = %(key)s FOR UPDATE",
            {"vid": version_id, "key": key},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return QuestionAdmin.model_validate(dict(zip(QUESTION_FIELDS, row, strict=True)))

    def _question_audit(
        self, action: str, version_id: UUID, before: QuestionAdmin | None, after: QuestionAdmin
    ) -> None:
        self._audit(
            action,
            version_id,
            before.model_dump(mode="json") if before else None,
            after.model_dump(mode="json"),
            target_type=TARGET,
        )

    def add_question(self, version_id: UUID, body: QuestionCreate) -> QuestionAdmin:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(version_id)
            if not _valid_key(body.key):
                raise invalid(
                    "key",
                    "Use lowercase letters, digits and underscores, starting with a letter.",
                    "invalid_key",
                )
            options = clean_options(body.type, body.options)
            if self.repo.execute(
                "SELECT 1 FROM vendor_question WHERE org_id = %(org_id)s "
                "AND version_id = %(vid)s AND key = %(key)s",
                {"vid": version_id, "key": body.key},
            ).fetchone():
                raise conflict(
                    "duplicate_key", f"This version already has a question {body.key!r}."
                )
            self.repo.execute(  # the version row is locked, so max(position) cannot race
                "INSERT INTO vendor_question (id, org_id, version_id, position, key, prompt, type, "
                "required, weight, options) VALUES (%(id)s, %(org_id)s, %(vid)s, (SELECT "
                "COALESCE(max(position), 0) + 1 FROM vendor_question WHERE version_id = %(vid)s), "
                "%(key)s, %(prompt)s, %(type)s, %(required)s, %(weight)s, %(options)s)",
                {
                    "id": uuid4(),
                    "vid": version_id,
                    "key": body.key,
                    "prompt": body.prompt.strip(),
                    "type": body.type,
                    "required": body.required,
                    "weight": body.weight,
                    "options": None if options is None else Jsonb(options),
                },
            )
            created = self._question(version_id, body.key)
            self._question_audit("vendor.questionnaire.question.add", version_id, None, created)
        return created

    def update_question(
        self, version_id: UUID, key: str, body: QuestionPatch, if_match: str | None
    ) -> QuestionAdmin:
        expected = expected_version(if_match, "question")
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(version_id)
            before = self._question(version_id, key)
            if before.row_version != expected:
                raise stale("question")
            given = body.model_fields_set
            for field in ("prompt", "required", "weight"):
                if field in given and getattr(body, field) is None:
                    raise invalid(field, "This field cannot be empty.")
            options = (
                clean_options(before.type, body.options) if "options" in given else before.options
            )
            self.repo.execute(
                "UPDATE vendor_question SET prompt = %(prompt)s, required = %(required)s, "
                "weight = %(weight)s, options = %(options)s, row_version = row_version + 1 "
                "WHERE org_id = %(org_id)s AND version_id = %(vid)s AND key = %(key)s",
                {
                    "vid": version_id,
                    "key": key,
                    "prompt": body.prompt.strip() if body.prompt is not None else before.prompt,
                    "required": body.required if body.required is not None else before.required,
                    "weight": body.weight if body.weight is not None else before.weight,
                    "options": None if options is None else Jsonb(options),
                },
            )
            after = self._question(version_id, key)
            self._question_audit("vendor.questionnaire.question.update", version_id, before, after)
        return after

    def delete_question(self, version_id: UUID, key: str, if_match: str | None) -> None:
        expected = expected_version(if_match, "question")
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(version_id)
            before = self._question(version_id, key)
            if before.row_version != expected:
                raise stale("question")
            self.repo.execute(
                "DELETE FROM vendor_question WHERE org_id = %(org_id)s "
                "AND version_id = %(vid)s AND key = %(key)s",
                {"vid": version_id, "key": key},
            )
            self.repo.execute(  # close the gap; the position constraint is deferred
                "UPDATE vendor_question SET position = position - 1 WHERE org_id = %(org_id)s "
                "AND version_id = %(vid)s AND position > %(position)s",
                {"vid": version_id, "position": before.position},
            )
            self._question_audit("vendor.questionnaire.question.delete", version_id, before, before)

    def move_question(self, version_id: UUID, key: str, body: QuestionMove) -> VersionDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(version_id)
            current = self._question(version_id, key)
            target = current.position + (-1 if body.direction == "up" else 1)
            neighbour = self.repo.execute(
                "SELECT key FROM vendor_question WHERE org_id = %(org_id)s "
                "AND version_id = %(vid)s AND position = %(position)s FOR UPDATE",
                {"vid": version_id, "position": target},
            ).fetchone()
            if neighbour is None:
                raise conflict(
                    "cannot_move",
                    "This question is already the "
                    + ("first" if body.direction == "up" else "last")
                    + " one.",
                )
            self.repo.execute(
                "UPDATE vendor_question SET position = CASE WHEN key = %(key)s THEN %(target)s "
                "ELSE %(current)s END WHERE org_id = %(org_id)s AND version_id = %(vid)s "
                "AND key IN (%(key)s, %(other)s)",
                {
                    "vid": version_id,
                    "key": key,
                    "other": neighbour[0],
                    "target": target,
                    "current": current.position,
                },
            )
            self._audit(
                "vendor.questionnaire.question.move",
                version_id,
                {"key": key, "position": current.position},
                {"key": key, "position": target},
                target_type=TARGET,
            )
            return self._detail(version_id)

    def publish_questionnaire(self, version_id: UUID) -> VersionDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            version = self._draft(version_id)
            if version.question_count == 0:
                raise conflict("no_questions", "Add at least one question before publishing.")
            if version.weight_total != 100:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=(
                        f"The weights add up to {version.weight_total}. "
                        "They must add up to exactly 100 before this version can be published."
                    ),
                    checks={"problem": "weights_total", "total": str(version.weight_total)},
                )
            # Retire first: at most one published version per organization.
            self.repo.execute(
                "UPDATE vendor_questionnaire_version SET status = 'retired' "
                "WHERE org_id = %(org_id)s AND status = 'published'"
            )
            self.repo.execute(
                "UPDATE vendor_questionnaire_version SET status = 'published', "
                "published_at = now(), "
                "published_by = %(actor)s, row_version = row_version + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": version_id, "actor": self.actor_id},
            )
            self._audit(
                "vendor.questionnaire.publish",
                version_id,
                {"status": "draft"},
                {"status": "published", "number": version.number},
                target_type=TARGET,
            )
            return self._detail(version_id)

    def copy_questionnaire(self, version_id: UUID) -> VersionDetail:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            source = self._version(version_id)
            id = self._new_draft(source.threshold)
            self.repo.execute(
                "INSERT INTO vendor_question (id, org_id, version_id, position, key, prompt, type, "
                "required, weight, options) SELECT gen_random_uuid(), org_id, %(new)s, position, "
                "key, prompt, type, required, weight, options FROM vendor_question "
                "WHERE org_id = %(org_id)s AND version_id = %(old)s",
                {"new": id, "old": version_id},
            )
            detail = self._detail(id)
            self._audit(
                "vendor.questionnaire.copy",
                id,
                {"copied_from": str(version_id)},
                {"number": detail.number, "questions": detail.question_count},
                target_type=TARGET,
            )
        return detail


def _valid_key(key: str) -> bool:
    return (
        1 <= len(key) <= 40
        and key[0].isascii()
        and key[0].islower()
        and all(c.isascii() and (c.islower() or c.isdigit() or c == "_") for c in key)
    )
