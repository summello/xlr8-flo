"""Only tenant-scoped purchase order queries."""

from collections.abc import Mapping

import psycopg

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.tenancy.context import Scope


class PurchaseOrderRepository(ScopedRepo[object]):
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(connection, scope)
        self.connection = connection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> psycopg.Cursor[tuple[object, ...]]:
        return self.connection.execute(query, self.scoped_params(params))


# The outbox owns a send's state until a provider event (E13-S10) writes `result`. A dead outbox row
# is `failed`, never `bounced`: a bounce is a provider event.
READ_STATE = (
    "COALESCE(t.result, CASE o.state WHEN 'sent' THEN 'sent' WHEN 'dead' THEN 'failed' "
    "ELSE 'queued' END)"
)
TRANSMISSION_FROM = (
    "FROM po_transmission t LEFT JOIN outbox o ON o.org_id = t.org_id AND o.id = t.outbox_id"
)
_LATEST_ATTEMPT = (
    "t.attempt = (SELECT max(m.attempt) FROM po_transmission m "
    "WHERE m.org_id = t.org_id AND m.po_id = t.po_id)"
)
NEEDS_ATTENTION = ("failed", "bounced", "complained", "address_suppressed")
# an issued PO whose latest attempt has any row that needs a person (alias `p` is the PO)
ATTENTION_SQL = (
    f"EXISTS (SELECT 1 {TRANSMISSION_FROM} WHERE t.org_id = p.org_id AND t.po_id = p.id "
    f"AND {_LATEST_ATTEMPT} AND {READ_STATE} IN {NEEDS_ATTENTION})"
)
# the latest attempt's rows for the POs in `%(ids)s`, worst first (`failed` ... `delivered`)
WORST_SENT_SQL = (
    f"SELECT DISTINCT ON (t.po_id) t.po_id, {READ_STATE}, COALESCE(t.result_at, o.sent_at) "
    f"{TRANSMISSION_FROM} WHERE t.org_id = %(org_id)s AND t.po_id = ANY(%(ids)s) "
    f"AND {_LATEST_ATTEMPT} ORDER BY t.po_id, CASE {READ_STATE} WHEN 'failed' THEN 0 "
    "WHEN 'bounced' THEN 1 WHEN 'complained' THEN 2 WHEN 'address_suppressed' THEN 3 "
    "WHEN 'queued' THEN 4 WHEN 'sent' THEN 5 ELSE 6 END, t.created_at"
)
