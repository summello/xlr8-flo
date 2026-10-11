"""Versioned formats and independent document-number allocation.

Counters commit before the document transaction: rollbacks leave intentional gaps,
which auditors must treat as unused numbers, not missing documents.
"""

from datetime import date
from typing import Literal, cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.models import OrgRepository

type DocumentType = Literal["project", "requisition", "rfq", "award", "po", "asset"]


class NumberingService:
    def __init__(self, settings: Settings, scope: Scope) -> None:
        self.settings = settings
        self.scope = scope

    def allocate(self, scope_id: UUID, doc_type: DocumentType, on_date: date) -> str:
        """Commit one allocation on a separate connection, never reuse after rollback."""
        if self.settings.database_url is None:
            raise RuntimeError("database configuration is required for numbering")
        with psycopg.connect(self.settings.database_url.get_secret_value()) as connection:
            with tenant_transaction(cast(RlsSession, connection), self.scope):
                repo = OrgRepository(connection, self.scope)
                row = repo.execute(
                    """SELECT prefix, include_year, width, scope_id FROM numbering_format
                    WHERE org_id = %(org_id)s AND scope_id IN (%(scope_id)s, %(org_id)s)
                    AND doc_type = %(doc_type)s AND effective_from <= %(today)s
                    ORDER BY (scope_id = %(scope_id)s) DESC, effective_from DESC LIMIT 1""",
                    {"scope_id": scope_id, "doc_type": doc_type, "today": on_date},
                ).fetchone()
                prefix, include_year, width, counter_scope = row or ("PRJ", True, 4, scope_id)
                year = on_date.year if include_year else 0
                result = repo.execute(
                    """INSERT INTO numbering_counter(org_id, scope_id, doc_type, year, last_value)
                    VALUES (%(org_id)s, %(scope_id)s, %(doc_type)s, %(year)s, 1)
                    ON CONFLICT (org_id, scope_id, doc_type, year) DO UPDATE
                    SET last_value = numbering_counter.last_value + 1 RETURNING last_value""",
                    {"scope_id": counter_scope, "doc_type": doc_type, "year": year},
                ).fetchone()
                if result is None:
                    raise RuntimeError("number allocation did not return a counter")
                return (
                    str(prefix)
                    + (f"-{on_date.year}-" if include_year else "")
                    + str(result[0]).zfill(cast(int, width))
                )

    def add_format(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope_id: UUID,
        doc_type: DocumentType,
        *,
        prefix: str,
        include_year: bool,
        width: int,
        effective_from: date,
    ) -> UUID:
        """Insert a new version; no update operation is exposed."""
        if not 1 <= width <= 9:
            raise ProblemError(ErrorCode.VALIDATION_FAILED, detail="Width must be from 1 to 9.")
        with tenant_transaction(cast(RlsSession, connection), self.scope):
            repo = OrgRepository(connection, self.scope)
            if scope_id != self.scope.org_id and repo.unit(scope_id) is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            format_id = uuid4()
            repo.execute(
                """INSERT INTO numbering_format
                (id, org_id, scope_id, doc_type, prefix, include_year, width, effective_from)
                VALUES (%(id)s, %(org_id)s, %(scope_id)s, %(doc_type)s, %(prefix)s,
                %(include_year)s, %(width)s, %(effective_from)s)""",
                {
                    "id": format_id,
                    "scope_id": scope_id,
                    "doc_type": doc_type,
                    "prefix": prefix,
                    "include_year": include_year,
                    "width": width,
                    "effective_from": effective_from,
                },
            )
            return format_id
