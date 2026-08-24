"""Closed error-code taxonomy for user-visible API failures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from flo.kernel.errors.schema import ProblemFieldError


class ErrorCode(StrEnum):
    """Stable machine-readable codes; additions require a taxonomy entry."""

    BAD_REQUEST = "bad-request"
    INSUFFICIENT_BUDGET = "insufficient-budget"
    INTERNAL_ERROR = "internal-error"
    METHOD_NOT_ALLOWED = "method-not-allowed"
    NOT_FOUND = "not-found"
    SERVICE_UNAVAILABLE = "service-unavailable"
    VALIDATION_FAILED = "validation-failed"


@dataclass(frozen=True, slots=True)
class ErrorTaxonomyEntry:
    """The immutable public contract for one error code."""

    status: int
    type_uri: str
    title: str
    detail: str
    recovery: str | None = None


ERROR_TAXONOMY: dict[ErrorCode, ErrorTaxonomyEntry] = {
    ErrorCode.BAD_REQUEST: ErrorTaxonomyEntry(
        status=400,
        type_uri="https://xlr8flo.app/errors/bad-request",
        title="The request could not be processed",
        detail="The request was not processed. No data was changed.",
        recovery="Check the request and try again.",
    ),
    ErrorCode.INSUFFICIENT_BUDGET: ErrorTaxonomyEntry(
        status=409,
        type_uri="https://xlr8flo.app/errors/insufficient-budget",
        title="Insufficient available budget",
        detail="The request exceeds the available budget. No funds were reserved.",
        recovery=(
            "Reduce the requested amount, transfer funds into the project, "
            "or request an override."
        ),
    ),
    ErrorCode.INTERNAL_ERROR: ErrorTaxonomyEntry(
        status=500,
        type_uri="https://xlr8flo.app/errors/internal-error",
        title="The request could not be completed",
        detail="An unexpected error prevented the request from completing.",
    ),
    ErrorCode.METHOD_NOT_ALLOWED: ErrorTaxonomyEntry(
        status=405,
        type_uri="https://xlr8flo.app/errors/method-not-allowed",
        title="This action is not available",
        detail="The requested action is not available for this resource. No data was changed.",
        recovery="Use one of the actions supported by this resource.",
    ),
    ErrorCode.NOT_FOUND: ErrorTaxonomyEntry(
        status=404,
        type_uri="https://xlr8flo.app/errors/not-found",
        title="Record not found",
        detail="The requested record was not found. No data was changed.",
        recovery="Check the address or return to the previous page and choose the record again.",
    ),
    ErrorCode.SERVICE_UNAVAILABLE: ErrorTaxonomyEntry(
        status=503,
        type_uri="https://xlr8flo.app/errors/service-unavailable",
        title="The service is temporarily unavailable",
        detail="The request could not be completed. No data was changed.",
        recovery="Try again in a few minutes. If the problem continues, contact support.",
    ),
    ErrorCode.VALIDATION_FAILED: ErrorTaxonomyEntry(
        status=422,
        type_uri="https://xlr8flo.app/errors/validation-failed",
        title="Some values need attention",
        detail="The request contains invalid values. Your submitted values were preserved.",
        recovery="Correct the listed values and submit the request again.",
    ),
}


class ProblemError(Exception):
    """A safe, deliberate API error backed by exactly one taxonomy code."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        detail: str | None = None,
        errors: tuple[ProblemFieldError, ...] = (),
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.detail = detail
        self.errors = errors
