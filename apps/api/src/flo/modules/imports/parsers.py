"""Strict lexical parsers shared with row validation in E08-S02."""

import re
from datetime import date
from decimal import Decimal

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.modules.imports.schemas import ColumnType


def invalid(field: str, message: str) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail="The import structure is invalid. Correct the listed fields and upload again.",
        errors=(ProblemFieldError(field=field, message=message),),
        checks={"problem": "import_structure"},
    )


def parse_value(kind: ColumnType, value: str) -> str | int | Decimal | date:
    # Code references and enum membership are validated in E08-S02.
    if kind in {"text", "code", "enum"}:
        return value
    if kind == "integer" and re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    if kind == "decimal" and re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value):
        return Decimal(value)
    if kind == "date" and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value)
    raise ValueError(f"Expected {kind} in its canonical format.")
