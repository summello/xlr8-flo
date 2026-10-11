from datetime import date

import pytest

from flo.kernel.errors import ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.org.service import NumberingService
from tests.projects.conftest import body, service, settings, unit
from tests.projects.conftest import project_db as project_db


def test_default_scope_and_format_versions(project_db):
    db = project_db
    a, b = unit(db, "A"), unit(db, "B")
    numbering = NumberingService(settings(db), Scope(db.org_a))
    first = service(db).create(body(a))
    numbering.add_format(
        db.connection,
        db.org_a,
        "project",
        prefix="ORG",
        include_year=False,
        width=3,
        effective_from=date(2000, 1, 1),
    )
    assert numbering.allocate(a.id, "project", date(2026, 1, 1)) == "ORG001"
    numbering.add_format(
        db.connection,
        a.id,
        "project",
        prefix="BU",
        include_year=True,
        width=2,
        effective_from=date(2000, 1, 1),
    )
    numbering.add_format(
        db.connection,
        a.id,
        "project",
        prefix="NEW",
        include_year=True,
        width=5,
        effective_from=date(2026, 2, 1),
    )
    assert numbering.allocate(a.id, "project", date(2026, 1, 1)) == "BU-2026-02"
    assert numbering.allocate(a.id, "project", date(2026, 2, 1)) == "NEW-2026-00003"
    assert numbering.allocate(a.id, "project", date(2027, 1, 1)) == "NEW-2027-00001"
    assert numbering.allocate(b.id, "project", date(2026, 1, 1)) == "ORG002"
    assert service(db).get(first.id).number == first.number
    assert not hasattr(numbering, "update_format")
    before = db.connection.execute("SELECT * FROM numbering_format ORDER BY id").fetchall()
    with pytest.raises(AttributeError):
        getattr(numbering, "update_format")(first.id, prefix="BAD")
    assert before == db.connection.execute("SELECT * FROM numbering_format ORDER BY id").fetchall()


def test_document_type_counters_are_independent_and_format_guards_bite(project_db):
    db = project_db
    bu = unit(db)
    numbering = NumberingService(settings(db), Scope(db.org_a))
    for doc_type in ["project", "requisition", "rfq", "award", "po", "asset"]:
        numbering.add_format(
            db.connection,
            bu.id,
            doc_type,
            prefix=doc_type,
            include_year=False,
            width=1,
            effective_from=date(2000, 1, 1),
        )
        assert numbering.allocate(bu.id, doc_type, date(2026, 1, 1)) == doc_type + "1"
        assert numbering.allocate(bu.id, doc_type, date(2027, 1, 1)) == doc_type + "2"
    for width in [0, 10]:
        with pytest.raises(ProblemError):
            numbering.add_format(
                db.connection,
                bu.id,
                "project",
                prefix="X",
                include_year=True,
                width=width,
                effective_from=date(2026, 1, 1),
            )
    from uuid import uuid4

    with pytest.raises(ProblemError):
        numbering.add_format(
            db.connection,
            uuid4(),
            "project",
            prefix="X",
            include_year=True,
            width=4,
            effective_from=date(2026, 1, 1),
        )


def test_allocation_requires_database_configuration():
    from uuid import uuid4

    from flo.kernel.config import Settings

    with pytest.raises(RuntimeError, match="database configuration"):
        NumberingService(Settings(database_url=None), Scope(uuid4())).allocate(
            uuid4(), "project", date(2026, 1, 1)
        )
