"""Atomic line edits serialized by the requisition row lock."""

from decimal import Decimal, localcontext
from typing import cast
from uuid import UUID, uuid4

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.money import quantize
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.service import MasterCodeUnusable, assert_usable, today
from flo.modules.requisitions.catalogue import CatalogueService
from flo.modules.requisitions.schemas import (
    LineCreate,
    LineRead,
    LinesRead,
    LinesReplace,
    LineWriteResult,
    invalid,
)
from flo.modules.requisitions.service import RequisitionService

FIELDS = (
    "id",
    "line_no",
    "line_type",
    "description",
    "quantity",
    "uom_code",
    "unit_price",
    "amount",
    "currency",
    "category_code",
    "catalogue_item_id",
    "sourced_qty",
    "awarded_qty",
    "ordered_qty",
    "cancelled_qty",
)
DECIMALS = {
    "quantity",
    "unit_price",
    "amount",
    "sourced_qty",
    "awarded_qty",
    "ordered_qty",
    "cancelled_qty",
}
LIMIT = Decimal("99999999999999.9999")


class LineService(RequisitionService):
    def _rows(self, id: UUID) -> list[LineRead]:
        rows = self.repo.execute(
            "SELECT "
            + ",".join(f + ("::text" if f in DECIMALS else "") for f in FIELDS)
            + " FROM requisition_line WHERE org_id=%(org_id)s AND "
            "requisition_id=%(id)s ORDER BY line_no",
            {"id": id},
        ).fetchall()
        result = []
        for row in rows:
            values = dict(zip(FIELDS, row, strict=True))
            values["requested_qty"] = values["quantity"]
            values["remaining_qty"] = str(
                Decimal(cast(str, values["quantity"]))
                - Decimal(cast(str, values["ordered_qty"]))
                - Decimal(cast(str, values["cancelled_qty"]))
            )
            result.append(LineRead.model_validate(values))
        return result

    def list_lines(self, id: UUID) -> LinesRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            header = self._get(id)
            return LinesRead(
                rows=self._rows(id),
                estimate=header.estimate,
                currency=header.currency,
                version=header.version,
            )

    def _values(
        self, body: LineCreate, currency: str, index: int, bu_id: UUID | None = None
    ) -> dict[str, object]:
        values = body.model_dump(exclude={"line_id"})
        if body.catalogue_item_id is not None:
            assert bu_id is not None
            item = CatalogueService(self.connection, self.scope).item(body.catalogue_item_id, bu_id)
            if item is None:
                raise invalid(
                    f"lines/{index}/catalogue_item_id",
                    "Choose an item from the suggestions for this unit.",
                )
            if item.currency != currency:
                raise invalid(
                    f"lines/{index}/catalogue_item_id",
                    f"This item is priced in {item.currency}; the request is in {currency}.",
                    "currency_mismatch",
                )
            values.update(
                line_type=item.line_type,
                description=item.description,
                uom_code=item.uom_code,
                category_code=item.category_code,
                quantity=body.quantity or "1",
                unit_price=body.unit_price or item.last_unit_price,
            )
        line_type = cast(str, values["line_type"])
        for field, kind in [("uom_code", "uom"), ("category_code", line_type + "_category")]:
            code = cast(str | None, values[field])
            if code is not None:
                try:
                    values[field] = assert_usable(
                        self.connection, self.scope, kind, code, today()
                    ).code
                except MasterCodeUnusable as exc:
                    raise invalid(
                        f"lines/{index}/{field}",
                        f"Code {exc.code} is {exc.reason}. Choose a usable code.",
                        exc.reason,
                    ) from exc
        with localcontext() as context:
            context.prec = 40
            amount = quantize(
                Decimal(cast(str, values["quantity"])) * Decimal(cast(str, values["unit_price"])),
                currency,
            )
        if amount > LIMIT:
            raise invalid(
                f"lines/{index}/quantity",
                "Line amount is too large. Reduce quantity or price.",
                "amount_too_large",
            )
        values.update(amount=amount, currency=currency)
        return values

    def write(
        self,
        id: UUID,
        version: str | None,
        *,
        body: LineCreate | None = None,
        line_id: UUID | None = None,
        replacement: LinesReplace | None = None,
        delete: bool = False,
    ) -> LineWriteResult | LinesRead:
        if version is None:
            raise ProblemError(
                ErrorCode.BAD_REQUEST,
                detail="Supply If-Match with the requisition version.",
                checks={"problem": "if_match_required"},
            )
        try:
            expected = int(version.strip('"'))
        except ValueError as exc:
            raise ProblemError(
                ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
            ) from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            header = self._get(id, lock=True)
            if header.version != expected:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="The requisition changed. Reload and retry.",
                    checks={"problem": "stale_version"},
                )
            if header.status != "draft":
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="Only drafts can be edited.",
                    checks={"problem": "not_editable"},
                )
            old = {row.id: row for row in self._rows(id)}
            if line_id is not None and line_id not in old:
                raise ProblemError(ErrorCode.NOT_FOUND)
            desired: dict[UUID, dict[str, object]] = {
                key: value.model_dump(include=set(FIELDS)) for key, value in old.items()
            }
            if replacement is not None:
                desired = {}
                for index, line in enumerate(replacement.lines):
                    key = line.line_id or uuid4()
                    if line.line_id is not None and (key not in old or key in desired):
                        raise invalid(
                            f"lines/{index}/line_id", "Choose an existing, distinct line."
                        )
                    desired[key] = self._values(line, header.currency, index, header.bu_id)
            elif delete:
                desired.pop(cast(UUID, line_id))
            else:
                assert body is not None
                line_id = line_id or uuid4()
                desired[line_id] = self._values(body, header.currency, 0, header.bu_id)
            if len(desired) > 500:
                raise invalid("lines", "A requisition can contain at most 500 lines.")
            total = sum((Decimal(str(value["amount"])) for value in desired.values()), Decimal(0))
            if total > LIMIT:
                raise invalid(
                    "lines/0/quantity",
                    "The estimate is too large. Reduce quantities or prices.",
                    "amount_too_large",
                )
            # Refuse mismatched persisted currencies as well as deriving all new ones.
            if any(value["currency"] != header.currency for value in desired.values()):
                raise invalid(
                    "currency", "Line currency must match the requisition.", "currency_mismatch"
                )
            counter = self.repo.execute(
                "SELECT last_line_no FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": id},
            ).fetchone()
            assert counter is not None
            last_no = int(str(counter[0]))
            changed: list[tuple[str, UUID]] = []
            for key in old.keys() - desired.keys():
                self.repo.execute(
                    "DELETE FROM requisition_line WHERE org_id=%(org_id)s AND "
                    "requisition_id=%(id)s AND id=%(line)s",
                    {"id": id, "line": key},
                )
                changed.append(("delete", key))
            for key, values in desired.items():
                if key in old:
                    if replacement is None and key != line_id:
                        continue
                    comparable = old[key].model_dump(include=set(values))
                    if replacement is not None and all(
                        Decimal(str(comparable[k])) == Decimal(str(v))
                        if k in DECIMALS
                        else comparable[k] == v
                        for k, v in values.items()
                    ):
                        continue
                    columns = [k for k in values if k not in {"id", "line_no"}]
                    self.repo.execute(
                        "UPDATE requisition_line SET "
                        + ",".join(k + "=%(" + k + ")s" for k in columns)
                        + ",updated_at=now() WHERE org_id=%(org_id)s AND "
                        "requisition_id=%(req)s AND id=%(key)s",
                        values | {"req": id, "key": key},
                    )
                    changed.append(("update", key))
                else:
                    last_no += 1
                    values = values | {"id": key, "requisition_id": id, "line_no": last_no}
                    columns = list(values)
                    self.repo.execute(
                        "INSERT INTO requisition_line (org_id,"
                        + ",".join(columns)
                        + ") VALUES (%(org_id)s,"
                        + ",".join("%(" + k + ")s" for k in columns)
                        + ")",
                        values,
                    )
                    changed.append(("create", key))
            self.repo.execute(
                "UPDATE requisition SET estimate=(SELECT COALESCE(SUM(amount),0) "
                "FROM requisition_line WHERE org_id=%(org_id)s AND "
                "requisition_id=%(id)s),version=version+1,last_line_no=%(last)s,"
                "updated_at=now() WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": id, "last": last_no},
            )
            after = self._get(id)
            rows = self._rows(id)
            new = {row.id: row for row in rows}
            for action, key in changed:
                self._audit(
                    "requisition.line." + action,
                    id,
                    {"line": old[key].model_dump(mode="json"), "estimate": header.estimate}
                    if key in old
                    else {"line": None, "estimate": header.estimate},
                    {
                        "line": new[key].model_dump(mode="json") if key in new else None,
                        "estimate": after.estimate,
                    },
                )
            if replacement is not None:
                return LinesRead(
                    rows=rows,
                    estimate=after.estimate,
                    currency=after.currency,
                    version=after.version,
                )
            return LineWriteResult(
                line=new.get(cast(UUID, line_id)),
                estimate=after.estimate,
                currency=after.currency,
                version=after.version,
            )
