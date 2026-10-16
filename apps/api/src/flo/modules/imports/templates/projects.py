"""Service-backed project imports with ordered parents and stable identities."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from pydantic import ValidationError

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.config import Settings
from flo.kernel.errors import ProblemError
from flo.kernel.money import currency_exponent
from flo.modules.approvals.service import require_workflow, start
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    AuthorizationTarget,
    IdentityAuthorizationConnection,
    ScopeType,
    user_id_by_email,
)
from flo.modules.imports.handlers import Issue, RowContext, RowPlan
from flo.modules.imports.schemas import Column, Template
from flo.modules.org.service import MasterCodeUnusable, assert_usable, list_currencies, unit_by_code
from flo.modules.projects.schemas import (
    ProjectCreate,
    ProjectPatch,
    ProjectRead,
    TransitionCreate,
)
from flo.modules.projects.service import ProjectService, get_by_external_ref

TEMPLATE = Template(
    name="projects",
    version=2,
    key_columns=("external_ref",),
    columns=(
        Column(name="external_ref", type="text", required=True, example="PLANT-2026"),
        Column(
            name="bu_code",
            type="code",
            required=True,
            example="BU",
            accepted_codes_source="/api/v1/org/units",
        ),
        Column(name="parent_external_ref", type="text", example=""),
        Column(name="name", type="text", required=True, example="Plant expansion"),
        Column(name="description", type="text", example="Expand production capacity"),
        Column(
            name="department_code",
            type="code",
            required=True,
            example="D",
            accepted_codes_source="/api/v1/master/department",
        ),
        Column(
            name="ledger_account_code",
            type="code",
            required=True,
            example="L",
            accepted_codes_source="/api/v1/master/ledger_account",
        ),
        Column(
            name="currency",
            type="code",
            required=True,
            example="USD",
            accepted_codes_source="/api/v1/master/currency",
        ),
        Column(name="planned_start", type="date", example="2026-10-10"),
        Column(name="planned_end", type="date", example="2027-10-10"),
        Column(name="owner_email", type="text", example=""),
        Column(name="budget_amount", type="decimal", example="250000.00"),
        Column(name="sponsor_emails", type="text", example="a@example.com;b@example.com"),
    ),
)


def text(values: dict[str, object], key: str) -> str:
    return str(values.get(key, "")).strip()


def sponsors(context: RowContext, values: dict[str, object]) -> tuple[list[UUID], list[Issue]]:
    """Resolve `sponsor_emails` (semicolon separated); unknown addresses become row issues."""
    found: list[UUID] = []
    issues: list[Issue] = []
    for email in filter(None, (e.strip() for e in text(values, "sponsor_emails").split(";"))):
        user = user_id_by_email(
            cast(IdentityAuthorizationConnection, context.connection), context.scope, email
        )
        if user is None:
            issues.append(Issue("sponsor_emails", "unknown_sponsor", f"Unknown sponsor {email!r}."))
        elif user not in found:
            found.append(user)
    if len(found) > 10:
        issues.append(Issue("sponsor_emails", "too_many_sponsors", "Name at most 10 sponsors."))
    return found, issues


class ProjectsHandler:
    name = "projects"
    parsed_columns = frozenset({"budget_amount"})

    def plan_row(self, context: RowContext, row_no: int, values: dict[str, object]) -> RowPlan:
        issues: list[Issue] = []
        ref = text(values, "external_ref")
        unit = unit_by_code(context.connection, context.scope, text(values, "bu_code"))
        if unit is None:
            issues.append(
                Issue("bu_code", "unknown_bu", f"Unknown BU {text(values, 'bu_code')!r}.")
            )
        owner_email = text(values, "owner_email")
        owner = (
            user_id_by_email(
                cast(IdentityAuthorizationConnection, context.connection),
                context.scope,
                owner_email,
            )
            if owner_email
            else None
        )
        if owner_email and owner is None:
            issues.append(Issue("owner_email", "unknown_owner", f"Unknown owner {owner_email!r}."))
        codes: dict[str, str] = {}
        for column, kind in (
            ("department_code", "department"),
            ("ledger_account_code", "ledger_account"),
        ):
            value = text(values, column)
            try:
                codes[column] = assert_usable(
                    context.connection, context.scope, kind, value, datetime.now(UTC).date()
                ).code
            except MasterCodeUnusable as error:
                issues.append(
                    Issue(
                        column,
                        "unknown_code" if error.reason == "unknown" else "inactive_code",
                        f"Code {value!r} is {error.reason}. Choose a usable code.",
                    )
                )
        currency = text(values, "currency")
        if currency not in {item.code for item in list_currencies()}:
            issues.append(Issue("currency", "unknown_code", f"Unknown currency {currency!r}."))
        parent_ref = text(values, "parent_external_ref")
        parent = (
            get_by_external_ref(context.connection, context.scope, parent_ref)
            if parent_ref
            else None
        )
        planned = cast(dict[str, object], context.scratch.setdefault("planned", {}))
        if parent_ref and parent is None and parent_ref not in planned:
            issues.append(
                Issue(
                    "parent_external_ref",
                    "parent_not_defined",
                    f"Parent `{parent_ref}` is not in the system and not listed on an earlier row. "
                    "List parents before children.",
                )
            )
        project = get_by_external_ref(context.connection, context.scope, ref)
        if unit is not None:
            target = (
                AuthorizationTarget(ScopeType.BU, unit.id)
                if project is None
                else AuthorizationTarget(ScopeType.PROJECT, project.id, "project", project.id)
            )
            if not context.can(
                "project.create" if project is None else "project.update",
                cast(TargetProtocol, target),
            ):
                issues.append(
                    Issue(
                        "external_ref", "permission_denied", f"No project permission for {ref!r}."
                    )
                )
        if project is None:
            in_batch = bool(parent_ref) and parent is None and parent_ref in planned
            issues.extend(self.budget_issues(context, values, unit, currency, parent, in_batch))
        elif text(values, "budget_amount") or text(values, "sponsor_emails"):
            issues.append(
                Issue(
                    "budget_amount",
                    "budget_not_applied",
                    f"Budget and sponsors apply only to new projects, so {ref!r} keeps what it "
                    "has. Edit a draft's budget and sponsors on the project.",
                    "warning",
                )
            )
        mutable: dict[str, object] = {
            "name": text(values, "name"),
            "description": text(values, "description") or None,
            "planned_start": values.get("planned_start"),
            "planned_end": values.get("planned_end"),
        }
        if owner_email:
            mutable["owner_id"] = owner
        if not 1 <= len(str(mutable["name"])) <= 200:
            issues.append(
                Issue("name", "invalid_name", "Project name must contain 1 to 200 characters.")
            )
        try:
            ProjectCreate.model_validate(
                mutable
                | {
                    "bu_id": unit.id if unit else UUID(int=0),
                    "external_ref": ref,
                    "department_code": codes.get("department_code", ""),
                    "ledger_account_code": codes.get("ledger_account_code", ""),
                    "currency": currency,
                }
            )
        except ValidationError as error:
            for entry in error.errors():
                issues.append(Issue(str(entry["loc"][0]), "invalid_value", str(entry["msg"])))
        start, end = (
            cast(date | None, mutable["planned_start"]),
            cast(date | None, mutable["planned_end"]),
        )
        if start is not None and end is not None and end < start:
            issues.append(Issue("planned_end", "invalid_dates", "Planned end precedes start."))
        action: Literal["create", "update", "skip", "error"] = "create"
        token = None
        if project is not None:
            immutable = {
                "bu_code": (unit.id if unit else None, project.bu_id),
                "parent_external_ref": (parent.id if parent else None, project.parent_id),
                "department_code": (codes.get("department_code"), project.department_code),
                "ledger_account_code": (
                    codes.get("ledger_account_code"),
                    project.ledger_account_code,
                ),
                "currency": (currency, project.currency),
            }
            for column, (desired, existing) in immutable.items():
                if desired != existing or (
                    column == "parent_external_ref" and parent_ref and parent is None
                ):
                    issues.append(
                        Issue(
                            column,
                            "immutable_field_changed",
                            f"Immutable column {column} differs for {ref!r}.",
                        )
                    )
            action = (
                "skip"
                if all(getattr(project, key) == value for key, value in mutable.items())
                else "update"
            )
            if action == "update":
                token = str(project.version)
                if owner_email and owner != project.owner_id:
                    issues.append(
                        Issue(
                            "owner_email",
                            "owner_changed",
                            f"Owner changes to {owner_email!r}.",
                            "warning",
                        )
                    )
        if any(issue.severity == "error" for issue in issues):
            action, token = "error", None
        elif action == "create":
            planned[ref] = True
        return RowPlan(
            action, tuple(issues), {"external_ref": ref, "name": str(mutable["name"])}, token
        )

    def budget_issues(
        self,
        context: RowContext,
        values: dict[str, object],
        unit: object,
        currency: str,
        parent: ProjectRead | None,
        parent_in_batch: bool,
    ) -> list[Issue]:
        """A row with a budget is created and submitted: it needs sponsors, the right to submit
        and a published `project` workflow, so allocation happens on approval (E22-S01)."""
        amount_text = text(values, "budget_amount")
        found, issues = sponsors(context, values)
        if not amount_text:
            return issues + (
                [Issue("budget_amount", "budget_required", "Enter the budget to submit.")]
                if text(values, "sponsor_emails")
                else []
            )
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", amount_text) or Decimal(amount_text) <= 0:
            issues.append(
                Issue(
                    "budget_amount",
                    "invalid_amount",
                    f"Budget {amount_text!r} must be a positive decimal.",
                )
            )
        else:
            try:
                if len(amount_text.partition(".")[2]) > currency_exponent(currency):
                    issues.append(
                        Issue(
                            "budget_amount",
                            "amount_precision",
                            f"Budget {amount_text!r} exceeds {currency}'s decimals.",
                        )
                    )
            except ValueError:
                pass  # the currency column already carries the issue
        if not found:
            issues.append(Issue("sponsor_emails", "sponsor_required", "Name at least one sponsor."))
        if parent_in_batch or parent is not None:
            issues.extend(self.main_issues(context, amount_text, parent, parent_in_batch))
        unit_id = getattr(unit, "id", None)
        if unit_id is not None:
            if not context.can(
                "project.submit", cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit_id))
            ):
                issues.append(
                    Issue("budget_amount", "permission_denied", "No project.submit permission.")
                )
            try:
                require_workflow(context.connection, context.scope, "project", unit_id, "Projects")
            except ProblemError as error:
                issues.append(Issue("budget_amount", "workflow_not_set_up", str(error.detail)))
        return issues

    def main_issues(
        self, context: RowContext, amount_text: str, parent: ProjectRead | None, in_batch: bool
    ) -> list[Issue]:
        """A child draws its budget from the main project when approved, so the main project
        must already hold the funds: one listed in this file is only submitted, not funded."""
        if in_batch:
            return [
                Issue(
                    "budget_amount",
                    "main_not_funded",
                    "The main project is in this file, so it holds no funds until it is "
                    "approved. Import this child after the main project is approved.",
                )
            ]
        try:
            amount = Decimal(amount_text)
        except ArithmeticError:
            return []  # the amount column already carries the issue
        if parent is None or amount <= 0:
            return []
        try:
            ProjectService(context.connection, context.scope).check_main_funds(parent.id, amount)
        except ProblemError as error:
            return [Issue("budget_amount", "insufficient_budget", str(error.detail))]
        return []

    def apply_row(
        self, context: RowContext, plan: RowPlan, values: dict[str, object]
    ) -> tuple[str, UUID]:
        ref = text(values, "external_ref")
        service = ProjectService(context.connection, context.scope, context.actor_id, Settings())
        mutable: dict[str, object] = {
            "name": text(values, "name"),
            "description": text(values, "description") or None,
            "planned_start": values.get("planned_start"),
            "planned_end": values.get("planned_end"),
        }
        if text(values, "owner_email"):
            mutable["owner_id"] = user_id_by_email(
                cast(IdentityAuthorizationConnection, context.connection),
                context.scope,
                text(values, "owner_email"),
            )
        if plan.action == "update":
            project = get_by_external_ref(context.connection, context.scope, ref)
            if project is None:
                raise RuntimeError("validated project no longer exists")
            result = service.update(
                project.id, ProjectPatch.model_validate(mutable), plan.state_token
            )
        else:
            unit = unit_by_code(context.connection, context.scope, text(values, "bu_code"))
            if unit is None:
                raise RuntimeError("validated unit no longer exists")
            parent_ref = text(values, "parent_external_ref")
            parent = (
                get_by_external_ref(context.connection, context.scope, parent_ref)
                if parent_ref
                else None
            )
            created = cast(dict[str, UUID], context.scratch.setdefault("created", {}))
            result = service.create(
                ProjectCreate.model_validate(
                    mutable
                    | {
                        "external_ref": ref,
                        "bu_id": unit.id,
                        "parent_id": (parent.id if parent else created[parent_ref])
                        if parent_ref
                        else None,
                        "department_code": text(values, "department_code"),
                        "ledger_account_code": text(values, "ledger_account_code"),
                        "currency": text(values, "currency"),
                        "budget": {
                            "amount": text(values, "budget_amount"),
                            "currency": text(values, "currency"),
                        }
                        if text(values, "budget_amount")
                        else None,
                        "sponsor_ids": sponsors(context, values)[0],
                    }
                )
            )
            created[ref] = result.id
            if text(values, "budget_amount"):
                self.submit(context, service, result.id, result.version)
        return "project", result.id

    def submit(
        self, context: RowContext, service: ProjectService, project_id: UUID, version: int
    ) -> None:
        target = cast(
            TargetProtocol,
            AuthorizationTarget(ScopeType.PROJECT, project_id, "project", project_id),
        )
        service.transition(
            project_id,
            TransitionCreate(to="approval_pending"),
            str(version),
            lambda permission: context.can(permission, target),
        )
        start(
            context.connection,
            context.scope,
            document_type="project",
            document_id=project_id,
            actor_id=context.actor_id,
            resolver=AuthorizationResolver(
                cast(AuthorizationConnection, context.connection), context.scope
            ),
        )
