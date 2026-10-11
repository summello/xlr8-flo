# Author traps

Every author prompt points here. Each line below cost at least one blocked round or one red CI
run on M0 or M1. Read it with your packet and `docs/m1-conventions.md`; a packet that says
otherwise wins.

## Before you write code

- **Trace the route through the real middleware stack** in `apps/api/src/flo/api/health.py`
  (the `install_*` block; the last installed runs first). A request meets: browser security,
  problem details, CSRF, session, MFA gate, organization selection, tenant context,
  idempotency. A route the MFA or organization-selection gate would block needs its path on
  that gate's allow-list, and the packet must say so. If it does not, that is a real block.
- **Idempotency:** state-changing `POST` only. Public routes with no tenant (sign-in, invitation
  accept) are in `IDEMPOTENCY_BYPASS_PATHS` and are never replayed. `/internal/*` POSTs require
  the header but are not replayed (`docs/m1-conventions.md` C).
- **`org_id` never comes from a body.** `kernel/tenancy/guards.py` rejects it; its one exception
  is listed there. Do not add another.
- **403 vs 404** is fixed by `kernel.authz.require` (`docs/m1-conventions.md` A). A foreign
  tenant is always 404.
- **Kernel files the packet does not name are out of scope.** If you need one, block with the
  exact file and reason.
- **The error taxonomy is closed** (`docs/m1-conventions.md` D).

## Tests that pass locally and fail in CI or in production

- Test public and internal routes through the real `flo.api.health.app` with the CSRF cookie and
  header pair. A private `FastAPI()` skips CSRF and idempotency; the cron tick shipped broken to
  production that way.
- **No machine paths.** Never write `/Users/`, `/private/tmp` or `/tmp/` into a test or fixture.
  Use `tmp_path`. CI is Linux.
- **No secret-shaped literals**, not even in a test. gitleaks scans history, so a key in an early
  commit fails CI after you remove it. Use `"A" * 43`.
- **A migration story appends its revision to `apps/api/tests/kernel/test_migrate.py`**, takes
  the head's date prefix plus the next number, and proves down then up preserves data.
- **New routes** go into `apps/api/tests/isolation/test_route_coverage.py::COVERED` with a real
  named foreign-tenant test.
- **Web:** tokens only, never a value from `design-system/canvas/`. Wait for mount before a visual
  assertion. Touch-target assertions allow sub-pixel rounding (43.99 passes 44).

## Never

- A CSRF exemption. Make the client take part in the protocol.
- Weakening an existing assertion to make a suite green.
- A new dependency.

## Validate before you report done

From the worktree root, with nothing else running against your database:

```
apps/api/.venv/bin/python agents/scripts/flo check
```

It recreates a database private to this worktree and runs every CI stage. For quick iteration on
one file:

```
export TEST_DATABASE_URL="$(apps/api/.venv/bin/python agents/scripts/flo db reset)"
cd apps/api && uv run --offline pytest -q tests/<file>
```

For every gate, guard or contract you add: break it, run its test, watch it fail, then restore
it with `git checkout -- <file>`. List each plant and its result in your final report.

## Blocking

Block (write `notes.blocked` in `task.json` and stop) only on a money rule, a state transition,
an authorization rule, or something `docs/requirements.md` marks open. When you block, **leave
your partial work uncommitted**; do not revert it.

Anything else the packet does not settle (copy, layout detail, a helper's name, a non-money
default): pick the conservative option, record it in `notes.followup`, and keep going.
