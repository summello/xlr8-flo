"""Onboarding questionnaire and request: versions, scoring, immutability (E11-S05)."""

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.vendors.schemas import (
    AnswersPut,
    QuestionCreate,
    QuestionMove,
    QuestionPatch,
    VendorCreate,
    VersionPatch,
)
from flo.modules.vendors.scoring import Question, score_answers
from flo.modules.vendors.service import VendorService
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.vendors.test_vendors import regrant

YES_NO = {"yes": 100, "no": 0}
SIZE = [
    {"value": "s", "label": "Small", "points": 50},
    {"value": "l", "label": "Large", "points": 100},
]


def svc(db, org=None, actor=None):
    return VendorService(db[0], Scope(org or db[1]), actor or db[3])


def question(key, type="text", weight=0, options=None, required=False):
    return QuestionCreate(
        key=key,
        prompt=f"Prompt {key}",
        type=type,
        weight=weight,
        options=options,
        required=required,
    )


def published(db, *questions, threshold=0, org=None):
    """A published version holding `questions` (their weights must total 100)."""
    service = svc(db, org)
    version = service.create_questionnaire()
    if threshold:
        service.update_questionnaire(version.id, VersionPatch(threshold=threshold), '"1"')
    for item in questions:
        service.add_question(version.id, item)
    return service.publish_questionnaire(version.id)


def two(db):
    return published(
        db, question("name", weight=40, required=True), question("size", "single_choice", 60, SIZE)
    )


def problem(error: pytest.ExceptionInfo[ProblemError]) -> tuple[ErrorCode, str | None]:
    return error.value.code, (error.value.checks or {}).get("problem")


def sql(db, query, args=(), org=None):
    with tenant_transaction(db[0], Scope(org or db[1])):
        cursor = db[0].execute(query, args)
        return cursor.fetchall() if cursor.description else []


def new_identity(db):
    return (
        db[0]
        .execute(
            "INSERT INTO identity(id,email,password_hash) VALUES (gen_random_uuid(),%s,"
            "'$argon2id$test-placeholder') RETURNING id",
            (f"{uuid4().hex}@example.test",),
        )
        .fetchone()[0]
    )


def vendor_body(name="Acme"):
    return VendorCreate(trading_name=name, legal_name=name, availability="global")


# -- scoring --------------------------------------------------------------------------------


def test_score_worked_example_with_every_question_type():
    multi = [
        {"value": "x", "label": "X", "points": 30},
        {"value": "y", "label": "Y", "points": 40},
        {"value": "z", "label": "Z", "points": 50},
    ]
    questions = [
        Question("t", "T", "text", True, 10, None),
        Question(
            "n",
            "N",
            "number",
            False,
            20,
            [{"min": "0", "max": "9", "points": 20}, {"min": "10", "max": None, "points": 80}],
        ),
        Question("y", "Y", "yes_no", False, 20, YES_NO),
        Question("s", "S", "single_choice", False, 20, SIZE),
        Question("m", "M", "multi_choice", False, 20, multi),
        Question("d", "D", "date", False, 0, None),
        Question("f", "F", "file", False, 0, None),
    ]
    answers = {"t": "x", "n": "12", "y": True, "s": "s", "m": ["x", "y"], "f": str(uuid4())}
    result = score_answers(questions, answers, 60)
    # 10*100 + 20*80 + 20*100 + 20*50 + 20*70, each over 100 = 10 + 16 + 20 + 10 + 14
    assert result.score == 70 and result.threshold == 60
    assert {line.key: line.points for line in result.breakdown} == {
        "t": 100,
        "n": 80,
        "y": 100,
        "s": 50,
        "m": 70,
        "d": 0,
        "f": 100,
    }
    assert [line.answered for line in result.breakdown][-2:] == [False, True]
    # an unanswered optional question scores 0, and a low score is a fact, not an error
    assert score_answers(questions, {}, 60).score == 0


@pytest.mark.parametrize(
    ("weight", "points", "expected"),
    [(5, 50, 3), (7, 50, 4), (3, 50, 2), (1, 49, 0), (1, 50, 1), (100, 100, 100)],
)
def test_score_rounds_half_up(weight, points, expected):
    options = [{"value": "a", "label": "A", "points": points}]
    result = score_answers(
        [Question("a", "A", "single_choice", False, weight, options)], {"a": "a"}, 0
    )
    assert result.score == expected  # 2.5 -> 3 where banker's rounding or round() gives 2
    assert Decimal(result.breakdown[0].contribution) == Decimal(weight) * points / 100


# -- versions, publish, immutability -----------------------------------------------------------


@pytest.mark.parametrize(
    ("weights", "ok"), [((50, 49), False), ((50, 51), False), ((50, 50), True)]
)
def test_publish_needs_a_weight_total_of_exactly_100(header_db, weights, ok):
    service = svc(header_db)
    version = service.create_questionnaire()
    for index, weight in enumerate(weights):
        service.add_question(version.id, question(f"q{index}", weight=weight))
    if ok:
        assert service.publish_questionnaire(version.id).status == "published"
        return
    with pytest.raises(ProblemError) as error:
        service.publish_questionnaire(version.id)
    assert problem(error) == (ErrorCode.CONFLICT, "weights_total")
    assert error.value.checks["total"] == str(sum(weights))
    assert service.get_questionnaire(version.id).status == "draft"


def test_publish_refuses_an_empty_version_and_retires_the_previous_one(header_db):
    service = svc(header_db)
    empty = service.create_questionnaire()
    with pytest.raises(ProblemError) as error:
        service.publish_questionnaire(empty.id)
    assert problem(error) == (ErrorCode.CONFLICT, "no_questions")
    first = two(header_db)
    second = service.copy_questionnaire(first.id)
    service.publish_questionnaire(second.id)
    status = {v.number: v.status for v in service.list_questionnaires()}
    assert status == {first.number: "retired", second.number: "published", empty.number: "draft"}


def test_a_published_version_refuses_every_write_and_the_trigger_backs_it_up(header_db):
    db, service = header_db, svc(header_db)
    version = two(db)
    writes = [
        lambda: service.update_questionnaire(version.id, VersionPatch(threshold=10), '"2"'),
        lambda: service.add_question(version.id, question("extra")),
        lambda: service.update_question(version.id, "name", QuestionPatch(weight=1), '"1"'),
        lambda: service.delete_question(version.id, "name", '"1"'),
        lambda: service.move_question(version.id, "size", QuestionMove(direction="up")),
        lambda: service.publish_questionnaire(version.id),
    ]
    for write in writes:
        with pytest.raises(ProblemError) as error:
            write()
        assert problem(error) == (ErrorCode.CONFLICT, "version_immutable")
    # the service is not the only gate: raw SQL meets the trigger
    for statement in (
        "UPDATE vendor_question SET weight = 1 WHERE version_id = %s",
        "DELETE FROM vendor_question WHERE version_id = %s",
        "INSERT INTO vendor_question (id, org_id, version_id, position, key, prompt, type, weight)"
        " SELECT gen_random_uuid(), org_id, version_id, 9, 'raw', 'p', 'text', 0"
        " FROM vendor_question WHERE version_id = %s LIMIT 1",
        "UPDATE vendor_questionnaire_version SET threshold = 5 WHERE id = %s",
        "DELETE FROM vendor_questionnaire_version WHERE id = %s",
    ):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            sql(db, statement, (version.id,))
    assert svc(db).get_questionnaire(version.id).threshold == 0


def test_copy_makes_an_identical_new_draft_and_editing_it_leaves_the_original_alone(header_db):
    db, service = header_db, svc(header_db)
    original = two(db)
    draft = service.copy_questionnaire(original.id)
    assert (draft.number, draft.status) == (original.number + 1, "draft")
    assert [q.model_dump() | {"row_version": 0} for q in draft.questions] == [
        q.model_dump() | {"row_version": 0} for q in original.questions
    ]
    service.update_question(draft.id, "name", QuestionPatch(weight=10, prompt="Changed"), '"1"')
    service.add_question(draft.id, question("late", weight=30))
    service.delete_question(draft.id, "size", '"1"')
    again = service.get_questionnaire(original.id)
    assert again.status == "published" and again.questions == original.questions
    assert [q.key for q in service.get_questionnaire(draft.id).questions] == ["name", "late"]


def test_move_swaps_neighbours_and_refuses_at_either_end(header_db):
    service = svc(header_db)
    version = service.create_questionnaire()
    for key in "abc":
        service.add_question(version.id, question(key))
    order = lambda: [q.key for q in service.get_questionnaire(version.id).questions]  # noqa: E731
    assert order() == ["a", "b", "c"]
    service.move_question(version.id, "c", QuestionMove(direction="up"))
    assert order() == ["a", "c", "b"]
    service.move_question(version.id, "a", QuestionMove(direction="down"))
    assert order() == ["c", "a", "b"]
    for key, direction in (("c", "up"), ("b", "down")):
        with pytest.raises(ProblemError) as error:
            service.move_question(version.id, key, QuestionMove(direction=direction))
        assert problem(error) == (ErrorCode.CONFLICT, "cannot_move")
    service.delete_question(version.id, "c", '"1"')
    assert [q.position for q in service.get_questionnaire(version.id).questions] == [1, 2]


def test_question_rules_options_keys_and_if_match(header_db):
    service = svc(header_db)
    version = service.create_questionnaire()
    for bad in (
        question("choice", "single_choice", 1, None),
        question("choice", "single_choice", 1, [{"value": "a", "label": "A", "points": 101}]),
        question("choice", "single_choice", 1, SIZE + SIZE),
        question("yn", "yes_no", 1, {"yes": 1}),
        question("txt", "text", 1, [1]),
        question("num", "number", 1, [{"min": 5, "max": 1, "points": 1}]),
    ):
        with pytest.raises(ProblemError) as error:
            service.add_question(version.id, bad)
        assert error.value.errors[0].field == "options", bad
    with pytest.raises(ProblemError) as error:
        service.add_question(version.id, question("Bad-Key"))
    assert error.value.errors[0].field == "key"
    created = service.add_question(version.id, question("ok", weight=5))
    with pytest.raises(ProblemError) as error:
        service.add_question(version.id, question("ok"))
    assert problem(error) == (ErrorCode.CONFLICT, "duplicate_key")
    with pytest.raises(ProblemError) as error:
        service.update_question(version.id, "ok", QuestionPatch(weight=6), None)
    assert error.value.code is ErrorCode.BAD_REQUEST
    with pytest.raises(ProblemError) as error:
        service.update_question(version.id, "ok", QuestionPatch(weight=6), '"7"')
    assert problem(error) == (ErrorCode.CONFLICT, "stale_version")
    assert (
        service.update_question(
            version.id, "ok", QuestionPatch(weight=6), f'"{created.row_version}"'
        ).row_version
        == 2
    )
    audit = {
        r[0]
        for r in sql(
            header_db, "SELECT action FROM audit_log WHERE action LIKE 'vendor.questionnaire.%%'"
        )
    }
    assert {
        "vendor.questionnaire.create",
        "vendor.questionnaire.question.add",
        "vendor.questionnaire.question.update",
    } <= audit


# -- the request ------------------------------------------------------------------------------


def walk(value):
    """Every dict key at any depth."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from walk(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from walk(inner)


def test_a_request_needs_a_published_questionnaire_and_hides_weights_and_points(header_db):
    db = header_db
    service = svc(db)
    with pytest.raises(ProblemError) as error:
        service.create_onboarding(vendor_body())
    assert problem(error) == (ErrorCode.CONFLICT, "no_published_questionnaire")
    assert sql(db, "SELECT count(*) FROM vendor") == [(0,)]  # nothing half-created
    two(db)
    request = service.create_onboarding(vendor_body())
    assert request.vendor_status == "draft" and request.version_number == 1
    payload = json.loads(request.model_dump_json())
    keys = set(walk(payload))
    assert not keys & {"weight", "points", "options", "threshold", "score"}, keys
    assert [q["key"] for q in payload["questions"]] == ["name", "size"]
    assert payload["questions"][1]["choices"] == [
        {"value": "s", "label": "Small"},
        {"value": "l", "label": "Large"},
    ]


def test_a_request_keeps_its_version_after_a_newer_one_publishes(header_db):
    db, service = header_db, svc(header_db)
    first = two(db)
    request = service.create_onboarding(vendor_body())
    answers = {"name": "Acme", "size": "s"}
    service.put_answers(request.vendor_id, AnswersPut(answers=answers), f'"{request.row_version}"')
    draft = service.copy_questionnaire(first.id)
    service.update_question(
        draft.id,
        "size",
        QuestionPatch(
            options=[
                {"value": "s", "label": "S", "points": 0},
                {"value": "l", "label": "L", "points": 0},
            ]
        ),
        '"1"',
    )
    service.publish_questionnaire(draft.id)
    assert service.get_onboarding(request.vendor_id).version_number == 1
    result = service.score(request.vendor_id)
    assert (result.score, result.threshold) == (70, 0)  # 40 + 60 * 50 / 100 against v1
    newer = service.create_onboarding(vendor_body("Other"))
    assert newer.version_number == 2
    service.put_answers(newer.vendor_id, AnswersPut(answers=answers), '"1"')
    assert service.score(newer.vendor_id).score == 40  # v2 gives the choice 0 points
    stored = sql(
        db,
        "SELECT score, breakdown FROM vendor_onboarding WHERE vendor_id = %s",
        (request.vendor_id,),
    )
    assert stored[0][0] == 70 and stored[0][1][1]["points"] == 50


def test_low_scores_inform_and_required_answers_are_only_listed(header_db):
    service = svc(header_db)
    published(header_db, question("name", weight=100, required=True), threshold=90)
    request = service.create_onboarding(vendor_body())
    assert service.answers_complete(request.vendor_id) == ["name"]
    low = service.score(request.vendor_id)
    assert (low.score, low.threshold) == (0, 90)  # below threshold, and nothing refuses
    service.put_answers(request.vendor_id, AnswersPut(answers={"name": "Acme"}), '"1"')
    assert service.answers_complete(request.vendor_id) == []


def test_answers_are_validated_per_field_and_input_survives(header_db):
    service = svc(header_db)
    published(
        header_db,
        question("txt", weight=10),
        question("num", "number", 10),
        question("yn", "yes_no", 10, YES_NO),
        question("one", "single_choice", 10, SIZE),
        question("many", "multi_choice", 10, SIZE),
        question("when", "date", 10),
        question("doc", "file", 40),
    )
    request = service.create_onboarding(vendor_body())
    bad = {
        "txt": 5,
        "num": "abc",
        "yn": "yes",
        "one": "xl",
        "many": ["s", "s"],
        "when": "13/13/2026",
        "doc": str(uuid4()),
        "ghost": "x",
    }
    with pytest.raises(ProblemError) as error:
        service.put_answers(request.vendor_id, AnswersPut(answers=bad), '"1"')
    assert error.value.code is ErrorCode.VALIDATION_FAILED
    assert {e.field for e in error.value.errors} == {f"answers.{k}" for k in bad}
    assert service.get_onboarding(request.vendor_id).answers == {}
    good = {
        "txt": "hi",
        "num": 12.5,
        "yn": False,
        "one": "l",
        "many": ["s", "l"],
        "when": "2026-10-16",
    }
    saved = service.put_answers(request.vendor_id, AnswersPut(answers=good), '"1"')
    assert saved.answers["num"] == "12.5" and saved.row_version == 2
    # a null clears one answer; a stale or missing If-Match is refused
    cleared = service.put_answers(request.vendor_id, AnswersPut(answers={"txt": None}), '"2"')
    assert cleared.answers == {} or "txt" not in cleared.answers
    with pytest.raises(ProblemError) as error:
        service.put_answers(request.vendor_id, AnswersPut(answers={}), '"2"')
    assert problem(error) == (ErrorCode.CONFLICT, "stale_version")
    with pytest.raises(ProblemError) as error:
        service.put_answers(request.vendor_id, AnswersPut(answers={}), None)
    assert error.value.code is ErrorCode.BAD_REQUEST
    sql(header_db, "UPDATE vendor SET status = 'pending_approval'")
    with pytest.raises(ProblemError) as error:
        service.put_answers(request.vendor_id, AnswersPut(answers={}), '"3"')
    assert problem(error) == (ErrorCode.CONFLICT, "not_draft")


def test_a_file_answer_must_be_an_attachment_on_this_vendor(header_db):
    from tests.vendors.test_documents import attach

    db, service = header_db, svc(header_db)
    published(db, question("doc", "file", 100))
    mine, other = (service.create_onboarding(vendor_body(n)).vendor_id for n in ("A", "B"))
    attachment, _ = attach(db, mine)
    assert service.put_answers(mine, AnswersPut(answers={"doc": str(attachment)}), '"1"').answers[
        "doc"
    ] == str(attachment)
    with pytest.raises(ProblemError) as error:
        service.put_answers(other, AnswersPut(answers={"doc": str(attachment)}), '"1"')
    assert error.value.errors[0].field == "answers.doc"
    assert service.score(mine).score == 100


def test_only_the_requester_reads_or_answers_a_draft_request(header_db):
    db = header_db
    two(db)
    request = svc(db).create_onboarding(vendor_body())
    stranger = svc(db, actor=new_identity(db))
    for call in (
        lambda: stranger.get_onboarding(request.vendor_id),
        lambda: stranger.put_answers(request.vendor_id, AnswersPut(answers={}), '"1"'),
    ):
        with pytest.raises(ProblemError) as error:
            call()
        assert error.value.code is ErrorCode.NOT_FOUND


# -- HTTP -------------------------------------------------------------------------------------

ADMIN = "/api/v1/admin/vendor-questionnaires"


async def call(browser, method, path, data=None, **extra):
    return await browser.request(
        method, path, json=data, headers=headers(browser, uuid4().hex) | extra
    )


def test_questionnaire_routes_run_end_to_end_through_the_middleware(header_db):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            made = await call(browser, "POST", ADMIN)
            assert made.status_code == 201 and made.headers["etag"] == '"1"', made.text
            vid = made.json()["id"]
            added = await call(
                browser,
                "POST",
                f"{ADMIN}/{vid}/questions",
                {"key": "a", "prompt": "A", "type": "text", "weight": 100},
            )
            assert added.status_code == 201, added.text
            missing = await call(browser, "PATCH", f"{ADMIN}/{vid}/questions/a", {"weight": 90})
            assert missing.status_code == 400
            stale = await call(
                browser,
                "PATCH",
                f"{ADMIN}/{vid}/questions/a",
                {"weight": 90},
                **{"If-Match": '"9"'},
            )
            assert stale.status_code == 409 and stale.json()["checks"]["problem"] == "stale_version"
            patched = await call(
                browser, "PATCH", f"{ADMIN}/{vid}", {"threshold": 70}, **{"If-Match": '"1"'}
            )
            assert patched.status_code == 200 and patched.headers["etag"] == '"2"'
            bad = await call(browser, "POST", f"{ADMIN}/{vid}/publish")
            assert bad.status_code == 200, bad.text
            frozen = await call(
                browser, "PATCH", f"{ADMIN}/{vid}", {"threshold": 1}, **{"If-Match": '"3"'}
            )
            assert (
                frozen.status_code == 409
                and frozen.json()["checks"]["problem"] == "version_immutable"
            )
            copied = await call(browser, "POST", f"{ADMIN}/{vid}/copy")
            assert copied.status_code == 201 and copied.json()["number"] == 2
            listed = await call(browser, "GET", ADMIN)
            assert [v["status"] for v in listed.json()] == ["draft", "published"]
            # the requester half
            request = await call(
                browser,
                "POST",
                "/api/v1/vendors/onboarding-requests",
                {"trading_name": "N", "legal_name": "N", "availability": "global"},
            )
            assert request.status_code == 201, request.text
            id = request.json()["vendor_id"]
            assert not set(walk(request.json())) & {"weight", "points"}
            put = await call(
                browser,
                "PUT",
                f"/api/v1/vendors/{id}/onboarding/answers",
                {"answers": {"a": "x"}},
                **{"If-Match": request.headers["etag"]},
            )
            assert put.status_code == 200 and put.headers["etag"] == '"2"'
            wrong = await call(
                browser,
                "PUT",
                f"/api/v1/vendors/{id}/onboarding/answers",
                {"answers": {"a": 1}},
                **{"If-Match": '"2"'},
            )
            assert wrong.status_code == 422 and wrong.json()["errors"][0]["field"] == "answers.a"
            assert (await call(browser, "GET", f"/api/v1/vendors/{id}/onboarding")).json()[
                "answers"
            ] == {"a": "x"}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("permissions", "admin_status", "request_status"),
    [
        (["requisition.view"], 403, 403),
        (["vendor.request"], 403, 201),  # any employee may ask, only administrators configure
        (["org.setting.manage"], 200, 403),
    ],
)
def test_questionnaire_permissions_per_role(header_db, permissions, admin_status, request_status):
    db = header_db
    two(db)
    regrant(db, permissions)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert (await call(browser, "GET", ADMIN)).status_code == admin_status
            made = await call(
                browser,
                "POST",
                "/api/v1/vendors/onboarding-requests",
                {"trading_name": "N", "legal_name": "N", "availability": "global"},
            )
            assert made.status_code == request_status, made.text

    asyncio.run(scenario())


def test_questionnaire_routes_conceal_foreign_tenants(header_db):
    db = header_db
    foreign = published(db, question("a", weight=100), org=db[2])
    draft = svc(db, db[2]).copy_questionnaire(foreign.id)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, suffix, data in [
                ("GET", "", None),
                ("PATCH", "", {"threshold": 1}),
                ("POST", "/questions", {"key": "k", "prompt": "P", "type": "text", "weight": 1}),
                ("PATCH", "/questions/a", {"weight": 1}),
                ("DELETE", "/questions/a", None),
                ("POST", "/questions/a/move", {"direction": "down"}),
                ("POST", "/publish", None),
                ("POST", "/copy", None),
            ]:
                response = await call(
                    browser, method, f"{ADMIN}/{draft.id}{suffix}", data, **{"If-Match": '"1"'}
                )
                assert response.status_code == 404, (method, suffix, response.text)
            assert (await call(browser, "GET", ADMIN)).json() == []

    asyncio.run(scenario())
    assert svc(db, db[2]).get_questionnaire(draft.id).questions  # untouched


def test_onboarding_routes_conceal_foreign_tenants_and_other_requesters(header_db):
    db = header_db
    two(db)
    published(db, question("a", weight=100), org=db[2])
    foreign = svc(db, db[2]).create_onboarding(vendor_body("Foreign"))
    theirs = svc(db, actor=new_identity(db)).create_onboarding(vendor_body("Colleague"))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for vendor in (foreign.vendor_id, theirs.vendor_id):
                for method, suffix, data in [
                    ("GET", "/onboarding", None),
                    ("PUT", "/onboarding/answers", {"answers": {}}),
                ]:
                    response = await call(
                        browser,
                        method,
                        f"/api/v1/vendors/{vendor}{suffix}",
                        data,
                        **{"If-Match": '"1"'},
                    )
                    assert response.status_code == 404, (method, suffix, response.text)

    asyncio.run(scenario())
