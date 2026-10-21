"""The one door to tax and banking data (VEN-014). No other file names these columns."""

from collections.abc import Sequence
from uuid import UUID

from flo.modules.vendors.models import VendorRepository

# ponytail: plaintext columns behind one repo door; add envelope encryption (kernel/identity/
# mfa.py's key handling) if a customer contract requires column-level encryption.

# wire field -> column; the revealed groups are `tax_id` and `bank`.
COLUMNS = {
    "tax_id": "tax_id",
    "tax_id_country": "tax_id_country",
    "bank_name": "bank_account_name",
    "bank_account": "bank_account_number",
    "bank_routing": "bank_routing",
}
GROUPS = {
    "tax_id": ("tax_id", "tax_id_country"),
    "bank": ("bank_name", "bank_account", "bank_routing"),
}
PLAIN = {"tax_id_country"}  # a country code is not secret


def _mask(column: str) -> str:
    """Masked in SQL, so the full value never enters this process on the masked path."""
    return (
        f"CASE WHEN s.{column} IS NULL THEN NULL WHEN char_length(s.{column}) <= 4 "
        f"THEN '••••' ELSE '•••• ' || right(s.{column}, 4) END"
    )


def masked(
    repo: VendorRepository, vendor_ids: Sequence[UUID]
) -> dict[UUID, dict[str, str | int | None]]:
    select = ", ".join(
        f"s.{column}" if field in PLAIN else _mask(column) for field, column in COLUMNS.items()
    )
    rows = repo.execute(
        f"SELECT s.vendor_id, s.version, {select} FROM vendor_sensitive s "
        "WHERE s.org_id = %(org_id)s AND s.vendor_id = ANY(%(ids)s)",
        {"ids": list(vendor_ids)},
    ).fetchall()
    return {
        UUID(str(row[0])): {
            "version": int(str(row[1])),
            **{
                field: None if value is None else str(value)
                for field, value in zip(COLUMNS, row[2:], strict=True)
            },
        }
        for row in rows
    }


def leaks(repo: VendorRepository, vendor_id: UUID, text: str) -> bool:
    """True when `text` holds a stored value of four or more characters. Compared in SQL."""
    tests = " OR ".join(
        f"(char_length({c}) >= 4 AND position(lower({c}) IN lower(%(text)s)) > 0)"
        for c in COLUMNS.values()
    )
    row = repo.execute(
        f"SELECT 1 FROM vendor_sensitive WHERE org_id = %(org_id)s AND vendor_id = %(id)s "
        f"AND ({tests})",
        {"id": vendor_id, "text": text},
    ).fetchone()
    return row is not None


def reveal(
    repo: VendorRepository, vendor_id: UUID, groups: Sequence[str]
) -> dict[str, str | int | None]:
    """Clear values of the named groups only; the caller has audited the reveal first."""
    fields = [f for group in groups for f in GROUPS[group]]
    row = repo.execute(
        f"SELECT version, {', '.join(COLUMNS[f] for f in fields)} FROM vendor_sensitive "
        "WHERE org_id = %(org_id)s AND vendor_id = %(id)s",
        {"id": vendor_id},
    ).fetchone()
    values = dict.fromkeys(COLUMNS) if row is None else dict(zip(fields, row[1:], strict=True))
    return {
        "version": 0 if row is None else int(str(row[0])),
        **{f: None if values.get(f) is None else str(values[f]) for f in COLUMNS},
    }


def put(
    repo: VendorRepository,
    vendor_id: UUID,
    values: dict[str, str | None],
    actor: UUID | None,
    expected: int,
) -> tuple[list[str], int] | None:
    """Replace the record if `expected` is its version (0 = none yet).

    Returns the names of the changed fields and the new version, or None when stale. The caller
    holds the vendor row lock, so the compare and the write cannot interleave.
    """
    row = repo.execute(
        f"SELECT version, {', '.join(COLUMNS.values())} FROM vendor_sensitive "
        "WHERE org_id = %(org_id)s AND vendor_id = %(id)s FOR UPDATE",
        {"id": vendor_id},
    ).fetchone()
    version = 0 if row is None else int(str(row[0]))
    if version != expected:
        return None
    old = dict.fromkeys(COLUMNS) if row is None else dict(zip(COLUMNS, row[1:], strict=True))
    changed = [f for f in COLUMNS if old[f] != values.get(f)]
    columns = ", ".join(COLUMNS.values())
    placeholders = ", ".join(f"%({f})s" for f in COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS.values())
    repo.execute(
        f"INSERT INTO vendor_sensitive (org_id, vendor_id, {columns}, updated_by, updated_at) "
        f"VALUES (%(org_id)s, %(id)s, {placeholders}, %(actor)s, now()) "
        f"ON CONFLICT (org_id, vendor_id) DO UPDATE SET {updates}, "
        "version = vendor_sensitive.version + 1, "
        "updated_by = EXCLUDED.updated_by, updated_at = now()",
        {"id": vendor_id, "actor": actor, **{f: values.get(f) for f in COLUMNS}},
    )
    return changed, version + 1
