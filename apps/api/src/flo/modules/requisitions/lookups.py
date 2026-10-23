"""Scoped options for the requisition form's selects (XFN-009)."""

from collections.abc import Callable
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.service import OrgService, today
from flo.modules.projects.service import ProjectService
from flo.modules.requisitions.models import RequisitionRepository
from flo.modules.requisitions.schemas import LookupOption, LookupRows, invalid

MASTER_KINDS = ("department", "ledger_account", "uom", "item_category", "service_category")
KINDS = ("project", "bu", "delivery_address", "user", *MASTER_KINDS)
FIVE_LEVELS = 5
# The router builds authorization targets (modules never import identity); the service
# only asks "may the caller raise a requisition in this project / unit?".
CanRaise = Callable[[UUID], bool]


class LookupService:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        can_raise_in_project: CanRaise,
        can_raise_in_unit: CanRaise,
        can_request_in_unit: CanRaise | None = None,
    ) -> None:
        self.can_request_in_unit = can_request_in_unit or can_raise_in_unit
        self.connection, self.scope = connection, scope
        self.can_raise_in_project, self.can_raise_in_unit = can_raise_in_project, can_raise_in_unit
        self.repo = RequisitionRepository(connection, scope)

    def options(self, kind: str, q: str, bu_id: UUID | None, limit: int) -> LookupRows:
        if kind not in KINDS:
            raise invalid("kind", "Choose a supported lookup.", "unknown_lookup")
        if not 1 <= limit <= 50:
            raise invalid("limit", "Choose a limit from 1 to 50.")
        needle = q.strip().casefold()
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            if kind == "project":
                return LookupRows(rows=self._projects(needle, limit))
            if kind == "bu":
                return LookupRows(rows=self._units(needle, limit))
            if kind == "user":
                return LookupRows(rows=self._users(needle, limit))
            if kind == "delivery_address":
                return LookupRows(rows=self._addresses(needle, bu_id, limit))
            page = OrgService(self.connection, self.scope).list_master(
                kind, q=q.strip(), active=True, as_of=today(), page_size=limit
            )
            return LookupRows(
                rows=[LookupOption(value=r.code, label=f"{r.code} {r.name}") for r in page.rows]
            )

    def _projects(self, needle: str, limit: int) -> list[LookupOption]:
        service = ProjectService(self.connection, self.scope)
        # ponytail: permits is evaluated per candidate over the first 50 matches;
        # move to a resolver-side scope filter beyond roughly 200 active projects.
        page = service.list(q=needle, status="active", page_size=50)
        rows: list[LookupOption] = []
        for project in page.rows:
            if not self.can_raise_in_project(project.id):
                continue
            disabled, note = False, None
            if project.generated_by_requisition_id is not None:
                source = self.repo.execute(
                    "SELECT number FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s",
                    {"id": project.generated_by_requisition_id},
                ).fetchone()
                disabled = True
                note = f"created from {source[0]}" if source else "created from a requisition"
            elif service.depth_of(project.id) >= FIVE_LEVELS:
                disabled, note = True, "at the five-level limit"
            rows.append(
                LookupOption(
                    value=str(project.id),
                    label=f"{project.number} {project.name}",
                    disabled=disabled,
                    note=note,
                    codes_expired=project.codes_expired,
                )
            )
            if len(rows) == limit:
                break
        return rows

    def _units(self, needle: str, limit: int) -> list[LookupOption]:
        # ponytail: first 200 active units; same upgrade path as the project lookup.
        units = OrgService(self.connection, self.scope).list_units(
            kind=None, active=True, cursor=None, page_size=200
        )
        rows: list[LookupOption] = []
        for unit in units.rows:
            if needle and not (
                unit.code.casefold().startswith(needle) or needle in unit.name.casefold()
            ):
                continue
            # Only a Project Request draws on a bare unit, so this is its permission (D-M2-18).
            if self.can_request_in_unit(unit.id):
                rows.append(LookupOption(value=str(unit.id), label=f"{unit.code} {unit.name}"))
            if len(rows) == limit:
                break
        return rows

    def _users(self, needle: str, limit: int) -> list[LookupOption]:
        """Active members of the organization, for naming sponsors."""
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        found = self.repo.execute(
            "SELECT i.id,i.email,i.display_name FROM identity i "
            "WHERE i.status='active' AND EXISTS ("
            "SELECT 1 FROM user_role r WHERE r.org_id=%(org_id)s AND r.user_id=i.id) "
            "AND (lower(i.email) LIKE %(like)s OR lower(i.display_name) LIKE %(like)s) "
            "ORDER BY lower(i.email) LIMIT %(limit)s",
            {"like": "%" + escaped + "%", "limit": limit},
        ).fetchall()
        return [
            LookupOption(value=str(r[0]), label=str(r[2] or r[1]), note=str(r[1]) if r[2] else None)
            for r in found
        ]

    def _addresses(self, needle: str, bu_id: UUID | None, limit: int) -> list[LookupOption]:
        if bu_id is None:
            raise invalid("bu_id", "Choose a unit first.", "bu_required")
        org = OrgService(self.connection, self.scope)
        chain = org.unit_chain(bu_id)  # 404 for an unknown or foreign unit
        if not self.can_raise_in_unit(bu_id):
            raise ProblemError(ErrorCode.NOT_FOUND)
        rows: list[LookupOption] = []
        for unit in chain:
            for address in org.list_addresses(unit, "ship_to", today()):
                label = f"{address.line1}, {address.city} {address.country}"
                if needle and needle not in label.casefold():
                    continue
                rows.append(LookupOption(value=str(address.id), label=label))
        return rows[:limit]
