"""E22-S13 (USR-003, ACC-004): one list-scope rule, the same roll-down the point check applies."""

from __future__ import annotations

from uuid import uuid4

import pytest

from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_resolver import (
    check,
    insert_identity,
    resolver_for,
    service_for,
)

PERMISSION = "requisition.view"


def tree(database, grants):
    """org > unit A > P > (P1 rolls, P2 does not > P3 rolls); unit B > Q; returns ids and users."""
    scope = Scope(database.org_a)
    ids = {n: uuid4() for n in ("a", "b", "p", "p1", "p2", "p3", "q")}
    users = {name: insert_identity(database, name) for name in grants}
    with service_for(database, scope, "granted-scopes-setup") as service:
        role = service.create_role("list-reader", "List reader")
        service.grant_permission(role.id, PERMISSION)
        service.register_scope(ScopeType.BU, ids["a"], ScopeType.ORG, database.org_a)
        service.register_scope(ScopeType.BU, ids["b"], ScopeType.ORG, database.org_a)
        edges = (
            ("p", ScopeType.BU, "a", True),
            ("p1", ScopeType.PROJECT, "p", True),
            ("p2", ScopeType.PROJECT, "p", False),
            ("p3", ScopeType.PROJECT, "p2", True),
            ("q", ScopeType.BU, "b", True),
        )
        for child, kind, parent, roll in edges:
            service.register_scope(ScopeType.PROJECT, ids[child], kind, ids[parent], roll_down=roll)
        for name, where in grants.items():
            if where is None:
                continue
            kind = ScopeType.PROJECT if where in ("p", "q") else ScopeType.BU
            target = (
                AuthorizationTarget.organization(database.org_a)
                if where == "org"
                else AuthorizationTarget(kind, ids[where])
            )
            service.grant_role(users[name], role.id, target)
    return scope, ids, users


def granted(database, scope, user):
    with tenant_transaction(database.connection, scope):
        return resolver_for(database, scope).granted_scopes(user, PERMISSION)


def test_unit_grant_is_exactly_the_unit_and_every_project_beneath_it_whatever_roll_down_says(
    authorization_database,
):
    db = authorization_database
    scope, ids, users = tree(db, {"unit": "a", "none": None})
    got = granted(db, scope, users["unit"])
    assert got.bu_ids == {ids["a"]}  # units never roll down to units: sibling B is absent
    assert got.project_ids == {
        ids[n] for n in ("p", "p1", "p2", "p3")
    }  # incl. across the non-rolling edge
    assert not got.org_wide
    assert not granted(db, scope, users["none"])  # falsy: the routes answer an empty page


def test_project_grant_covers_sub_projects_only_across_rolling_edges(authorization_database):
    db = authorization_database
    scope, ids, users = tree(db, {"project": "p"})
    got = granted(db, scope, users["project"])
    assert got.project_ids == {ids["p"], ids["p1"]}  # p2 is a non-rolling edge, p3 is beneath it
    assert got.bu_ids == frozenset() and not got.org_wide


def test_org_grant_is_org_wide(authorization_database):
    db = authorization_database
    scope, _, users = tree(db, {"org": "org"})
    assert granted(db, scope, users["org"]).org_wide


@pytest.mark.parametrize("who", ["unit", "project", "org", "sibling", "none"])
def test_the_list_set_and_the_point_check_agree_on_every_project_and_unit(
    authorization_database, who
):
    db = authorization_database
    scope, ids, users = tree(
        db, {"unit": "a", "project": "p", "org": "org", "sibling": "b", "none": None}
    )
    got = granted(db, scope, users[who])
    for name in ("p", "p1", "p2", "p3", "q"):
        _, allowed = check(
            db, scope, users[who], PERMISSION, AuthorizationTarget(ScopeType.PROJECT, ids[name])
        )
        assert allowed == (got.org_wide or ids[name] in got.project_ids), (who, name)
    for name in ("a", "b"):
        _, allowed = check(
            db, scope, users[who], PERMISSION, AuthorizationTarget(ScopeType.BU, ids[name])
        )
        # add_evaluator's may_view: org grant, the RFQ's unit, never a sibling unit, never none
        assert allowed == (got.org_wide or ids[name] in got.bu_ids), (who, name)
