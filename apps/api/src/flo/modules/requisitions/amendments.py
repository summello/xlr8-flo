"""Amendments to an approved requisition: the approved version stays in force until approved."""

import copy
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.service import approval_state, start, withdraw
from flo.modules.budget.service import lock_balance, reservation_remaining
from flo.modules.identity.service import AuthorizationConnection, AuthorizationResolver
from flo.modules.org.service import MasterCodeUnusable, OrgService, assert_usable, today
from flo.modules.requisitions import revisions
from flo.modules.requisitions.catalogue import CatalogueService
from flo.modules.requisitions.lifecycle import check_version, read_row, routing_check
from flo.modules.requisitions.lines import LIMIT, LineService
from flo.modules.requisitions.models import RequisitionRepository
from flo.modules.requisitions.schemas import (
    AmendmentHeader,
    AmendmentLineRead,
    AmendmentPut,
    AmendmentRead,
    AmendmentSubmit,
    SubmissionCheck,
    SubmissionInvalid,
    SubmissionProblem,
    invalid,
)
from flo.modules.requisitions.service import RequisitionService
from flo.modules.requisitions.validation import check_submission

type Conn = psycopg.Connection[tuple[object, ...]]

REQUIRED = (
    ("title", "Enter a title"),
    ("justification", "Explain why it is needed"),
    ("needed_by", "Enter the date it is needed by"),
    ("department_code", "Choose a department"),
    ("ledger_account_code", "Choose a ledger account"),
    ("delivery_address_id", "Choose a delivery location"),
)


def to_read(revision: dict[str, object]) -> AmendmentRead:
    snap = cast(revisions.Snapshot, revision["snapshot"])
    return AmendmentRead(
        revision_no=cast(int, revision["revision_no"]),
        status=cast(str, revision["status"]),  # type: ignore[arg-type]
        version=cast(int, revision["version"]),
        header=AmendmentHeader.model_validate(snap["header"]),
        lines=[AmendmentLineRead.model_validate(line) for line in snap["lines"]],
        estimate=snap["estimate"],
        currency=snap["header"]["currency"],
    )


def _amendment(
    repo: RequisitionRepository, id: UUID, revision_no: int, *, lock: bool = False
) -> dict[str, object]:
    revision = revisions.revision_row(repo, id, revision_no, lock=lock)
    if revision["kind"] != "amendment":
        raise ProblemError(ErrorCode.NOT_FOUND)
    return revision


def _check_revision_version(revision: dict[str, object], version: str | None) -> None:
    if version is None:
        raise ProblemError(
            ErrorCode.BAD_REQUEST, detail="Supply If-Match with the amendment version."
        )
    try:
        expected = int(version.strip('"'))
    except ValueError as exc:
        raise ProblemError(
            ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
        ) from exc
    if revision["version"] != expected:
        raise conflict("stale_version", "The amendment changed. Reload it and retry.")


def _audit(
    conn: Conn, scope: Scope, actor_id: UUID, action: str, id: UUID, after: dict[str, object]
) -> None:
    RequisitionService(conn, scope, actor_id)._audit(action, id, None, after)


def current(conn: Conn, scope: Scope, id: UUID) -> AmendmentRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        row = read_row(repo, id)
        revision = revisions.open_amendment(repo, id)
        if revision is None or row["status"] != "sourcing_in_progress":
            raise ProblemError(ErrorCode.NOT_FOUND)
        return to_read(revision)


def create(
    conn: Conn, scope: Scope, id: UUID, version: str | None, actor_id: UUID
) -> AmendmentRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        # The requisition row lock serializes two concurrent opens: the second sees the first.
        row = read_row(repo, id, lock=True)
        check_version(row, version)
        if row["status"] != "sourcing_in_progress":
            raise conflict("invalid_transition", "Only an approved requisition can be amended.")
        revisions.ensure_no_open(repo, id)
        try:
            with conn.transaction():
                revision = revisions.insert_revision(
                    repo,
                    id,
                    "amendment",
                    "draft",
                    revisions.live_snapshot(conn, scope, id),
                    actor_id,
                    None,
                )
        except psycopg.errors.UniqueViolation as exc:
            raise conflict(
                "amendment_open", "This requisition already has an open amendment."
            ) from exc
        _audit(
            conn,
            scope,
            actor_id,
            "requisition.amendment.open",
            id,
            {"revision_no": revision["revision_no"]},
        )
        return to_read(revision)


def _four(value: object) -> str:
    return format(Decimal(str(value)), ".4f")


def put(
    conn: Conn,
    scope: Scope,
    id: UUID,
    revision_no: int,
    body: AmendmentPut,
    version: str | None,
    actor_id: UUID,
) -> AmendmentRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        row = read_row(repo, id)
        revision = _amendment(repo, id, revision_no, lock=True)
        _check_revision_version(revision, version)
        if row["status"] != "sourcing_in_progress":
            raise conflict(
                "invalid_transition",
                "This requisition is no longer approved, so its amendment cannot be edited.",
            )
        if revision["status"] != "draft":
            raise conflict("not_editable", "Only a draft amendment can be edited.")
        snap = copy.deepcopy(cast(revisions.Snapshot, revision["snapshot"]))
        header = snap["header"]
        changes = body.header.model_dump(exclude_unset=True)
        if "project_id" in changes:
            if changes.pop("project_id") != (
                UUID(header["project_id"]) if header["project_id"] else None
            ):
                raise invalid(
                    "project_id",
                    "To move it to another project, withdraw it and raise a new requisition.",
                    "project_change_blocked",
                )
        for field in ("department_code", "ledger_account_code"):
            if field in changes:
                value = changes.pop(field)
                if (value or "").strip().upper() != (header[field] or "").strip().upper():
                    raise invalid(
                        field,
                        "Codes cannot be changed by amendment. "
                        "Raise a transfer or adjustment request.",
                        "codes_change_blocked",
                    )
        if "title" in changes and changes["title"] is None:
            raise invalid("title", "Supply a title.")
        coded = ("department_code", "ledger_account_code", "delivery_address_id")
        values: dict[str, object] = {
            "department_code": header["department_code"],
            "ledger_account_code": header["ledger_account_code"],
            "delivery_address_id": UUID(header["delivery_address_id"])
            if header["delivery_address_id"]
            else None,
            "bu_id": UUID(header["bu_id"]),
        } | {key: value for key, value in changes.items() if key in coded}
        service = LineService(conn, scope, actor_id)
        service._validate(values)
        for name in ("title", "justification"):
            if name in changes:
                header[name] = changes[name]
        if "needed_by" in changes:
            header["needed_by"] = changes["needed_by"].isoformat() if changes["needed_by"] else None
        header["department_code"] = values["department_code"]
        header["ledger_account_code"] = values["ledger_account_code"]
        header["delivery_address_id"] = (
            str(values["delivery_address_id"]) if values["delivery_address_id"] else None
        )
        before = {line["line_id"]: line for line in snap["lines"]}
        lines: list[revisions.Snapshot] = []
        seen: set[UUID] = set()
        for index, line in enumerate(body.lines):
            if line.line_id is not None and (
                str(line.line_id) not in before or line.line_id in seen
            ):
                raise invalid(f"lines/{index}/line_id", "Choose an existing, distinct line.")
            key = line.line_id or uuid4()
            seen.add(key)
            built = service._values(line, header["currency"], index, UUID(header["bu_id"]))
            item = built["catalogue_item_id"]
            lines.append(
                {
                    "line_id": str(key),
                    "line_no": before[str(key)]["line_no"] if str(key) in before else None,
                    "line_type": built["line_type"],
                    "description": built["description"],
                    "quantity": _four(built["quantity"]),
                    "uom_code": built["uom_code"],
                    "unit_price": _four(built["unit_price"]),
                    "amount": _four(built["amount"]),
                    "category_code": built["category_code"],
                    "catalogue_item_id": str(item) if item else None,
                }
            )
        total = sum((Decimal(line["amount"]) for line in lines), Decimal(0))
        if total > LIMIT:
            raise invalid(
                "lines/0/quantity",
                "The estimate is too large. Reduce quantities or prices.",
                "amount_too_large",
            )
        snap["lines"], snap["estimate"] = lines, _four(total)
        updated = repo.execute(
            "UPDATE requisition_revision SET snapshot=%(snap)s,material_digest=%(digest)s,"
            "version=version+1 WHERE org_id=%(org_id)s AND id=%(id)s RETURNING "
            + ",".join(revisions.REVISION_FIELDS),
            {
                "id": revision["id"],
                "snap": Jsonb(snap),
                "digest": revisions.snapshot_digest(snap),
            },
        ).fetchone()
        assert updated is not None
        result = dict(zip(revisions.REVISION_FIELDS, updated, strict=True))
        _audit(
            conn,
            scope,
            actor_id,
            "requisition.amendment.update",
            id,
            {"revision_no": revision_no, "estimate": snap["estimate"]},
        )
        return to_read(result)


def delete(conn: Conn, scope: Scope, id: UUID, revision_no: int, actor_id: UUID) -> None:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        read_row(repo, id)
        revision = _amendment(repo, id, revision_no, lock=True)
        if revision["status"] != "draft":
            raise conflict("not_editable", "Only a draft amendment can be deleted.")
        repo.execute(
            "DELETE FROM requisition_revision WHERE org_id=%(org_id)s AND id=%(id)s",
            {"id": revision["id"]},
        )
        _audit(
            conn,
            scope,
            actor_id,
            "requisition.amendment.delete",
            id,
            {"revision_no": revision_no},
        )


def _shape_problems(
    snap: revisions.Snapshot, base: revisions.Snapshot | None
) -> list[SubmissionProblem]:
    problems: list[SubmissionProblem] = []
    header = snap["header"]
    for field, message in REQUIRED:
        value = header[field]
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(
                SubmissionProblem(category="required", field=f"required/{field}", message=message)
            )
    changed = base is None or base["header"]["needed_by"] != header["needed_by"]
    if changed and header["needed_by"] and date.fromisoformat(header["needed_by"]) < today():
        problems.append(
            SubmissionProblem(
                category="required",
                field="required/needed_by",
                message="The needed-by date is in the past",
            )
        )
    if not snap["lines"]:
        problems.append(
            SubmissionProblem(
                category="lines", field="lines/lines", message="Add at least one line"
            )
        )
    return problems


def _master_problems(conn: Conn, scope: Scope, snap: revisions.Snapshot) -> list[SubmissionProblem]:
    """The proposal's codes, address and catalogue items against today's master data."""
    problems: list[SubmissionProblem] = []
    header = snap["header"]
    on_date = today()

    def usable(field: str, kind: str, code: str | None, label: str) -> None:
        if not code:
            return
        try:
            assert_usable(conn, scope, kind, code, on_date)
        except MasterCodeUnusable as exc:
            problems.append(
                SubmissionProblem(
                    category="master_data",
                    field=f"master_data/{field}",
                    message=f"{label} {exc.code} is {exc.reason}. Choose an active one",
                )
            )

    usable("department_code", "department", header["department_code"], "Department")
    usable("ledger_account_code", "ledger_account", header["ledger_account_code"], "Ledger account")
    bu_id = UUID(header["bu_id"])
    if header["delivery_address_id"]:
        org = OrgService(conn, scope)
        addresses = [
            a
            for unit in org.unit_chain(bu_id)
            for a in org.list_addresses(unit, "ship_to", on_date)
        ]
        if not any(str(a.id) == header["delivery_address_id"] for a in addresses):
            problems.append(
                SubmissionProblem(
                    category="master_data",
                    field="master_data/delivery_address_id",
                    message="Delivery location is no longer available. Choose another",
                )
            )
    catalogue = CatalogueService(conn, scope)
    for line in snap["lines"]:
        n = line["line_no"] or "new"
        usable(f"lines/{n}/uom_code", "uom", line["uom_code"], "Unit")
        usable(
            f"lines/{n}/category_code",
            f"{line['line_type']}_category",
            line["category_code"],
            "Category",
        )
        if line["catalogue_item_id"]:
            item = catalogue.item(UUID(line["catalogue_item_id"]), bu_id)
            if item is None or item.currency != header["currency"]:
                problems.append(
                    SubmissionProblem(
                        category="lines",
                        field=f"lines/{n}/catalogue_item_id",
                        message=f"Line {n} uses a catalogue item that is no longer available",
                    )
                )
    return problems


def submit(
    conn: Conn,
    scope: Scope,
    id: UUID,
    revision_no: int,
    body: AmendmentSubmit,
    version: str | None,
    actor_id: UUID,
) -> None:
    """Lock order: project_balance, requisition, amendment row; `start` is the last step."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        _amendment(repo, id, revision_no)
        before = read_row(repo, id)
        held = (
            reservation_remaining(conn, scope, cast(int, before["reservation_entry_id"]))
            if before["reservation_entry_id"] is not None
            else None
        )
        if held is not None:
            lock_balance(conn, scope, held.project_id)
        elif before["project_id"] is not None:
            lock_balance(conn, scope, cast(UUID, before["project_id"]))
        row = read_row(repo, id, lock=True)
        revision = _amendment(repo, id, revision_no, lock=True)
        _check_revision_version(revision, version)
        if revision["status"] != "draft":
            raise conflict("not_editable", "Only a draft amendment can be submitted.")
        if row["status"] != "sourcing_in_progress":
            raise conflict(
                "invalid_transition",
                "This status transition is invalid. Choose an available action.",
            )
        snap = cast(revisions.Snapshot, revision["snapshot"])
        if snap["base_version"] != row["version"]:
            raise conflict(
                "stale_version", "The requisition changed since this amendment was opened."
            )
        applied = revisions.latest_applied(repo, id)
        base = cast(revisions.Snapshot, applied["snapshot"]) if applied else None
        problems = _shape_problems(snap, base) + _master_problems(conn, scope, snap)
        total = Decimal(snap["estimate"])
        if held is not None and total > held.remaining:
            problems += [
                p
                for p in check_submission(
                    conn, scope, id, lock=True, need=total - held.remaining
                ).problems
                if p.category == "funds"
            ]
        if problems:
            raise SubmissionInvalid(problems)
        if base is not None and revisions.unchanged(base, snap):
            raise conflict("nothing_changed", "Not submitted. Nothing has changed.")
        state = approval_state(conn, scope, "requisition", id)
        approved = (
            state.approved_material_digest
            if state.status == "approved"
            else revisions.snapshot_digest(revisions.live_snapshot(conn, scope, id))
        )
        material = revisions.snapshot_digest(snap) != approved
        params = {"id": revision["id"], "note": body.note}
        if not material:
            revisions.apply_snapshot(conn, scope, id, snap)
            repo.execute(
                "UPDATE requisition_revision SET status='applied',decided_at=now(),note=%(note)s,"
                "version=version+1 WHERE org_id=%(org_id)s AND id=%(id)s",
                params,
            )
            revisions.amendment_event(conn, scope, row, actor_id, "applied")
            return
        repo.execute(
            "UPDATE requisition_revision SET status='pending',note=%(note)s,version=version+1 "
            "WHERE org_id=%(org_id)s AND id=%(id)s",
            params,
        )
        repo.execute(
            "UPDATE requisition SET pending_amendment_id=%(rev)s,updated_at=now() "
            "WHERE org_id=%(org_id)s AND id=%(id)s",
            {"id": id, "rev": revision["id"]},
        )
        routed = routing_check(
            conn, scope, id, SubmissionCheck(ready=True, problems=[], funding=None)
        )
        if routed.problems:
            raise SubmissionInvalid(routed.problems)
        _audit(
            conn,
            scope,
            actor_id,
            "requisition.amendment.submit",
            id,
            {"revision_no": revision_no, "estimate": snap["estimate"]},
        )
        start(
            conn,
            scope,
            document_type="requisition",
            document_id=id,
            actor_id=actor_id,
            resolver=AuthorizationResolver(cast(AuthorizationConnection, conn), scope),
        )


def withdraw_amendment(
    conn: Conn,
    scope: Scope,
    id: UUID,
    revision_no: int,
    actor_id: UUID,
    permitted: Callable[[str], bool],
) -> None:
    """The requester withdraws a running amendment; the instance lock comes first (R)."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        revision = _amendment(repo, id, revision_no)
        row = read_row(repo, id)
        if not permitted("requisition.withdraw"):
            raise ProblemError(
                ErrorCode.FORBIDDEN,
                detail="You need requisition.withdraw. Ask an administrator for access.",
            )
        if row["requester_id"] != actor_id:
            raise ProblemError(
                ErrorCode.FORBIDDEN, detail="Only the requester can withdraw this amendment."
            )
        if revision["status"] != "pending" or row["pending_amendment_id"] != revision["id"]:
            raise conflict(
                "invalid_transition", "Only an amendment awaiting approval can be withdrawn."
            )
        withdraw(
            conn,
            scope,
            document_type="requisition",
            document_id=id,
            actor_id=actor_id,
            reason="Amendment withdrawn",
        )
