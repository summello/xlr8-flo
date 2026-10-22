"""Table-driven master attribute contracts; percentages are stored verbatim."""

import re
from collections.abc import Callable
from typing import cast

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError

PERCENT = re.compile(r"(?:100(?:\.0{1,4})?|[0-9]{1,2}(?:\.[0-9]{1,4})?)", re.ASCII)


def percentage(value: object) -> bool:
    return isinstance(value, str) and PERCENT.fullmatch(value) is not None


def days(value: object) -> bool:
    return type(value) is int and 0 <= value <= 365


def is_bool(value: object) -> bool:
    return type(value) is bool


def code_text(value: object) -> bool:
    # ponytail: a vendor_category master kind does not exist yet; E11-S08 should check the code.
    return isinstance(value, str) and 1 <= len(value) <= 100


def choices(*values: str) -> Callable[[object], bool]:
    return lambda value: isinstance(value, str) and value in values


RULES: dict[str, dict[str, Callable[[object], bool]]] = {
    "department": {},
    "ledger_account": {
        "account_type": choices("asset", "liability", "equity", "revenue", "expense")
    },
    "uom": {"dimension": choices("count", "length", "mass", "volume", "area", "time", "other")},
    "tax_code": {"rate": percentage},
    "payment_term": {"net_days": days, "discount_percent": percentage, "discount_days": days},
    "item_category": {},
    "service_category": {},
    "vendor_contact_type": {},
    "vendor_document_type": {"required": is_bool, "category": code_text},
    "vendor_score_category": {},
    "vendor_identifier_type": {"unique": is_bool},
}
RESERVED_CONTACT_TYPES = ("billing", "service_delivery")  # built in to vendors
# Seeded per org by vendors.performance on first use; they cannot be deactivated (VEN-007).
CORE_SCORE_CATEGORIES = ("QUALITY", "PRICING", "DELIVERY", "SERVICE")
OPTIONAL = {
    "payment_term": {"discount_percent", "discount_days"},
    "vendor_document_type": {"category"},
}


def core_category_active(kind: str, code: str, active: bool, effective_to: object) -> None:
    """The four core score categories always apply: never inactive, never end-dated."""
    if kind == "vendor_score_category" and code in CORE_SCORE_CATEGORIES:
        if not active or effective_to is not None:
            raise ProblemError(
                ErrorCode.VALIDATION_FAILED,
                detail="Quality, pricing, delivery and service always apply. Keep them active.",
                errors=(ProblemFieldError(field="active", message="Must stay active."),),
                checks={"problem": "core_category"},
            )


def known_kind(kind: str) -> None:
    if kind not in RULES:
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED,
            detail="Unknown master kind. Choose a governed master-data kind.",
            errors=(ProblemFieldError(field="kind", message="Unknown kind."),),
            checks={"problem": "unknown_kind"},
        )


def validate_attributes(kind: str, attributes: dict[str, object]) -> None:
    known_kind(kind)
    rules = RULES[kind]
    required = rules.keys() - OPTIONAL.get(kind, set())
    valid = required <= attributes.keys() <= rules.keys() and all(
        rules[key](value) for key, value in attributes.items() if key in rules
    )
    if kind == "payment_term":
        discount = {"discount_percent", "discount_days"} & attributes.keys()
        valid = valid and len(discount) in (0, 2)
        if valid and discount:
            valid = cast(int, attributes["discount_days"]) <= cast(int, attributes["net_days"])
    if not valid:
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED,
            detail="Invalid attributes. Use the fields and types declared for this kind.",
            errors=(ProblemFieldError(field="attributes", message="Invalid attributes for kind."),),
            checks={"problem": "invalid_attributes"},
        )
