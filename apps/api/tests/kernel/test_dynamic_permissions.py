"""Dynamic authorization uses the same identity and tenant context as require."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request

from flo.kernel.authz import install_authorization, permits
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope, use_scope


@pytest.mark.parametrize("allowed", [True, False])
def test_permits_returns_resolver_decision(allowed):
    app = FastAPI()
    user, org, record = uuid4(), uuid4(), uuid4()
    target = SimpleNamespace(scope_type="project", scope_id=record, record_id=record)
    seen = []

    class Resolver:
        def check(self, user_id, permission, resolved_target):
            seen.append((user_id, permission, resolved_target))
            return SimpleNamespace(allowed=allowed)

    @contextmanager
    def factory(scope):
        assert scope.org_id == org
        yield Resolver()

    install_authorization(app, factory)
    request = Request({"type": "http", "app": app})
    request.state.session = SimpleNamespace(identity_id=user)
    with use_scope(Scope(org)):
        assert permits(request, "project.complete", target) is allowed
    assert seen == [(user, "project.complete", target)]


@pytest.mark.parametrize("session", [None, SimpleNamespace(identity_id=uuid4())])
def test_permits_requires_identity_and_scope(session):
    request = Request({"type": "http", "app": FastAPI()})
    request.state.session = session
    with pytest.raises(ProblemError) as exc:
        permits(request, "project.complete", SimpleNamespace())
    assert exc.value.code == ErrorCode.UNAUTHORIZED
