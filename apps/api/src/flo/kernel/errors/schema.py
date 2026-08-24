"""RFC 9457-compatible problem-detail response models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProblemFieldError(BaseModel):
    """One input error mapped to a form-compatible field path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ProblemDetails(BaseModel):
    """Problem details, including the support-safe request correlation identifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str | None = None
    title: str | None = None
    status: int | None = Field(default=None, ge=400, le=599)
    detail: str | None = None
    instance: str | None = None
    correlation_id: str = Field(min_length=1)
    recovery: str | None = None
    errors: tuple[ProblemFieldError, ...] | None = None
    checks: dict[str, str] | None = None

    @model_validator(mode="after")
    def require_recovery_for_client_errors(self) -> ProblemDetails:
        """Enforce the UX recovery contract for every client error."""

        if self.status is not None and 400 <= self.status < 500:
            if self.recovery is None or not self.recovery.strip():
                raise ValueError("recovery is required for client errors")
        return self
