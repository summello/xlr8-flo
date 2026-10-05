# M1 conventions (apply to every M1 story packet)

Written by Opus after the first M1 author runs blocked on the same ambiguities. A packet that
says something different wins; otherwise these rules apply.

## A. Authorization outcomes — use `kernel.authz.require(permission, target_resolver)`

The existing guard (`apps/api/src/flo/kernel/authz.py`) is the only way a business route is
authorized. Its outcome is fixed:

| Situation | Response |
|---|---|
| allowed | proceed |
| the caller holds **some** grant covering the target's scope but not the permission | **403** |
| the caller holds **no** grant covering the target's scope, and the target is a record or a non-org scope | **404** (existence concealed) |
| an org-level target (collection list/create) and not allowed | **403** |
| the record belongs to another tenant | **404**, always |

Wherever a packet says "403" it means the first or third row; "404" means the second or fifth.
Tests must cover each row that the route can reach (no grant → 404 for record routes; wrong
permission with a grant in scope → 403; foreign tenant → 404).

## B. Which scope a route targets

- Collection and create routes target the **organization** scope, unless the packet names a BU
  or org unit (project create targets the new project's BU/OU scope).
- Record routes target the record's own authorization scope: an org unit (registered as a
  `bu`-type scope, D-M1-20), or a project (registered as a `project` scope whose parent is its
  BU/OU or its parent project, E06-S01). A grant at an ancestor scope covers descendants
  **through the authorization scope tree only**.
- An organization-level grant covers everything in the org.
- Scopes are registered by calling `identity.service.register_scope(...)` in the same
  transaction that creates the thing. No story edits `modules/identity/` unless its packet says so.

## C. Everything else

- Amounts on the wire are decimal **strings**; JSON numbers for money are a 422.
- **Idempotency-Key applies to state-changing `POST` only** (AGENTS.md 3.1; the kernel middleware ignores other methods and this story set does not change it). `PUT` and `DELETE` are idempotent by semantics (a repeated `PUT` stores the same value; a repeated `DELETE` of something already absent is 204 and writes nothing), `PATCH` uses `If-Match` where the packet says so. Where a packet shows `(Idempotency-Key)` on a non-POST route, ignore that annotation. Each write audits in the same transaction.
- **File scope:** a packet's *Files* list names the main files. Creating additional files **inside the module directories it names and under `tests/`** is allowed, and so is registering a new router where existing routers are included, and the regenerated OpenAPI client. Editing other modules or `kernel/` is **not** allowed unless the packet names that file; if you believe you need to, write `notes.blocked` with the exact reason.
- A new route needs an entry in `apps/api/tests/isolation/test_route_coverage.py::COVERED`
  pointing at a real, named foreign-tenant test.
- Migrations: next revision after the current head, reversible, with a data-preservation test.
- If something is genuinely undecided after reading the packet **and** this file, write
  `notes.blocked` with the specific question and stop. Do not guess at a money, state or
  authorization rule, but do not block on anything these two documents answer.

## D. Error responses — do not extend the taxonomy

`kernel/errors` is a closed taxonomy and **no M1 story edits it** (except where a packet says so).
The problem names written in packets (`duplicate_code`, `PeriodClosed`, `StaleVersion`, ...) are
**labels**. Raise `ProblemError` with the existing code that matches the status, put the plain-language
explanation in `detail` (it must say what happened and what to do), and carry the label in
`checks={"problem": "<label as snake_case>"}`. Tests assert status, code and `checks["problem"]`.

| Status | `ErrorCode` | Typical labels |
|---|---|---|
| 409 (state or uniqueness conflict) | `CONFLICT` | duplicate_code, PeriodClosed, StaleVersion, InvalidTransition, ClosingBlocked, TransferNotEligible, FundFromParent, ProjectNotFunding, ReleaseExceedsReservation, FundingModeLocked, NotValidated, HasErrors, StaleValidation, ImportKeyConflict, TransferInvariantViolation, address_overlap, ParentInsufficient |
| 409 (money) | `INSUFFICIENT_BUDGET` | InsufficientBudget |
| 422 (bad values) | `VALIDATION_FAILED` (with `errors` field entries where a field is at fault) | depth limit, CurrencyMismatch, FxRateMissing, unknown kind or key, attribute-schema errors, formula-in-file, structure errors |
| 422 (key reused for different content) | `IDEMPOTENCY_KEY_REUSED` | idempotency_conflict |
| 400 | `BAD_REQUEST` | malformed request not about field values |
| 403 / 404 | `FORBIDDEN` / `NOT_FOUND` | see section A |

If a packet needs a status with no entry (E08-S01's 413 and 415 are the only ones), it names the
taxonomy addition explicitly.

## E. Cross-module imports

The import-linter contract allows `flo.modules.<a>` to import `flo.modules.<b>.service` and
`flo.modules.<b>.schemas`, and nothing else of `<b>` (not `models`, `repo`, `db`, or the package
root). Import the service function, not the package. Kernel imports are always fine.
