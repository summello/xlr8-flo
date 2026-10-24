"""Duplicate identifiers (VEN-010): tax id plus configured unique identifiers, matched in SQL."""

import re
from collections.abc import Mapping
from uuid import UUID

from flo.modules.vendors import repo as sensitive
from flo.modules.vendors.models import VendorRepository

KIND = "vendor_identifier_type"


def normalise(value: str) -> str:
    return re.sub(r"[ .-]", "", value).upper()


def unique_types(repo: VendorRepository) -> set[str]:
    """Identifier types the organization marked `unique` (active ones only)."""
    rows = repo.execute(
        "SELECT code FROM master_record WHERE org_id = %(org_id)s AND kind = %(kind)s "
        "AND active AND attributes -> 'unique' = 'true'::jsonb",
        {"kind": KIND},
    ).fetchall()
    return {str(r[0]) for r in rows}


def stored(repo: VendorRepository, vendor_id: UUID) -> dict[str, str]:
    rows = repo.execute(
        "SELECT type_code, value_normalised FROM vendor_identifier "
        "WHERE org_id = %(org_id)s AND vendor_id = %(id)s ORDER BY type_code",
        {"id": vendor_id},
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def store(repo: VendorRepository, vendor_id: UUID, values: Mapping[str, str]) -> None:
    """Replace the vendor's configured identifiers; `values` are already validated codes."""
    repo.execute(
        "DELETE FROM vendor_identifier WHERE org_id = %(org_id)s AND vendor_id = %(id)s",
        {"id": vendor_id},
    )
    for type_code, value in values.items():
        repo.execute(
            "INSERT INTO vendor_identifier (org_id, vendor_id, type_code, value_normalised) "
            "VALUES (%(org_id)s, %(id)s, %(type)s, %(value)s)",
            {"id": vendor_id, "type": type_code, "value": normalise(value)},
        )


def lock(repo: VendorRepository, vendor_id: UUID) -> None:
    """Take the advisory locks for this vendor's identifiers, always in the same order."""
    sensitive.lock_tax(repo, vendor_id)
    own = stored(repo, vendor_id)
    for type_code in sorted(unique_types(repo) & own.keys()):
        repo.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%(org_id)s::text || ':id:' || "
            "%(type)s || ':' || %(value)s, 0))",
            {"type": type_code, "value": own[type_code]},
        )


def find(
    repo: VendorRepository,
    *,
    vendor_id: UUID | None = None,
    tax_id: str | None = None,
    country: str | None = None,
    identifiers: Mapping[str, str] | None = None,
) -> list[tuple[str, UUID]]:
    """(field, matching vendor id) pairs. Give `vendor_id` to check a stored vendor, or the
    typed values for a live check."""
    found: list[tuple[str, UUID]] = []
    if vendor_id is not None or (tax_id and country):
        typed = normalise(tax_id) if tax_id else None
        matches = sensitive.tax_matches(repo, vendor_id, typed, country and country.upper())
        found = [("tax_id", id) for id in matches]
    values = stored(repo, vendor_id) if vendor_id is not None else identifiers or {}
    for type_code in sorted(unique_types(repo) & values.keys()):
        rows = repo.execute(
            "SELECT i.vendor_id FROM vendor_identifier i JOIN vendor v "
            "ON v.org_id = i.org_id AND v.id = i.vendor_id "
            "WHERE i.org_id = %(org_id)s AND i.type_code = %(type)s "
            f"AND i.value_normalised = %(value)s AND {sensitive.HOLDS} "
            "AND i.vendor_id IS DISTINCT FROM %(self)s::uuid ORDER BY i.vendor_id",
            {"type": type_code, "value": normalise(values[type_code]), "self": vendor_id},
        ).fetchall()
        found += [(f"identifiers.{type_code}", UUID(str(r[0]))) for r in rows]
    return found
