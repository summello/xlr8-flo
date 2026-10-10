from unittest.mock import patch
from uuid import uuid4

from flo.kernel.tenancy.context import Scope
from flo.modules.identity.models import ScopeType
from flo.modules.identity.service import register_business_unit_scope


def test_business_unit_wrapper_registers_directly_under_org_in_existing_transaction() -> None:
    scope = Scope(uuid4())
    unit_id = uuid4()
    with patch("flo.modules.identity.service.RoleRepository") as repository:
        connection = repository.return_value
        register_business_unit_scope(connection, scope, unit_id)
        repository.assert_called_once_with(connection, scope)
        repository.return_value.register_scope.assert_called_once_with(
            ScopeType.BU, unit_id, ScopeType.ORG, scope.org_id, roll_down=True
        )
        connection.transaction.assert_not_called()
