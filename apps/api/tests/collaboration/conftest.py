"""Real sessions, tenant documents and private in-memory storage."""

import pytest

from flo.api.collaboration import get_collaboration_storage
from flo.api.health import app
from tests.imports.test_upload import MemoryStorage
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import draft, project, unit
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db


@pytest.fixture
def collaboration_db(header_db):
    db = header_db
    store = MemoryStorage()
    app.dependency_overrides[get_collaboration_storage] = lambda: store
    yield db, store, draft(db), draft(db, org=db[2]), project(db, bu=unit(db, "PROJECT"))
