"""The one eligibility rule (VEN-012). Reads status, availability and documents; writes nothing."""

from datetime import date
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.vendors.documents import KIND, state_of
from flo.modules.vendors.models import ANCESTORS_CTE, AVAILABLE_SQL, VendorRepository
from flo.modules.vendors.schemas import Eligibility, Reason

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _on(day: date) -> str:
    # ponytail: fixed English day-month (not process-locale strftime); no caller locale exists
    # server-side yet (UX-007). Add one with a user locale setting; the raw date is returned too.
    return f"{day.day} {MONTHS[day.month - 1]}"


def _label(status: str) -> str:
    return status.replace("_", " ").title()


class EligibilityMixin:
    connection: psycopg.Connection[tuple[object, ...]]
    scope: Scope
    repo: VendorRepository

    def eligibility(
        self, vendor_ids: list[UUID], unit_id: UUID, on: date
    ) -> dict[UUID, Eligibility]:
        """Batched: five queries however many vendors. A vendor of another tenant, or an unknown
        id, is absent from the result rather than reported ineligible."""
        ids = list(dict.fromkeys(vendor_ids))
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            unit = self.repo.execute(
                "SELECT name FROM org_unit WHERE org_id = %(org_id)s AND id = %(unit_id)s",
                {"unit_id": unit_id},
            ).fetchone()
            if unit is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            unit_name = str(unit[0])
            vendors = self.repo.execute(
                f"{ANCESTORS_CTE} SELECT v.id, v.status, v.availability, {AVAILABLE_SQL} "
                "FROM vendor v WHERE v.org_id = %(org_id)s AND v.id = ANY(%(ids)s)",
                {"unit_id": unit_id, "ids": ids},
            ).fetchall()
            disabled = {
                cast(UUID, r[0]): (str(r[1]), str(r[2]))
                for r in self.repo.execute(
                    f"{ANCESTORS_CTE} SELECT DISTINCT ON (d.vendor_id) d.vendor_id, u.name, "
                    "i.email FROM vendor_local_disable d JOIN anc ON anc.id = d.unit_id "
                    "JOIN org_unit u ON u.org_id = d.org_id AND u.id = d.unit_id "
                    "JOIN identity i ON i.id = d.disabled_by "
                    "WHERE d.org_id = %(org_id)s AND d.vendor_id = ANY(%(ids)s) "
                    "ORDER BY d.vendor_id, u.name",
                    {"unit_id": unit_id, "ids": ids},
                ).fetchall()
            }
            required = {
                str(r[0]): str(r[1])
                for r in self.repo.execute(
                    "SELECT code, name FROM master_record WHERE org_id = %(org_id)s "
                    "AND kind = %(kind)s AND active AND effective_from <= %(on)s "
                    "AND (effective_to IS NULL OR effective_to >= %(on)s) "
                    "AND attributes @> '{\"required\": true}'::jsonb",
                    {"kind": KIND, "on": on},
                ).fetchall()
            }
            docs: dict[UUID, dict[str, tuple[str, int | None, date | None]]] = {}
            for vid, code, expiry, verification in self.repo.execute(
                "SELECT vendor_id, type_code, expiry_date, verification FROM vendor_document "
                "WHERE org_id = %(org_id)s AND vendor_id = ANY(%(ids)s) "
                "AND type_code = ANY(%(codes)s)",
                {"ids": ids, "codes": list(required)},
            ).fetchall():
                state, left = state_of(str(verification), cast(date | None, expiry), on)
                docs.setdefault(cast(UUID, vid), {})[str(code)] = (
                    state,
                    left,
                    cast(date | None, expiry),
                )
        result: dict[UUID, Eligibility] = {}
        for vid, status, availability, available in vendors:
            vid = cast(UUID, vid)
            reasons: list[Reason] = []
            warnings: list[Reason] = []
            if status != "active":
                reasons.append(Reason(code="status", text=f"Vendor is {_label(str(status))}"))
            if vid in disabled and availability == "global":
                where, who = disabled[vid]
                reasons.append(Reason(code="disabled_here", text=f"Disabled in {where} by {who}"))
            elif not available:
                reasons.append(Reason(code="not_available", text=f"Not available in {unit_name}"))
            mine = docs.get(vid, {})
            found = {c: mine.get(c, ("missing", None, None)) for c in sorted(required)}
            for state, code, text in (
                ("missing", "document_missing", "{} missing"),
                ("rejected", "document_rejected", "{} rejected"),
                ("expired", "document_expired", "{} expired {}"),
            ):
                for type_code, (st, _left, expiry) in found.items():
                    if st == state:
                        reasons.append(
                            Reason(
                                code=code,
                                text=text.format(
                                    required[type_code], _on(expiry) if expiry else ""
                                ).strip(),
                                document_type=type_code,
                                date=expiry,
                            )
                        )
            for type_code, (st, left, expiry) in found.items():
                if st == "expiring":
                    warnings.append(
                        Reason(
                            code="document_expiring",
                            text=f"{required[type_code]} expiring in {left} Days",
                            document_type=type_code,
                            date=expiry,
                        )
                    )
                elif st == "unverified":
                    warnings.append(
                        Reason(
                            code="document_unverified",
                            text=f"{required[type_code]} unverified",
                            document_type=type_code,
                            date=expiry,
                        )
                    )
            result[vid] = Eligibility(eligible=not reasons, reasons=reasons, warnings=warnings)
        return result
