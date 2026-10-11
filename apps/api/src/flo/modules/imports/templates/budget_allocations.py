"""Append-only allocations keyed independently of the upload batch."""

import re
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Literal, cast
from uuid import UUID

from pydantic import ValidationError

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.money import currency_exponent
from flo.modules.budget.schemas import AllocationCreate
from flo.modules.budget.service import allocate, entry_by_idempotency_key
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.imports.handlers import Issue, RowContext, RowPlan
from flo.modules.imports.schemas import Column, Template
from flo.modules.projects.service import get_by_external_ref

TEMPLATE = Template(
    name="budget_allocations",
    version=1,
    key_columns=("row_key",),
    columns=(
        Column(name="row_key", type="text", required=True, example="ALLOCATION-2026-001"),
        Column(name="project_ref", type="text", required=True, example="PLANT-2026"),
        Column(name="amount", type="decimal", required=True, example="1000.00"),
        Column(
            name="currency",
            type="code",
            required=True,
            example="USD",
            accepted_codes_source="/api/v1/master/currency",
        ),
        Column(name="effective_date", type="date", required=True, example="2026-10-10"),
        Column(
            name="reason", type="text", required=True, example="Initial approved project funding"
        ),
        Column(name="evidence_ref", type="text", example=""),
    ),
)


def row_key(context: RowContext, values: dict[str, object]) -> str:
    return (
        "import:"
        + sha256(
            f"{context.scope.org_id}:budget_allocations:{values.get('row_key', '')}".encode()
        ).hexdigest()
    )


def body(values: dict[str, object]) -> AllocationCreate:
    return AllocationCreate.model_validate(
        {
            "amount": str(values.get("amount", "")),
            "currency": values.get("currency", ""),
            "effective_date": values.get("effective_date"),
            "reason": values.get("reason", ""),
            "evidence_ref": values.get("evidence_ref") or None,
        }
    )


class BudgetAllocationsHandler:
    name = "budget_allocations"
    parsed_columns = frozenset({"amount"})

    def plan_row(self, context: RowContext, row_no: int, values: dict[str, object]) -> RowPlan:
        issues: list[Issue] = []
        amount_text = str(values.get("amount", ""))
        currency = str(values.get("currency", ""))
        amount: Decimal | None = None
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", amount_text) or Decimal(amount_text) <= 0:
            issues.append(
                Issue(
                    "amount",
                    "invalid_amount",
                    f"Amount {amount_text!r} must be a positive decimal string.",
                )
            )
        else:
            amount = Decimal(amount_text)
        try:
            exponent = currency_exponent(currency)
            if amount is not None and len(amount_text.partition(".")[2]) > exponent:
                issues.append(
                    Issue(
                        "amount",
                        "amount_precision",
                        f"Amount {amount_text!r} exceeds {currency}'s {exponent} decimals.",
                    )
                )
        except ValueError:
            issues.append(Issue("currency", "unknown_code", f"Unknown currency {currency!r}."))
        ref = str(values.get("project_ref", ""))
        project = get_by_external_ref(context.connection, context.scope, ref)
        if project is None:
            issues.append(
                Issue("project_ref", "unknown_project", f"Unknown project reference {ref!r}.")
            )
        else:
            if currency != project.currency:
                issues.append(
                    Issue(
                        "currency",
                        "currency_mismatch",
                        f"Currency {currency!r} differs from project {project.currency}.",
                    )
                )
            if not context.can(
                "budget.allocate",
                cast(
                    TargetProtocol,
                    AuthorizationTarget(ScopeType.PROJECT, project.id, "project", project.id),
                ),
            ):
                issues.append(
                    Issue(
                        "project_ref",
                        "permission_denied",
                        f"No budget.allocate permission for {ref!r}.",
                    )
                )
        try:
            body(values)
        except ValidationError as error:
            for error_entry in error.errors():
                column = str(error_entry["loc"][0])
                if not any(issue.column == column for issue in issues):
                    issues.append(Issue(column, "invalid_value", str(error_entry["msg"])))
        action: Literal["create", "skip", "error"] = "create"
        entry = entry_by_idempotency_key(
            context.connection, context.scope, row_key(context, values)
        )
        if entry is not None:
            if project is not None and (
                entry.project_id,
                entry.amount,
                entry.currency,
                entry.effective_date,
                entry.reason,
            ) == (
                project.id,
                amount,
                currency,
                cast(date | None, values.get("effective_date")),
                values.get("reason"),
            ):
                action = "skip"
            else:
                issues.append(
                    Issue(
                        "row_key",
                        "row_key_conflict",
                        "This row key already posted different content. "
                        "Use a new row key for a new allocation.",
                    )
                )
        if issues:
            action = "error"
        return RowPlan(action, tuple(issues), {"project_ref": ref, "amount": amount_text}, None)

    def apply_row(
        self, context: RowContext, plan: RowPlan, values: dict[str, object]
    ) -> tuple[str, UUID | int]:
        project = get_by_external_ref(context.connection, context.scope, str(values["project_ref"]))
        if project is None:
            raise RuntimeError("validated project no longer exists")
        result = allocate(
            context.connection,
            context.scope,
            project.id,
            body(values),
            context.actor_id,
            row_key(context, values),
            permitted_at=lambda parent_id: context.can(
                "budget.allocate",
                cast(
                    TargetProtocol,
                    AuthorizationTarget(ScopeType.PROJECT, parent_id, "project", parent_id),
                ),
            ),
        )
        return "ledger_entry", next(
            entry.id for entry in result.entries if entry.project_id == project.id
        )
