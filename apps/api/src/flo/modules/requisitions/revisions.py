"""Immutable requisition revisions: snapshots, the material digest, change sets, in-place apply."""

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.money import currency_exponent, quantize
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.schemas import DocumentSnapshot
from flo.modules.approvals.service import instances_for, material_digest
from flo.modules.budget.service import get_balance, reservation_remaining
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.requisitions.models import RequisitionRepository
from flo.modules.requisitions.schemas import (
    ChangeDelta,
    ChangeFunding,
    ChangeRow,
    ChangeSet,
    RevisionSummary,
)

type Snapshot = dict[str, Any]
type Conn = psycopg.Connection[tuple[object, ...]]

HEADER_LABELS = {
    "title": "Title",
    "justification": "Justification",
    "needed_by": "Needed by",
    "department_code": "Department",
    "ledger_account_code": "Ledger account",
    "delivery_address_id": "Delivery location",
    "project_id": "Project",
}
LINE_LABELS = {
    "line_type": "type",
    "description": "description",
    "quantity": "quantity",
    "uom_code": "unit",
    "unit_price": "unit price",
    "amount": "amount",
    "category_code": "category",
    "catalogue_item_id": "catalogue item",
}
LINE_MONEY = {"unit_price", "amount"}
REVISION_FIELDS = (
    "id",
    "revision_no",
    "kind",
    "status",
    "snapshot",
    "material_digest",
    "version",
    "created_by",
    "created_at",
    "decided_at",
    "note",
)


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _four(value: object) -> str:
    return format(Decimal(str(value)), ".4f")


def live_snapshot(conn: Conn, scope: Scope, requisition_id: UUID) -> Snapshot:
    """The requisition's header and lines as they stand now; money as four-place strings."""
    repo = RequisitionRepository(conn, scope)
    head = repo.execute(
        "SELECT title,justification,needed_by,department_code,ledger_account_code,"
        "delivery_address_id,project_id,bu_id,currency,estimate,version FROM requisition "
        "WHERE org_id=%(org_id)s AND id=%(id)s",
        {"id": requisition_id},
    ).fetchone()
    if head is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    lines = repo.execute(
        "SELECT id,line_no,line_type,description,quantity,uom_code,unit_price,amount,"
        "category_code,catalogue_item_id FROM requisition_line WHERE org_id=%(org_id)s "
        "AND requisition_id=%(id)s ORDER BY line_no",
        {"id": requisition_id},
    ).fetchall()
    return {
        "header": {
            "title": head[0],
            "justification": head[1],
            "needed_by": _text(head[2]),
            "department_code": head[3],
            "ledger_account_code": head[4],
            "delivery_address_id": _text(head[5]),
            "project_id": _text(head[6]),
            "bu_id": _text(head[7]),
            "currency": head[8],
        },
        "lines": [
            {
                "line_id": str(r[0]),
                "line_no": r[1],
                "line_type": r[2],
                "description": r[3],
                "quantity": _four(r[4]),
                "uom_code": r[5],
                "unit_price": _four(r[6]),
                "amount": _four(r[7]),
                "category_code": r[8],
                "catalogue_item_id": _text(r[9]),
            }
            for r in lines
        ],
        "estimate": _four(head[9]),
        "base_version": head[10],
    }


def material_of(
    estimate: Decimal, project_id: object, ledger_account_code: object, catalogue: Sequence[object]
) -> dict[str, str]:
    """The one definition of the material fields (D-M2-13); the adapter and revisions share it."""
    return {
        "amount": format(estimate, ".4f"),
        "project_id": str(project_id) if project_id else "",
        "ledger_account_code": str(ledger_account_code or ""),
        "catalogue_items": ",".join(sorted(str(item) if item else "-" for item in catalogue)),
        "line_count": str(len(catalogue)),
    }


def digest(material: dict[str, str]) -> str:
    """The approvals digest of a material dict (never a local hash)."""
    nil = UUID(int=0)
    return material_digest(
        DocumentSnapshot(0, nil, cast(Any, AuthorizationTarget(ScopeType.BU, nil)), {}, material)
    )


def snapshot_material(snap: Snapshot) -> dict[str, str]:
    return material_of(
        Decimal(snap["estimate"]),
        snap["header"]["project_id"],
        snap["header"]["ledger_account_code"],
        [line["catalogue_item_id"] for line in snap["lines"]],
    )


def snapshot_digest(snap: Snapshot) -> str:
    return digest(snapshot_material(snap))


def _content(snap: Snapshot) -> Snapshot:
    header = snap["header"]
    return {
        "header": {key: header[key] for key in HEADER_LABELS},
        "lines": sorted(
            (
                {k: v for k, v in line.items() if k != "line_no"}
                for line in cast(list[Snapshot], snap["lines"])
            ),
            key=lambda line: str(line["line_id"]),
        ),
    }


def unchanged(left: Snapshot, right: Snapshot) -> bool:
    """Every header field and every line identical (REQ-012)."""
    return _content(left) == _content(right)


def _revision(row: tuple[object, ...]) -> dict[str, object]:
    return dict(zip(REVISION_FIELDS, row, strict=True))


def revision_row(
    repo: RequisitionRepository,
    requisition_id: UUID,
    revision_no: int,
    *,
    lock: bool = False,
) -> dict[str, object]:
    row = repo.execute(
        f"SELECT {','.join(REVISION_FIELDS)} FROM requisition_revision WHERE "
        "org_id=%(org_id)s AND requisition_id=%(id)s AND revision_no=%(no)s"
        + (" FOR UPDATE" if lock else ""),
        {"id": requisition_id, "no": revision_no},
    ).fetchone()
    if row is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return _revision(row)


def open_amendment(
    repo: RequisitionRepository, requisition_id: UUID, *, lock: bool = False
) -> dict[str, object] | None:
    row = repo.execute(
        f"SELECT {','.join(REVISION_FIELDS)} FROM requisition_revision WHERE "
        "org_id=%(org_id)s AND requisition_id=%(id)s AND kind='amendment' "
        "AND status IN ('draft','pending')" + (" FOR UPDATE" if lock else ""),
        {"id": requisition_id},
    ).fetchone()
    return _revision(row) if row else None


def latest_applied(
    repo: RequisitionRepository, requisition_id: UUID, against: int | None = None
) -> dict[str, object] | None:
    row = repo.execute(
        f"SELECT {','.join(REVISION_FIELDS)} FROM requisition_revision WHERE "
        "org_id=%(org_id)s AND requisition_id=%(id)s AND "
        + ("revision_no=%(no)s" if against is not None else "status='applied'")
        + " ORDER BY revision_no DESC LIMIT 1",
        {"id": requisition_id, "no": against},
    ).fetchone()
    return _revision(row) if row else None


def insert_revision(
    repo: RequisitionRepository,
    requisition_id: UUID,
    kind: str,
    status: str,
    snap: Snapshot,
    actor_id: UUID,
    note: str | None,
) -> dict[str, object]:
    """Caller holds the requisition row lock, so the next number cannot be taken twice."""
    row = repo.execute(
        "INSERT INTO requisition_revision(id,org_id,requisition_id,revision_no,kind,status,"
        "snapshot,material_digest,created_by,note,decided_at) VALUES(%(rid)s,%(org_id)s,%(id)s,"
        "(SELECT COALESCE(MAX(revision_no),0)+1 FROM requisition_revision WHERE "
        "requisition_id=%(id)s),%(kind)s,%(status)s,%(snap)s,%(digest)s,%(actor)s,%(note)s,"
        "CASE WHEN %(status)s='applied' THEN now() END) "
        f"RETURNING {','.join(REVISION_FIELDS)}",
        {
            "rid": uuid4(),
            "id": requisition_id,
            "kind": kind,
            "status": status,
            "snap": Jsonb(snap),
            "digest": snapshot_digest(snap),
            "actor": actor_id,
            "note": note,
        },
    ).fetchone()
    assert row is not None
    return _revision(row)


def record_submission(
    conn: Conn, scope: Scope, requisition_id: UUID, actor_id: UUID, note: str | None
) -> int:
    """Every submission is an immutable revision (REQ-012); the note is kept with it."""
    row = insert_revision(
        RequisitionRepository(conn, scope),
        requisition_id,
        "submission",
        "applied",
        live_snapshot(conn, scope, requisition_id),
        actor_id,
        note,
    )
    return cast(int, row["revision_no"])


def set_status(repo: RequisitionRepository, revision_id: object, status: str) -> None:
    repo.execute(
        "UPDATE requisition_revision SET status=%(status)s,decided_at=now() "
        "WHERE org_id=%(org_id)s AND id=%(id)s",
        {"id": revision_id, "status": status},
    )


def summaries(conn: Conn, scope: Scope, requisition_id: UUID) -> list[RevisionSummary]:
    with tenant_transaction(cast(RlsSession, conn), scope):
        rows = (
            RequisitionRepository(conn, scope)
            .execute(
                "SELECT revision_no,kind,status,created_by,created_at,note FROM "
                "requisition_revision WHERE org_id=%(org_id)s AND requisition_id=%(id)s "
                "ORDER BY revision_no DESC",
                {"id": requisition_id},
            )
            .fetchall()
        )
    fields = ("revision_no", "kind", "status", "created_by", "created_at", "note")
    return [RevisionSummary.model_validate(dict(zip(fields, r, strict=True))) for r in rows]


def apply_snapshot(conn: Conn, scope: Scope, requisition_id: UUID, snap: Snapshot) -> None:
    """Write an amendment's header and lines in place; the revision row keeps the history."""
    repo = RequisitionRepository(conn, scope)
    header = snap["header"]
    counter = repo.execute(
        "SELECT last_line_no FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s",
        {"id": requisition_id},
    ).fetchone()
    assert counter is not None
    last_no = int(str(counter[0]))
    existing = {
        cast(UUID, r[0])
        for r in repo.execute(
            "SELECT id FROM requisition_line WHERE org_id=%(org_id)s AND requisition_id=%(id)s",
            {"id": requisition_id},
        ).fetchall()
    }
    keep = {UUID(line["line_id"]) for line in snap["lines"]}
    for gone in existing - keep:
        repo.execute(
            "DELETE FROM requisition_line WHERE org_id=%(org_id)s AND requisition_id=%(req)s "
            "AND id=%(id)s",
            {"req": requisition_id, "id": gone},
        )
    for line in snap["lines"]:
        line_id = UUID(line["line_id"])
        values = {
            "line_type": line["line_type"],
            "description": line["description"],
            "quantity": line["quantity"],
            "uom_code": line["uom_code"],
            "unit_price": line["unit_price"],
            "amount": line["amount"],
            "category_code": line["category_code"],
            "catalogue_item_id": line["catalogue_item_id"],
        }
        if line_id in existing:
            repo.execute(
                "UPDATE requisition_line SET "
                + ",".join(f"{k}=%({k})s" for k in values)
                + ",updated_at=now() WHERE org_id=%(org_id)s AND requisition_id=%(req)s "
                "AND id=%(id)s",
                values | {"req": requisition_id, "id": line_id},
            )
        else:
            last_no += 1
            repo.execute(
                "INSERT INTO requisition_line(org_id,id,requisition_id,line_no,currency,"
                + ",".join(values)
                + ") VALUES(%(org_id)s,%(id)s,%(req)s,%(no)s,%(currency)s,"
                + ",".join(f"%({k})s" for k in values)
                + ")",
                values
                | {
                    "id": line_id,
                    "req": requisition_id,
                    "no": last_no,
                    "currency": header["currency"],
                },
            )
    repo.execute(
        "UPDATE requisition SET title=%(title)s,justification=%(justification)s,"
        "needed_by=%(needed_by)s,department_code=%(department_code)s,"
        "ledger_account_code=%(ledger_account_code)s,delivery_address_id=%(delivery)s,"
        "estimate=(SELECT COALESCE(SUM(amount),0) FROM requisition_line WHERE "
        "org_id=%(org_id)s AND requisition_id=%(id)s),last_line_no=%(last)s,"
        "version=version+1,updated_at=now() WHERE org_id=%(org_id)s AND id=%(id)s",
        {
            "id": requisition_id,
            "title": header["title"],
            "justification": header["justification"],
            "needed_by": header["needed_by"],
            "department_code": header["department_code"],
            "ledger_account_code": header["ledger_account_code"],
            "delivery": header["delivery_address_id"],
            "last": last_no,
        },
    )


def amendment_event(
    conn: Conn,
    scope: Scope,
    row: dict[str, object],
    actor_id: UUID,
    outcome: str,
    *,
    ledger_entry_id: int | None = None,
    decision_id: UUID | None = None,
) -> None:
    """A status event (from = to) and an audit row; the requisition's status is untouched."""
    from flo.modules.requisitions.service import RequisitionService

    RequisitionRepository(conn, scope).execute(
        "INSERT INTO requisition_status_event(org_id,requisition_id,from_status,to_status,"
        "actor_id,reason,funding_round,ledger_entry_id,decision_id) VALUES(%(org_id)s,%(id)s,"
        "'sourcing_in_progress','sourcing_in_progress',%(actor)s,%(reason)s,%(round)s,"
        "%(ledger)s,%(decision)s)",
        {
            "id": row["id"],
            "actor": actor_id,
            "reason": f"amendment {outcome}",
            "round": row["funding_round"],
            "ledger": ledger_entry_id,
            "decision": decision_id,
        },
    )
    RequisitionService(conn, scope, actor_id)._audit(
        f"requisition.amendment.{outcome}",
        cast(UUID, row["id"]),
        {"amendment": "pending"},
        {"amendment": outcome},
    )


# --- change sets -------------------------------------------------------------------------


def _money_text(value: str, currency: str) -> str:
    return format(quantize(Decimal(value), currency), f".{currency_exponent(currency)}f")


def _delta(was: str, now: str, currency: str) -> ChangeDelta:
    difference = Decimal(now) - Decimal(was)
    percent: str | None = None
    if Decimal(was) != 0:
        # ponytail: one decimal place through kernel/money (JPY exponent 0 on a x10 value).
        scaled = quantize(abs(difference) / Decimal(was) * 1000, "JPY")
        percent = format(scaled / 10, ".1f")
    return ChangeDelta(
        amount=_money_text(str(abs(difference)), currency),
        percent=percent,
        direction="up" if difference > 0 else "down",
    )


def diff_rows(
    base: Snapshot, work: Snapshot, currency: str, project_label: Callable[[str | None], str | None]
) -> list[ChangeRow]:
    rows: list[ChangeRow] = []
    for key, label in HEADER_LABELS.items():
        was, now = base["header"][key], work["header"][key]
        if was != now:
            if key == "project_id":
                was, now = project_label(was), project_label(now)
            rows.append(ChangeRow(field=key, label=label, was=was, now=now, change="changed"))
    if Decimal(base["estimate"]) != Decimal(work["estimate"]):
        rows.append(
            ChangeRow(
                field="estimate",
                label="Estimate",
                was=_money_text(base["estimate"], currency),
                now=_money_text(work["estimate"], currency),
                change="changed",
                delta=_delta(base["estimate"], work["estimate"], currency),
            )
        )
    before = {line["line_id"]: line for line in base["lines"]}
    after = {line["line_id"]: line for line in work["lines"]}
    fresh = 0
    for line_id, line in after.items():
        if line_id not in before:
            fresh += 1
            number = line["line_no"] or f"new {fresh}"
            rows.append(
                ChangeRow(
                    field=f"lines/{line_id}",
                    label=f"Line {number}",
                    was=None,
                    now=line["description"],
                    change="added",
                )
            )
            continue
        old = before[line_id]
        for key, noun in LINE_LABELS.items():
            was, now = old[key], line[key]
            if (
                (Decimal(was) != Decimal(now))
                if key in {"quantity", "unit_price", "amount"}
                else (was != now)
            ):
                money = key in LINE_MONEY
                rows.append(
                    ChangeRow(
                        field=f"lines/{line_id}/{key}",
                        label=f"Line {old['line_no']} {noun}",
                        was=_money_text(was, currency) if money else was,
                        now=_money_text(now, currency) if money else now,
                        change="changed",
                        delta=_delta(was, now, currency) if money else None,
                    )
                )
    for line_id, old in before.items():
        if line_id not in after:
            rows.append(
                ChangeRow(
                    field=f"lines/{line_id}",
                    label=f"Line {old['line_no']}",
                    was=old["description"],
                    now=None,
                    change="removed",
                )
            )
    return rows


def latest_instance(conn: Conn, scope: Scope, requisition_id: UUID) -> tuple[UUID, str] | None:
    """The newest approval instance, read through approvals.service (greatest started_at, id)."""
    found = instances_for(conn, scope, "requisition", requisition_id)
    return (found[-1].id, found[-1].status) if found else None


def _change_set(
    conn: Conn,
    scope: Scope,
    requisition_id: UUID,
    against: int | None,
    can_view_budget: Callable[[UUID], bool],
) -> ChangeSet:
    """What a submit or amendment submit would change, and what it would then do to funding."""
    repo = RequisitionRepository(conn, scope)
    live = live_snapshot(conn, scope, requisition_id)
    status = cast(
        str,
        cast(
            tuple[object, ...],
            repo.execute(
                "SELECT status FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": requisition_id},
            ).fetchone(),
        )[0],
    )
    amendment = open_amendment(repo, requisition_id)
    work = cast(Snapshot, amendment["snapshot"]) if amendment else live
    to: Literal["draft", "amendment"] = "amendment" if amendment else "draft"
    base_row = latest_applied(repo, requisition_id, against)
    if against is not None and base_row is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    currency = str(live["header"]["currency"])

    def project_label(project_id: str | None) -> str | None:
        if project_id is None:
            return None
        row = repo.execute(
            "SELECT number,name FROM project WHERE org_id=%(org_id)s AND id=%(id)s",
            {"id": project_id},
        ).fetchone()
        return f"{row[0]} {row[1]}" if row else project_id

    if base_row is None:
        return ChangeSet(
            from_revision=0,
            to=to,
            nothing_changed=False,
            material=False,
            routing="not_applicable",
            rows=[],
            funding=None,
        )
    base = cast(Snapshot, base_row["snapshot"])
    rows = diff_rows(base, work, currency, project_label)
    same = unchanged(base, work)
    material = snapshot_digest(work) != snapshot_digest(base)
    instance = latest_instance(conn, scope, requisition_id)
    routing: str = "not_applicable"
    if not same:
        if to == "amendment":
            routing = "restarts" if material else "not_applicable"
        elif status == "draft":
            returned = instance is not None and instance[1] == "returned"
            if returned and not material:
                routing = "same_step"
            else:
                routing = "restarts"
    held_row = repo.execute(
        "SELECT reservation_entry_id FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s",
        {"id": requisition_id},
    ).fetchone()
    entry_id = cast(int | None, held_row[0]) if held_row else None
    project_id = work["header"]["project_id"]
    funding: ChangeFunding | None = None
    if project_id is not None and (to == "amendment" or status == "draft"):
        held = reservation_remaining(conn, scope, entry_id) if entry_id is not None else None
        now_total = Decimal(work["estimate"])
        reserved_now = held.remaining if held else Decimal(0)
        moved = held is not None and str(held.project_id) != project_id
        available: Decimal | None = None
        if can_view_budget(UUID(project_id)):
            balance = get_balance(conn, scope, UUID(project_id)).available
            available = (
                balance - now_total if moved or held is None else balance + reserved_now - now_total
            )
        outcome = (
            "moved"
            if moved
            else "unchanged"
            if held is not None and reserved_now == now_total
            else "released_once"
            if held is not None and now_total < reserved_now
            else "reserved_more"
        )
        funding = ChangeFunding(
            reserved_now=_money_text(str(reserved_now), currency),
            reserved_after=_money_text(str(now_total), currency),
            outcome=cast(Any, outcome),
            project_available_after=None
            if available is None
            else _money_text(str(available), currency),
            currency=currency,
        )
    return ChangeSet(
        from_revision=cast(int, base_row["revision_no"]),
        to=cast(Any, to),
        nothing_changed=same,
        material=material,
        routing=cast(Any, routing),
        rows=rows,
        funding=funding,
    )


def change_set(
    conn: Conn,
    scope: Scope,
    requisition_id: UUID,
    against: int | None,
    can_view_budget: Callable[[UUID], bool],
) -> ChangeSet:
    with tenant_transaction(cast(RlsSession, conn), scope):
        return _change_set(conn, scope, requisition_id, against, can_view_budget)


def ensure_no_open(repo: RequisitionRepository, requisition_id: UUID) -> None:
    if open_amendment(repo, requisition_id) is not None:
        raise conflict(
            "amendment_open",
            "This requisition already has an open amendment. Continue or withdraw it first.",
        )
