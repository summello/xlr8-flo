from unittest.mock import patch
from uuid import uuid4

import pytest

from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.identity.service import register_project_scope, user_exists
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_resolver import insert_identity, service_for


@pytest.mark.parametrize("kind", ["bu", "project"])
def test_project_wrapper_registers_in_existing_transaction(kind):
    scope = Scope(uuid4())
    project, parent = uuid4(), uuid4()
    with patch("flo.modules.identity.service.RoleRepository") as repository:
        connection = repository.return_value
        register_project_scope(connection, scope, project, kind, parent)
        repository.assert_called_once_with(connection, scope)
        repository.return_value.register_scope.assert_called_once_with(
            ScopeType.PROJECT, project, ScopeType(kind), parent, roll_down=True
        )
        connection.transaction.assert_not_called()


def test_user_exists_is_scoped_to_organization(authorization_database):
    db = authorization_database
    own, foreign = insert_identity(db, "own-user"), insert_identity(db, "foreign-user")
    for org, user in [(db.org_a, own), (db.org_b, foreign)]:
        with service_for(db, Scope(org), "member-grant") as identity:
            role = identity.create_role("member", "Member")
            identity.grant_role(user, role.id, AuthorizationTarget.organization(org))
    with tenant_transaction(db.connection, Scope(db.org_a)):
        assert user_exists(db.service_connection, Scope(db.org_a), own)
        assert not user_exists(db.service_connection, Scope(db.org_a), foreign)
        assert not user_exists(db.service_connection, Scope(db.org_a), uuid4())
