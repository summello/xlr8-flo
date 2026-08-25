"""Closed error-code taxonomy for user-visible API failures."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from flo.kernel.errors.schema import ProblemFieldError


class ErrorCode(StrEnum):
    """Stable machine-readable codes; additions require a taxonomy entry."""

    BAD_REQUEST = "bad-request"
    CONFLICT = "conflict"
    FORBIDDEN = "forbidden"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    INSUFFICIENT_BUDGET = "insufficient-budget"
    INTERNAL_ERROR = "internal-error"
    METHOD_NOT_ALLOWED = "method-not-allowed"
    NOT_FOUND = "not-found"
    REQUEST_IN_FLIGHT = "request_in_flight"
    SERVICE_UNAVAILABLE = "service-unavailable"
    TOO_MANY_REQUESTS = "too-many-requests"
    UNAUTHORIZED = "unauthorized"
    UNSUPPORTED_MEDIA_TYPE = "unsupported-media-type"
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
    ErrorCode.CONFLICT: ErrorTaxonomyEntry(
        status=409,
        type_uri="https://xlr8flo.app/errors/conflict",
        title="The request conflicts with the current record",
        detail="The record changed or is not in the required state. No data was changed.",
        recovery="Refresh the record, review its current state, and try again.",
    ),
    ErrorCode.FORBIDDEN: ErrorTaxonomyEntry(
        status=403,
        type_uri="https://xlr8flo.app/errors/forbidden",
        title="You cannot perform this action",
        detail="Your account does not have permission for this action. No data was changed.",
        recovery="Ask an organization administrator for access or choose another action.",
    ),
    ErrorCode.IDEMPOTENCY_KEY_REUSED: ErrorTaxonomyEntry(
        status=422,
        type_uri="https://xlr8flo.app/errors/idempotency_key_reused",
        title="This idempotency key was already used",
        detail="The key belongs to a different request. No data was changed.",
        recovery="Use a new Idempotency-Key for a different request.",
    ),
    ErrorCode.INSUFFICIENT_BUDGET: ErrorTaxonomyEntry(
        status=409,
        type_uri="https://xlr8flo.app/errors/insufficient-budget",
        title="Insufficient available budget",
        detail="The request exceeds the available budget. No funds were reserved.",
        recovery=(
            "Reduce the requested amount, transfer funds into the project, or request an override."
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
    ErrorCode.REQUEST_IN_FLIGHT: ErrorTaxonomyEntry(
        status=409,
        type_uri="https://xlr8flo.app/errors/request_in_flight",
        title="This request is already in progress",
        detail="Another request with this Idempotency-Key is still running.",
        recovery="Wait one second, then retry with the same Idempotency-Key.",
    ),
    ErrorCode.SERVICE_UNAVAILABLE: ErrorTaxonomyEntry(
        status=503,
        type_uri="https://xlr8flo.app/errors/service-unavailable",
        title="The service is temporarily unavailable",
        detail="The request could not be completed. No data was changed.",
        recovery="Try again in a few minutes. If the problem continues, contact support.",
    ),
    ErrorCode.TOO_MANY_REQUESTS: ErrorTaxonomyEntry(
        status=429,
        type_uri="https://xlr8flo.app/errors/too-many-requests",
        title="Too many requests were sent",
        detail="The request limit was reached. No data was changed.",
        recovery="Wait before trying again.",
    ),
    ErrorCode.UNAUTHORIZED: ErrorTaxonomyEntry(
        status=401,
        type_uri="https://xlr8flo.app/errors/unauthorized",
        title="Sign-in is required",
        detail="A current sign-in is required for this request. No data was changed.",
        recovery="Sign in and try again.",
    ),
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: ErrorTaxonomyEntry(
        status=415,
        type_uri="https://xlr8flo.app/errors/unsupported-media-type",
        title="The request format is not supported",
        detail="The request body format is not supported. No data was changed.",
        recovery="Use a supported Content-Type and send the request again.",
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
        checks: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.detail = detail
        self.errors = errors
        self.checks = dict(checks) if checks is not None else None
        self.headers = dict(headers) if headers is not None else None
