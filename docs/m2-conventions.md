# M2 conventions (apply to every M2 story packet)

`docs/m1-conventions.md` applies to M2 unchanged (authorization outcomes A, scope targets B,
wire and idempotency rules C, closed error taxonomy D, cross-module imports E, migration list F).
`docs/author-traps.md` applies too. This file adds what M2 shares. A packet that says something
different wins; otherwise these rules hold. Decisions cited as D-M2-n are in `docs/m2-decisions.md`.

## G. New modules and their boundaries

| Module | Owns | Stories |
|---|---|---|
| `modules/approvals/` | workflow definitions and versions, conditions, graph validation, eligibility, instances, tasks, decisions, delegation, explainability | E10-S01..S09, S13 |
| `modules/requisitions/` | requisition header, lines, catalogue, submission validation, lifecycle, revisions, links | E09-S01..S09 |
| `modules/collaboration/` | notes and attachments on any business document | E09-S10 |

Each module follows the M1 shape: `models.py` (repository), `schemas.py`, `service.py` (the only
file another module imports, with an explicit `__all__`), routers in `apps/api/src/flo/api/`.

## H. Approvals never import a document module: the document-adapter registry

`approvals` must not import `requisitions` (requisitions imports approvals to submit; the reverse
edge is a cycle). Instead `approvals.service` exposes:

```python
class DocumentAdapter(Protocol):
    document_type: str                                   # "requisition", "project", "budget_change"
    def snapshot(self, conn, scope, document_id) -> DocumentSnapshot: ...
    def on_outcome(self, conn, scope, document_id, outcome: Outcome, context: OutcomeContext) -> None: ...

def register_document_type(adapter: DocumentAdapter) -> None   # idempotent per document_type
```

`DocumentSnapshot` (in `approvals/schemas.py`) carries `version: int`, `requester_id: UUID`,
`target: AuthorizationTarget` (the BU/OU or project used for approver eligibility),
`attributes: dict[str, AttributeValue]` (for conditions, E10-S02) and `material: dict[str, str]`
(a stable digest input for E10-S07). Adapters live in their own module (`requisitions/approval.py`)
and are registered by one composition function, `apps/api/src/flo/api/document_types.py::register_document_types()`,
called once from `api/health.py` at import time and from `_run_jobs_tick` in `api/internal.py`
(the import-handler `register_builtin()` precedent, E08-S04).

**`on_outcome` runs on the decision's connection, inside the decision's transaction** (a nested
`tenant_transaction` is a savepoint). The final approval, the requisition transition, the
sub-project and the reservation commit together or not at all (WF-003). An exception in
`on_outcome` rolls the decision back and is answered as the exception's problem.

## I. Permissions added in M2 (seeded by the story that first needs them, in its migration)

**Reuse the shipped catalogue first.** `modules/identity/models.py` `PERMISSION_CODES` already
holds `requisition.create`, `requisition.edit`, `requisition.view` and `requisition.approve`, and
`BASELINE_ROLE_PERMISSIONS` grants them (requestor: create, edit, view; approver: approve, view;
organization-administrator: all). M2 uses those codes; inventing parallel ones would leave every
seeded role unable to act until someone hand-grants the new codes.

| Permission | New? | Seeded by | Checked on |
|---|---|---|---|
| `requisition.view` (read), `requisition.create`, `requisition.edit` (draft edits) | existing | — | the requisition's BU/OU, or its project |
| `requisition.submit`, `requisition.withdraw`, `requisition.cancel` | new | E09-S05 | the requisition's scope target |
| `requisition.approve`, `project.approve` (existing), `budget.approve` (new, E10-S13) | — | — | **approval eligibility**: each `DocumentAdapter` names its `approve_permission`; holding it at the document's scope makes a user *eligible*, step membership makes them an *approver* (E10-S04) |
| `workflow.read`, `workflow.manage` | new | E10-S01 | organization (definitions), or the BU/OU an override binds to |
| `approval.delegate`, `approval.admin` | new | E10-S08 | own tasks; organization (reassign, escalate, view all) |

**Every new permission is added in three places in the same story:** `PERMISSION_CODES`,
`BASELINE_ROLE_PERMISSIONS` (organization-administrator always; the packet names any other
baseline role), and the migration, which inserts the `permission` row **and** grants it to existing
tenants' system roles by role code, so current tenants get it too. No other new permission without
a packet amendment. Downgrades delete `role_permission` rows first, then `permission` rows (the
E06-S03 precedent, `migrations/20261008_0025_project_lifecycle.py`).

## J. Requisition statuses are fixed now, so no later story migrates the CHECK

`draft`, `approval_pending`, `sourcing_in_progress`, `sourced`, `awarded`, `open_for_purchase`,
`completed` (REQ-005), plus the terminals `rejected`, `withdrawn`, `cancelled`, `expired`.
E09-S01 creates the column with this full CHECK; E09-S05 owns the transition table. Only
`draft → approval_pending → sourcing_in_progress` and the terminal transitions are reachable in M2;
`sourced` onward are driven by M3 stories.

## K. Live membership (D-M2-2, D-M2-9)

A task names *who may decide* by rule (a role, a set of users, or a permission at the document's
scope), never by a stored member list. Eligibility is evaluated at decision time with the
production resolver (`AuthorizationResolver.holders`, E10-S04). Completion: `any` = first approval
by a current member; `all` = every current member has approved; `quorum(n)` = n approvals from
current members. Each decision row stores the member list it was evaluated against
(`members_at_decision`), so attribution never depends on today's membership.

## L. Money in M2

Requisition money is `estimate NUMERIC(18,4)` with `currency char(3)` beside it, and the currency
is **derived from the project** (D-M2-7), never accepted from a request body (a body carrying
`currency` is a 422 naming the field). Reservation and release go through
`budget.service.reserve` / `release_reservation` only, inside the transaction that changes the
requisition's status, with idempotency keys `req:<requisition_id>:reserve` and
`req:<requisition_id>:release:<reason>` (one release per reason, and at most one non-zero release
in total — FIN-011).

## M. Settings added in M2 (register in `modules/org/settings.py` `SETTING_DEFAULTS` and `SETTING_VALIDATORS`)

| Key | Default | Valid | Packet |
|---|---|---|---|
| `approval.max_depth` | `10` | integer 1..50 | E10-S03 |
| `requisition.expiry_days` | `null` | `null` or integer 1..3650 | E09-S05 |
| `approval.separation_of_duty` | `true` | boolean | E10-S04 |

## O. Optimistic concurrency (DATA-002)

Every editable resource carries an integer `version` (or `row_version`), returned as the `ETag`.
A write needs `If-Match`: missing is **400 `BAD_REQUEST`** ("Supply If-Match with the <thing>
version."), a mismatch is **409 `CONFLICT`, `checks.problem = "stale_version"`** (the
`ProjectService.transition` precedent, `modules/projects/service.py`). There is no 428: the error
taxonomy is closed (convention D).

## N. Parallel lanes in M2

E09 and E10 run as two lanes until E09-S06, which needs E10-S05. Each story's migration takes the
next revision after the head **at the time the story starts**; `finish.sh` merges the milestone into
the story before gating (AGENTS.md 2.5), and the second lane to merge rebases its first revision's
`down_revision` onto the new head and reruns `python -m flo.kernel.migrate --check`.
