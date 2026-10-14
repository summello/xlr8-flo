"""Read-only submission checks, authoritative only under the caller's funding locks."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.money import currency_exponent, quantize
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget.service import get_balance, lock_balance, reservation_remaining
from flo.modules.org.service import MasterCodeUnusable, OrgService, assert_usable, today
from flo.modules.projects.service import ProjectService, depth_of, is_requisition_generated
from flo.modules.requisitions.models import RequisitionRepository
from flo.modules.requisitions.schemas import FundingCheck, SubmissionCheck, SubmissionProblem

Category = Literal["required", "master_data", "project", "lines", "funds", "routing"]


def check_submission(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    requisition_id: UUID,
    *,
    need: Decimal | None = None,
    also_lock: Sequence[UUID] = (),
    lock: bool,
) -> SubmissionCheck:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        # Discover the funding target without taking the document lock first.
        target = repo.execute(
            "SELECT project_id FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s",
            {"id": requisition_id},
        ).fetchone()
        if target is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        project_id = cast(UUID | None, target[0])
        if lock:
            for id in sorted(set(also_lock) | ({project_id} if project_id else set())):
                lock_balance(conn, scope, id)
        fields = (
            "title",
            "justification",
            "needed_by",
            "department_code",
            "ledger_account_code",
            "delivery_address_id",
            "bu_id",
            "project_id",
            "standalone",
            "currency",
            "estimate",
            "status",
            "reservation_entry_id",
        )
        row = repo.execute(
            "SELECT " + ",".join(fields) + " FROM requisition "
            "WHERE org_id=%(org_id)s AND id=%(id)s" + (" FOR UPDATE" if lock else ""),
            {"id": requisition_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        values = dict(zip(fields, row, strict=True))
        # A concurrent draft edit may move the funding target while we wait. Never
        # validate against an unlocked replacement; the caller retries the transaction.
        if lock and values["project_id"] != project_id:
            raise ProblemError(
                ErrorCode.CONFLICT,
                detail="The requisition changed. Reload it and retry.",
                checks={"problem": "stale_version"},
            )
        problems: list[SubmissionProblem] = []

        def add(category: Category, field: str, message: str) -> None:
            problems.append(
                SubmissionProblem(
                    category=category,
                    field=f"{category}/{field}",
                    message=message,
                )
            )

        on_date = today()
        for field, message in (
            ("title", "Enter a title"),
            ("justification", "Explain why it is needed"),
            ("needed_by", "Enter the date it is needed by"),
            ("department_code", "Choose a department"),
            ("ledger_account_code", "Choose a ledger account"),
            ("delivery_address_id", "Choose a delivery location"),
        ):
            value = values[field]
            if value is None or (isinstance(value, str) and not value.strip()):
                add("required", field, message)
        if values["needed_by"] is not None and cast(date, values["needed_by"]) < on_date:
            add("required", "needed_by", "The needed-by date is in the past")
        standalone = bool(values["standalone"])
        if standalone and values["bu_id"] is None:
            add("required", "bu_id", "Choose a business unit")
        if standalone and (
            repo.execute(
                "SELECT 1 FROM requisition_sponsor WHERE org_id=%(org_id)s AND "
                "requisition_id=%(id)s LIMIT 1",
                {"id": requisition_id},
            ).fetchone()
            is None
        ):
            add("required", "sponsors", "Name at least one sponsor")
        org = OrgService(conn, scope)

        def master(category: Category, field: str, kind: str, code: str, label: str) -> None:
            try:
                assert_usable(conn, scope, kind, code, on_date)
            except MasterCodeUnusable as exc:
                reason: str = exc.reason
                if reason == "not_effective":
                    # The helper combines the two date failures; retrieve the scoped
                    # master reference to preserve actionable page-spec wording.
                    page = org.list_master(kind, q=code)
                    ref = next((r for r in page.rows if r.code == code.strip().upper()), None)
                    while ref is None and page.next_cursor is not None:
                        page = org.list_master(kind, q=code, cursor=page.next_cursor)
                        ref = next((r for r in page.rows if r.code == code.strip().upper()), None)
                    reason = (
                        "not effective yet"
                        if ref and ref.effective_from > on_date
                        else "no longer effective"
                    )
                noun = "account" if kind == "ledger_account" else label.lower()
                add(category, field, f"{label} {exc.code} is {reason}. Choose an active {noun}")

        for field, kind, label in (
            ("department_code", "department", "Department"),
            ("ledger_account_code", "ledger_account", "Ledger account"),
        ):
            if values[field]:
                master("master_data", field, kind, cast(str, values[field]), label)
        bu_id = cast(UUID, values["bu_id"])
        if values["delivery_address_id"] is not None:
            addresses = [
                a
                for unit in org.unit_chain(bu_id)
                for a in org.list_addresses(unit, "ship_to", on_date)
            ]
            if not any(a.id == values["delivery_address_id"] for a in addresses):
                add(
                    "master_data",
                    "delivery_address_id",
                    "Delivery location is no longer available. Choose another",
                )
        lines = repo.execute(
            "SELECT line_no,description,quantity,unit_price,uom_code,category_code,line_type "
            "FROM requisition_line WHERE org_id=%(org_id)s "
            "AND requisition_id=%(id)s ORDER BY line_no",
            {"id": requisition_id},
        ).fetchall()
        for n, _, _, _, uom, category, line_kind in lines:
            if uom:
                master("lines", f"{n}/uom_code", "uom", cast(str, uom), "Unit")
            if category:
                master(
                    "lines",
                    f"{n}/category_code",
                    f"{line_kind}_category",
                    cast(str, category),
                    "Category",
                )
        currency = cast(str, values["currency"])
        if not standalone and project_id is not None:
            project = ProjectService(conn, scope).get(project_id)
            if project.status != "active":
                add(
                    "project",
                    "project_id",
                    "The chosen project cannot take a requisition: it is "
                    + project.status.replace("_", " "),
                )
            if is_requisition_generated(conn, scope, project_id):
                add("project", "project_id", "The chosen project cannot take a requisition")
            if depth_of(conn, scope, project_id) >= 5:
                add(
                    "project",
                    "project_id",
                    "The chosen project is at the five-level limit, so approval "
                    "could not create its sub-project. Choose a shallower project",
                )
            if project.currency != currency:
                add(
                    "project",
                    "project_id",
                    "The project's currency changed. Reopen the requisition",
                )
        elif not org.get_unit(bu_id).active:
            add("project", "bu_id", "Choose an active business unit")
        if not lines:
            add("lines", "lines", "Add at least one line")
        for n, description, quantity, price, uom, category, line_kind in lines:
            for field, value, label in (
                ("description", description, "a description"),
                ("quantity", quantity, "a quantity"),
                ("unit_price", price, "a unit price"),
            ):
                if (
                    value is None
                    or (isinstance(value, str) and not value.strip())
                    or (isinstance(value, Decimal) and value <= 0)
                ):
                    add("lines", f"{n}/{field}", f"Line {n} needs {label}")
        funding = None
        if not standalone and project_id is not None:
            balance = get_balance(conn, scope, project_id)
            held = (
                reservation_remaining(conn, scope, cast(int, values["reservation_entry_id"]))
                if values["reservation_entry_id"] is not None
                else None
            )
            reserved = (
                held.remaining if held is not None and held.project_id == project_id else Decimal(0)
            )
            estimate = cast(Decimal, values["estimate"])
            remaining_need = estimate - reserved if need is None else need
            after = balance.available - remaining_need
            funding = FundingCheck(
                available=format(balance.available, ".4f"),
                this_requisition=format(estimate, ".4f"),
                already_reserved=format(reserved, ".4f"),
                available_after=format(after, ".4f"),
                currency=currency,
                advisory=not lock,
            )
            # Mirror the ledger: a posting that lowers nothing is never refused, so a problem
            # needs an amount still to reserve; the policy comes from the funding row's unit.
            if (
                remaining_need > 0
                and after < 0
                and balance.currency == currency
                and org.effective_setting(balance.bu_id, "allow_negative_budget").value is not True
            ):
                shortfall = format(quantize(-after, currency), f",.{currency_exponent(currency)}f")
                add(
                    "funds",
                    "estimate",
                    f"Over the available funds by {shortfall} {currency}. "
                    "Reduce the estimate or ask for a budget transfer",
                )
        return SubmissionCheck(
            ready=values["status"] == "draft" and not problems, problems=problems, funding=funding
        )
