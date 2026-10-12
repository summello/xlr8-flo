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
| `requisition.approve`, `project.approve`, `budget.approve` (all existing) | — | — | **approval eligibility**: each `DocumentAdapter` names its `approve_permission`; holding it at the document's scope makes a user *eligible*, step membership makes them an *approver* (E10-S04) |
| `workflow.read`, `workflow.manage` | new | E10-S01 | organization (definitions), or the BU/OU an override binds to |
| `approval.delegate`, `approval.admin` | new | E10-S08 | own tasks; organization (reassign, escalate, view all) |

**Every new permission is added in three places in the same story:** `PERMISSION_CODES`,
`BASELINE_ROLE_PERMISSIONS` (organization-administrator always; the packet names any other
baseline role), and the migration, which inserts the `permission` row **and** grants it to existing
tenants' system roles by role code, so current tenants get it too. **New permissions go in their own permissions-only migration** (`<next+1>_<module>_permissions.py`,
touching only `permission`, `role` and `role_permission`, the E09-S05 pattern), separate from the
story's table migration, so `tests/authz/conftest.py` can apply it right after RBAC without the
story's tables. This applies to E09-S05, E10-S01 and E10-S08 (E10-S13 adds no permission: `budget.approve` exists). **The same story updates the permission catalogue tests**, which hardcode the set: `apps/api/tests/authz/test_models.py` (`EXPECTED_PERMISSIONS`), and `tests/authz/test_migration.py` with the migration list `tests/authz/conftest.py` applies (pre-flight finding on E10-S08). No other new permission without
a packet amendment. Downgrades delete `role_permission` rows first, then `permission` rows (the
E06-S03 precedent, `migrations/20261008_0025_project_lifecycle.py`).

**Auditor is read-only by construction.** `BASELINE_ROLE_PERMISSIONS[auditor]` is every catalogue
permission whose action is `read` or `view`, and `tests/authz/test_models.py::test_auditor_permissions_are_read_only_by_construction`
pins it (the requirements role table: the Auditor "reads configuration, decisions, transaction history
and audit records"). A new permission with action `read` or `view` therefore reaches Auditors the moment
it joins `PERMISSION_CODES`, and the story's permissions migration **must also backfill the system
`auditor` role in existing tenants**, or new tenants and old tenants diverge. A permission with any
other action never reaches Auditor. Add the code to `EXPECTED_PERMISSIONS`; the by-construction test then
passes unchanged, and no special case for Auditor is written anywhere. Among M2's new permissions only
`workflow.read` has such a suffix.

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
requisition's status, after locking the project's funding row (`SELECT … FOR UPDATE`, invariant 3).

**Funding rounds (D-M2-15).** A project-linked requisition reserves at submit and may reserve again
after a reject-to-draft and resubmit, so it holds **at most one live reservation at a time**:
`requisition.reservation_entry_id bigint NULL` and `requisition.funding_round int NOT NULL DEFAULT 0`.
Each reservation starts a new round. Idempotency keys:

- reserve: `req:<id>:reserve:<round>`
- release: `req:<id>:release:<round>:<reason>`, reason ∈ `withdrawn | rejected | returned_to_draft | cancelled | expired | resubmit`
- partial reduce: `req:<id>:release:<round>:reduce:<version>`

A resubmit that changes the estimate releases the round's remaining reservation and reserves the new
total under the same lock (round + 1). Exactly-once is **per round**, guaranteed by
`release_reservation`'s remaining-never-below-zero rule plus these keys (FIN-011). A standalone
requisition has no reservation until approval (D-M2-5), which is its round 1.

## M. Settings added in M2 (register in `modules/org/settings.py` `SETTING_DEFAULTS` and `SETTING_VALIDATORS`)

| Key | Default | Valid | Packet |
|---|---|---|---|
| `approval.max_depth` | `10` | integer 1..50 | E10-S03 |
| `requisition.expiry_days` | `null` | `null` or integer 1..3650 | E09-S07 |
| `approval.separation_of_duty` | `true` | boolean | E10-S04 |

## O. Optimistic concurrency (DATA-002)

Every editable resource carries an integer `version` (or `row_version`), returned as the `ETag`.
A write needs `If-Match`: missing is **400 `BAD_REQUEST`** ("Supply If-Match with the <thing>
version."), a mismatch is **409 `CONFLICT`, `checks.problem = "stale_version"`** (the
`ProjectService.transition` precedent, `modules/projects/service.py`). There is no 428: the error
taxonomy is closed (convention D).

## P. Authorizing approval routes (the CI route gate)

Every non-public route must carry a `kernel.authz` guard in its dependency graph:
`route_authorization_failures` (`kernel/authz.py`), run in CI by `apps/api/scripts/check_route_authz.py`.
Approval routes cannot name a static permission, because the permission depends on the document type
behind an instance or task. **E10-S05 adds two sanctioned guards to `kernel/authz.py`**, both
setting the gate's marker and both answering through the same outcome rules as `require`
(convention A), by factoring `require`'s body into one shared `_decide(request, permission, target)`:

- **`require_resolved(resolve)`**: `resolve` is a FastAPI dependency that loads the record from the
  path and returns `(permission, AuthorizationTarget)`. For approvals: instance or task, then
  `document_type`, then the adapter's permission at the document snapshot's `target`. A missing or
  foreign record makes `resolve` raise 404 itself, before any decision.
- **`require_member()`**: for **self-scoped lists only** (the caller's inbox, the caller's
  delegations). It requires an authenticated session in a selected organization. The handler then
  filters strictly by the caller's identity and by live eligibility, so it can return nothing the
  caller could not reach through `require_resolved`. A route that returns another user's data
  never uses it.
  Exception (E10-S08 amendment 1): a mutation that only **reduces** authority, such as revoking a
  delegation, may use it when the service performs an explicit ownership-or-permission check in
  the same transaction (404 for absent or foreign, 403 otherwise). Route and per-role tests are
  named in the packet.

`DocumentAdapter` therefore carries **`read_permission: str`** (for example `requisition.view`)
instead of a `can_read()` callable; read routes use `require_resolved` with `read_permission`, and
decision routes use the adapter's `approve_permission`. `@public_route` is never used for approval
routes.

## Q. People are shown by email in M2 (D-M2-16)

`identity` has no name column (only `invitation.full_name` stores one, and it is not copied to the
identity). Wherever a packet shows a person (approver pickers, requester, "on behalf of", decision
trails, explanations), M2 shows the **email**, and `q` filters match an email prefix. Adding a
profile name is an operator follow-up.

## R. Lock order (deadlock law, cross-lane)

Every transaction that touches approvals and money takes locks in this one order, never another:

1. the **approval instance** row (`SELECT … FOR UPDATE`; E10-S06's decision path starts here),
2. the **`project_balance`** rows, **ascending by project id** (a sub-project and its parent are locked parent-first only because ids sort that way; never in discovery order),
3. the **requisition** (or other document) row.

A document module that starts, withdraws or cancels an approval takes the instance lock **before** its
own balance and document locks, and must not pre-lock the document before calling
`approvals.service.withdraw`, `start` or any decision function. A path that locks in another order
deadlocks under load (Postgres `40P01`). Each story that takes more than one of these locks names its
order in its packet and ships a two-connection test that interleaves two such transactions and asserts
neither fails with a deadlock.

## N. Parallel lanes in M2

E09 and E10 run as two lanes until E09-S06, which needs E10-S05. Each story's migration takes the
next revision after the head **at the time the story starts**; `finish.sh` merges the milestone into
the story before gating (AGENTS.md 2.5), and the second lane to merge rebases its first revision's
`down_revision` onto the new head and reruns `python -m flo.kernel.migrate --check`.
