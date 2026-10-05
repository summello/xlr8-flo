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
- State-changing `POST` (and the packet's named `PUT`/`DELETE`) honour `Idempotency-Key`
  through the kernel middleware. Each write audits in the same transaction.
- A new route needs an entry in `apps/api/tests/isolation/test_route_coverage.py::COVERED`
  pointing at a real, named foreign-tenant test.
- Migrations: next revision after the current head, reversible, with a data-preservation test.
- If something is genuinely undecided after reading the packet **and** this file, write
  `notes.blocked` with the specific question and stop. Do not guess at a money, state or
  authorization rule, but do not block on anything these two documents answer.
