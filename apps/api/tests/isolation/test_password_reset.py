from __future__ import annotations

import pytest
from pydantic import ValidationError

from flo.api.auth import PasswordResetCompletion, PasswordResetRequest
from flo.kernel.idempotency.middleware import IDEMPOTENCY_KEY_EXEMPT_PATHS


def test_password_reset_contracts_have_no_tenant_selector_and_fail_if_fields_go_missing() -> None:
    request_fields = set(PasswordResetRequest.model_fields)
    completion_fields = set(PasswordResetCompletion.model_fields)

    missing_request = {"email"} - request_fields
    missing_completion = {"token", "password"} - completion_fields
    assert not missing_request, f"MISSING reset-request fields: {sorted(missing_request)}"
    assert not missing_completion, f"MISSING reset fields: {sorted(missing_completion)}"
    assert request_fields == {"email"}
    assert completion_fields == {"token", "password"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PasswordResetRequest.model_validate(
            {"email": "person@example.test", "org_id": "planted-tenant-selector"}
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PasswordResetCompletion.model_validate(
            {
                "token": "opaque",
                "password": "the replacement safe passphrase",
                "org_id": "planted-tenant-selector",
            }
        )


def test_pre_authentication_reset_routes_are_explicit_idempotency_scope_exceptions() -> None:
    required = {"/api/v1/auth/reset", "/api/v1/auth/reset-request"}
    missing = required - IDEMPOTENCY_KEY_EXEMPT_PATHS
    assert not missing, f"MISSING pre-auth idempotency exceptions: {sorted(missing)}"
