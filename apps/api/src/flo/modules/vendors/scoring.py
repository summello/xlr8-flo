"""Questionnaire rules that touch no database: option shapes, answer types, the score (VEN-006).

A score is not money, so it does not go through `kernel/money`; it is a `Decimal` sum rounded
half up to an integer from 0 to 100.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID

from pydantic import JsonValue

from flo.modules.vendors.schemas import ScoreLine, ScoreResult, invalid

HUNDRED = Decimal(100)
CHOICE_TYPES = ("single_choice", "multi_choice")


@dataclass(frozen=True, slots=True)
class Question:
    key: str
    prompt: str
    type: str
    required: bool
    weight: int
    options: JsonValue


def _points(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100


def _number(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def clean_options(type: str, options: JsonValue) -> JsonValue:
    """The stored shape of a question's options, or a 422 on `options`."""

    def bad(message: str) -> Exception:
        return invalid("options", message, "invalid_options")

    if type in ("text", "date", "file"):
        if options is not None:
            raise bad(f"A {type} question has no options.")
        return None
    if type == "yes_no":
        if (
            not isinstance(options, dict)
            or set(options) != {"yes", "no"}
            or not all(_points(v) for v in options.values())
        ):
            raise bad('Give points from 0 to 100 for "yes" and "no".')
        return options
    if type == "number":
        if options is None:
            return None
        if not isinstance(options, list) or len(options) > 50:
            raise bad("Give up to 50 bands, each with min, max and points.")
        bands: list[JsonValue] = []
        for band in options:
            if not isinstance(band, dict) or set(band) != {"min", "max", "points"}:
                raise bad("Each band needs min, max (either may be null) and points.")
            low, high = (None if band[k] is None else _number(band[k]) for k in ("min", "max"))
            if (band["min"] is not None and low is None) or (
                band["max"] is not None and high is None
            ):
                raise bad("Band limits must be numbers or null.")
            if low is not None and high is not None and low > high:
                raise bad("A band's min cannot exceed its max.")
            if not _points(band["points"]):
                raise bad("Band points run from 0 to 100.")
            bands.append(
                {
                    "min": None if low is None else format(low, "f"),
                    "max": None if high is None else format(high, "f"),
                    "points": band["points"],
                }
            )
        return bands or None
    # single_choice, multi_choice
    if not isinstance(options, list) or not 1 <= len(options) <= 50:
        raise bad("Give 1 to 50 choices, each with value, label and points.")
    seen: set[str] = set()
    for choice in options:
        if not isinstance(choice, dict) or set(choice) != {"value", "label", "points"}:
            raise bad("Each choice needs value, label and points.")
        value, label = choice["value"], choice["label"]
        if not (isinstance(value, str) and 1 <= len(value) <= 100):
            raise bad("A choice value is 1 to 100 characters.")
        if not (isinstance(label, str) and 1 <= len(label) <= 200):
            raise bad("A choice label is 1 to 200 characters.")
        if value in seen:
            raise bad(f"Choice value {value!r} is used twice.")
        seen.add(value)
        if not _points(choice["points"]):
            raise bad("Choice points run from 0 to 100.")
    return options


def clean_answer(question: Question, value: JsonValue) -> JsonValue:
    """The stored form of one answer. Raises ValueError with a message for the field."""
    type = question.type
    if type == "text":
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 2000:
            raise ValueError("Enter 1 to 2000 characters.")
        return value.strip()
    if type == "number":
        number = _number(value)
        if number is None:
            raise ValueError("Enter a number.")
        return format(number, "f")
    if type == "yes_no":
        if not isinstance(value, bool):
            raise ValueError("Answer yes or no.")
        return value
    if type == "date":
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            raise ValueError("Enter a date as YYYY-MM-DD.") from None
    if type == "file":
        try:
            return str(UUID(str(value)))
        except ValueError:
            raise ValueError("Choose an uploaded file.") from None
    choices = question.options if isinstance(question.options, list) else []
    values = {c["value"] for c in choices if isinstance(c, dict)}
    if type == "single_choice":
        if not isinstance(value, str) or value not in values:
            raise ValueError("Choose one of the listed options.")
        return value
    # multi_choice
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) and v in values for v in value)
        or len(set(map(str, value))) != len(value)
    ):
        raise ValueError("Choose one or more of the listed options, each once.")
    return value


def answered(value: JsonValue) -> bool:
    return value is not None and value != [] and value != ""


def points_for(question: Question, value: JsonValue) -> int:
    """0 to 100. Unanswered is 0; text, date and file score 100 once answered."""
    if not answered(value):
        return 0
    options = question.options
    if question.type == "yes_no" and isinstance(options, dict):
        points = options["yes" if value is True else "no"]
        return points if isinstance(points, int) else 0
    if question.type == "single_choice" and isinstance(options, list):
        by_value = {c["value"]: c["points"] for c in options if isinstance(c, dict)}
        points = by_value.get(value if isinstance(value, str) else "", 0)
        return points if isinstance(points, int) else 0
    if question.type == "multi_choice" and isinstance(options, list) and isinstance(value, list):
        by_value = {c["value"]: c["points"] for c in options if isinstance(c, dict)}
        # ponytail: selected points add up, capped at 100; switch to a per-question rule if asked
        return min(100, sum(p for v in value if isinstance(p := by_value.get(v, 0), int)))
    if question.type == "number" and isinstance(options, list):
        number = _number(value)
        for band in options:
            if not isinstance(band, dict) or number is None:
                continue
            low, high = (None if band[k] is None else Decimal(str(band[k])) for k in ("min", "max"))
            if (low is None or number >= low) and (high is None or number <= high):
                points = band["points"]
                return points if isinstance(points, int) else 0
        return 0
    return 100


def score_answers(
    questions: list[Question], answers: dict[str, JsonValue], threshold: int
) -> ScoreResult:
    """score = sum(weight * points / 100), half up to an integer (never float, never round())."""
    total, lines = Decimal(0), []
    for q in questions:
        value = answers.get(q.key)
        points = points_for(q, value)
        contribution = Decimal(q.weight) * Decimal(points) / HUNDRED
        total += contribution
        lines.append(
            ScoreLine(
                key=q.key,
                prompt=q.prompt,
                type=q.type,  # type: ignore[arg-type]
                weight=q.weight,
                answered=answered(value),
                points=points,
                contribution=format(contribution, "f"),
            )
        )
    return ScoreResult(
        score=int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
        threshold=threshold,
        breakdown=lines,
    )
