from __future__ import annotations

import pytest

from tests.authz.conftest import AuthorizationDatabase

from .test_units import admin, create, request


def test_precedence_replaces_value_and_clear_falls_back_one_level(
    org_database: AuthorizationDatabase,
) -> None:
    db = org_database
    admin(db)
    parent = create(db, "P")
    child = create(db, "C", parent.id, kind="ou")
    key = "fiscal_year_start_month"

    def effective() -> dict[str, object]:
        response = request(db, "GET", f"/units/{child.id}/settings/{key}/effective")
        assert response.status_code == 200
        return response.json()

    assert effective() == {
        "value": 1,
        "source": {"scope": "default", "unit_id": None, "unit_code": None},
    }
    for unit, value in [(None, 4), (parent.id, 7), (child.id, 10)]:
        assert (
            request(
                db,
                "PUT",
                f"/settings/{key}",
                {"unit_id": str(unit) if unit else None, "value": value},
            ).status_code
            == 200
        )
    assert effective() == {
        "value": 10,
        "source": {"scope": "unit", "unit_id": str(child.id), "unit_code": "C"},
    }
    assert request(db, "DELETE", f"/settings/{key}?unit_id={child.id}").status_code == 204
    assert effective() == {
        "value": 7,
        "source": {"scope": "unit", "unit_id": str(parent.id), "unit_code": "P"},
    }
    assert request(db, "DELETE", f"/settings/{key}?unit_id={parent.id}").status_code == 204
    assert effective() == {
        "value": 4,
        "source": {"scope": "org", "unit_id": None, "unit_code": None},
    }
    assert request(db, "DELETE", f"/settings/{key}").status_code == 204
    assert effective()["source"]["scope"] == "default"


def test_put_foreign_unit(org_database: AuthorizationDatabase) -> None:
    db = org_database
    admin(db)
    foreign = create(db, "F", org=db.org_b)
    assert (
        request(
            db, "PUT", "/settings/funding_mode", {"unit_id": str(foreign.id), "value": "roll_up"}
        ).status_code
        == 404
    )


def test_delete_foreign_unit(org_database: AuthorizationDatabase) -> None:
    db = org_database
    admin(db)
    foreign = create(db, "F", org=db.org_b)
    assert request(db, "DELETE", f"/settings/funding_mode?unit_id={foreign.id}").status_code == 404


def test_effective_foreign_unit(org_database: AuthorizationDatabase) -> None:
    db = org_database
    admin(db)
    foreign = create(db, "F", org=db.org_b)
    assert (
        request(db, "GET", f"/units/{foreign.id}/settings/funding_mode/effective").status_code
        == 404
    )


@pytest.mark.parametrize("unit_override", [False, True])
def test_equal_put_and_absent_delete_do_not_audit(
    org_database: AuthorizationDatabase, unit_override: bool
) -> None:
    db = org_database
    admin(db)
    unit = create(db, "U")
    suffix = f"?unit_id={unit.id}" if unit_override else ""
    body = {"value": False, "unit_id": str(unit.id) if unit_override else None}
    for _ in range(2):
        assert request(db, "PUT", "/settings/allow_negative_budget", body).status_code == 200
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action = 'org_setting.set'"
    ).fetchone() == (1,)
    for _ in range(2):
        assert request(db, "DELETE", "/settings/allow_negative_budget" + suffix).status_code == 204
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action = 'org_setting.clear'"
    ).fetchone() == (1,)


@pytest.mark.parametrize("method", ["PUT", "DELETE", "GET"])
def test_unknown_key_guard_rejects_violation(
    org_database: AuthorizationDatabase, method: str
) -> None:
    db = org_database
    admin(db)
    unit = create(db, "U")
    path = f"/units/{unit.id}/settings/nope/effective" if method == "GET" else "/settings/nope"
    response = request(db, method, path, {"value": True} if method == "PUT" else None)
    assert response.status_code == 422
    assert response.json()["checks"]["problem"] == "unknown_key"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("allow_negative_budget", "false"),
        ("allow_negative_budget", 0),
        ("allow_negative_budget", None),
        ("funding_mode", "banana"),
        ("funding_mode", True),
        ("fiscal_year_start_month", 0),
        ("fiscal_year_start_month", 13),
        ("fiscal_year_start_month", "4"),
        ("fiscal_year_start_month", True),
    ],
)
def test_setting_values_are_type_checked(
    org_database: AuthorizationDatabase, key: str, value: object
) -> None:
    db = org_database
    admin(db)
    response = request(db, "PUT", f"/settings/{key}", {"unit_id": None, "value": value})
    assert response.status_code == 422
    assert response.json()["checks"]["problem"] == "invalid_setting_value"


@pytest.mark.parametrize(
    ("key", "value"),
    [("allow_negative_budget", True), ("funding_mode", "roll_up"), ("fiscal_year_start_month", 4)],
)
def test_valid_setting_values_are_accepted(
    org_database: AuthorizationDatabase, key: str, value: object
) -> None:
    db = org_database
    admin(db)
    response = request(db, "PUT", f"/settings/{key}", {"unit_id": None, "value": value})
    assert response.status_code == 200
