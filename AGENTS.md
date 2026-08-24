# AGENTS.md — XLR8 FLO

Binding rules for every coding agent on this repository: **Claude Code (Opus)**, **Codex CLI**, **OpenCode CLI**, **OpenRouter (qwen-3-coder)**.

This file is read automatically by Codex CLI and OpenCode CLI. Claude Code reads it via `CLAUDE.md`. It is the single rulebook — if another document disagrees with this one, this one wins.

**Read before every task:** this file, `agents/project-memory.md`, and your story's design packet at `design/<STORY_ID>.md`.
Your packet is in your worktree at `design/<STORY_ID>.md` — packets are tracked, so it is there from the moment `flo start` creates the branch. Implement that packet exactly.

**Before any UI story, additionally:** `design-system/MASTER.md`, then `design-system/pages/<page>.md` if one exists for the page you are touching — the page file overrides the master where it says so.

---

## 0. Non-negotiables

1. **Never work outside a story.** Every change belongs to a story id in `agents/roadmap.yaml`. No story id, no commit.
2. **Never work outside your worktree.** `flo start` gave you a directory. Do not touch the parent repo or another agent's worktree.
3. **Never merge to `main`.** Only a milestone pull request reaches `main`, and only a human opens it.
4. **Never change a requirement.** If `docs/requirements.md` or `docs/claude-plan.md` is wrong, stop and report it in the task packet. Do not silently reinterpret it.
5. **Never invent scope.** Implement the design packet. Anything you think is missing goes in `notes.blocked` or `notes.followup`, not in the diff.
6. **Never add a dependency** without an entry in the PR body naming what it replaces and why stdlib or an existing dependency will not do.

---

## 1. Roles

| Agent | Authors | Reviews | Never |
|---|---|---|---|
| **Opus** (`claude-opus-5`, Claude Pro) | Design packets, arbitration patches | Final review on gated stories; whole-milestone diff before PR | Routine authoring — it is the scarcest capacity in the fleet |
| **Codex** (ChatGPT Plus) | Ledger, approvals engine, concurrency, migrations, security-sensitive backend | Any story | Boilerplate, CRUD screens, docs — its cap is too tight to spend there |
| **OpenCode-nemotron** (nemotron-ultra, free) | Default author for everything else | Any story | — |
| **OpenCode-ox** (ox-alpha, free) | Author when nemotron is the reviewer, or on fallback | Any story | — |
| **Qwen** (qwen-3-coder, OpenRouter, <$20/mo) | Small stories (`size: S`) | Any story, budget permitting | Exceeding the monthly cap in `agents/agents.yaml` |

**At least one reviewer on every story comes from the free pool** (`opencode-nemotron`, `opencode-ox`). Two free models exist precisely so that a free author still gets a free, independent reviewer — a rationed agent is never the reason a story cannot merge.

Assignment is computed, not chosen: `agents/scripts/flo assign <STORY_ID>`.

---

## 2. Workflow

### 2.1 As an author

```bash
flo start  E07-S03          # creates branch + worktree + task.json; prints the worktree path
cd ../xlr8flo-E07-S03
cat design/E07-S03.md        # your specification — implement exactly this
# ... implement ...
flo check                    # lint, types, tests, boundaries, a11y — run this before you claim done
flo submit                   # marks ready for review, prints the reviewer commands
```

Rules while authoring:

- Read the code the change touches **before** writing. Trace the real flow end to end. A small diff in the wrong place is a second bug.
- Reuse what exists. `kernel/` already has money, clock, errors, audit, idempotency, outbox, storage, email. Re-implementing one of those is an automatic rejection.
- Commit often inside your worktree with real messages. The branch is squashed on merge, so commit noise costs nothing and helps reviewers.
- **Run your own regression before submitting.** `flo check` must be green. Submitting red work wastes the reviewers' capacity, and two of the four agents are rate-limited.
- If you are blocked, write `notes.blocked` in `task.json` and stop. Do not guess at a financial rule, an authorization rule, or a state transition.

### 2.2 As a reviewer

```bash
flo review E07-S03           # prints the diff, the design packet, and the requirement IDs
```

You receive the diff, the design packet, and the cited requirement IDs. **You do not receive the author's rationale** — agreeing with reasoning you were shown is not review.

Write your verdict to `reviews/<STORY_ID>.<your-agent-id>.json`:

```json
{
  "story": "E07-S03",
  "reviewer": "opencode-ox",
  "verdict": "changes_requested",
  "ran_tests": true,
  "findings": [
    {
      "severity": "blocker",
      "file": "apps/api/src/flo/modules/budget/service.py",
      "line": 118,
      "requirement": "FIN-006",
      "problem": "Available balance is read before the FOR UPDATE lock is taken, so two concurrent reservations can both pass validation.",
      "fix": "Move the select().with_for_update() above the availability check and re-read inside the lock."
    }
  ]
}
```

Rules while reviewing:

- **Run the tests.** `ran_tests: false` makes your verdict advisory only and does not count toward the two-reviewer floor.
- One finding per problem. State the failure, not a preference.
- Severity: `blocker` (wrong, unsafe, or violates a cited requirement) · `major` (will break under a foreseeable condition) · `minor` (clarity, naming, dead code). Only `blocker` prevents merge.
- **Do not rewrite the code.** Review only. The author fixes.
- Check the requirement IDs the design cites are actually satisfied. A green test suite that tests the wrong thing is the most common agent failure mode.
- Silence is not approval. Every review produces a file.

### 2.3 Rounds and arbitration

Maximum **3** author↔reviewer rounds. If blockers remain, `flo escalate <STORY_ID>` hands the story, both reviews, and the diff to Opus for arbitration. Opus's ruling is final and is recorded in the task packet.

### 2.3b You are measured

`flo done` writes one immutable record per story to `agents/scorecard.json`, and `flo score`
renders `agents/SCORECARD.md`. Both are committed with the story, and CI fails if the rendered
scorecard drifts.

As an **author** you are measured on clean-merge rate (one review round, no gate failure), mean
rounds to merge, and *escapes* — blockers Opus found at final review, meaning two reviewers approved
code that was wrong.

As a **reviewer** you are measured on test compliance, blocker precision (blockers upheld ÷ blockers
raised — crying wolf costs you), catch rate (escapes on stories you approved), and thoroughness,
which saturates at two findings per review because more than that is not better.

These numbers route work; they do not rank agents. A low author score on `money` and a high one on
`crud` is a signal to change `allowed_kinds` in `agents/agents.yaml`, not a verdict.

### 2.4 Gate and merge

```bash
flo gate E07-S03             # Definition of Done, machine-checked
flo done E07-S03             # squash-merge into the milestone branch, update roadmap, drop the worktree
```

`flo done` refuses unless: `flo gate` is green, two independent reviewers with `ran_tests: true` have `verdict: approve`, and — for gated stories — Opus's final review is recorded.

---

## 3. Coding rules

### 3.1 Money — the rules that must never bend

- `Decimal` end to end. **`float` is banned** in `budget/`, `purchasing/`, `sourcing/`, `requisitions/` and is enforced by lint, not by review.
- `NUMERIC(18,4)` in Postgres. Every amount column has a currency column beside it.
- Rounding goes through `kernel/money.py`. Never `round()`, never `//`, never `%` on money.
- `ledger_entry`, `audit_log`, `stock_movement`, `approval_decision` are **append-only**. Database triggers enforce it. Corrections are reversal rows.
- Every budget-consuming command: `SELECT … FOR UPDATE` on the funding row **before** validating availability, ledger insert and balance update in one transaction.
- Every state-changing `POST` accepts `Idempotency-Key` and routes through the kernel middleware.
- Every external effect (email, webhook, API call) goes through the outbox. Never send from a request handler.

### 3.2 Tenancy and authorization

- `org_id` resolves from the session. Never from a URL, body or header.
- Every query goes through a scoped repository. An unscoped query does not type-check — do not add an escape hatch.
- Authorization is server-side on every protected operation. Hiding a UI control is not authorization.
- Access to another tenant's record returns **404**, never 403.
- Every new endpoint gets a test in the TEN-010 isolation suite.

### 3.3 Backend

- Routers parse, authorize, call one module service, serialize. **No business logic in `api/`.** A money conditional in a router is a rejection.
- A module imports another module's `service` and `schemas` only. Never its `models`, `repo` or `db`. `import-linter` enforces this in CI.
- Errors are RFC 9457 problem details from `kernel/errors.py`. Never leak a stack trace, query, path or internal hostname (SEC-011).
- Every migration is reversible and tested against a seeded database. Destructive migrations require an explicit `# irreversible:` comment and Opus review.
- Type hints everywhere; `mypy --strict` on `kernel/` and `modules/`.

### 3.4 Frontend

**`design-system/MASTER.md` is binding.** Tokens, type scale, density, motion curves, component specs and the UI definition of done all live there. Do not invent a colour, a spacing value, a duration, or a radius — if the token you need is missing, say so in `notes.blocked` rather than hardcoding one.

- Every interaction is keyboard-reachable with visible focus. Drag-and-drop always ships its non-drag equivalent **in the same story** (A11Y-003, APR-002).
- Colour never carries meaning alone (A11Y-004). Status needs an icon or text too.
- Money: tabular figures, right-aligned, currency code visible, negatives in parentheses **and** signed **and** coloured.
- Icons are Phosphor, one weight. No emoji in the interface, ever.
- Contrast is measured in both themes, not assumed from light mode.
- Every chart ships an accessible data table (A11Y-009, RPT-013).
- Forms: programmatic labels, field-level errors, an error summary, and input preserved after a validation failure (A11Y-006).
- API types are **generated** from OpenAPI. Hand-writing a request or response type is a rejection.
- Grids are virtualized and server-filtered. Never load a full dataset into the browser (PERF-005).
- Respect `prefers-reduced-motion`. Transitions ≤ 300 ms.

### 3.5 Tests

Every story leaves behind the smallest runnable check that fails if the logic breaks.

| Story kind | Required |
|---|---|
| `money`, `concurrency` | Unit tests on the calculation **and** a real-Postgres concurrency test |
| `auth`, `security` | Authorization test per role, plus a TEN-010 isolation case |
| `workflow` | State-machine test covering every valid and invalid transition |
| `ui` | Playwright happy path + `@axe-core/playwright` on the new route + a keyboard-only path + the `design-system/MASTER.md` §7 checklist |
| `migration` | Up, down, and a data-preservation assertion |
| `crud`, `chore` | Unit or integration test on the non-trivial branch |

No test frameworks beyond `pytest`, `vitest` and `playwright`. No fixtures nobody reads. A trivial one-liner needs no test.

### 3.6 Simplicity

Before writing code, in order: does this need to exist · is it already in `kernel/` or another module · does the standard library do it · does Postgres or the browser do it natively · does an installed dependency do it · can it be one line. Stop at the first that holds.

Deliberate shortcuts get a `ponytail:` comment naming the ceiling and the upgrade path:

```python
# ponytail: recomputes all balances for the org; switch to per-project incremental if this exceeds 2s
```

---

## 4. Definition of Done

`flo gate` checks all of it. A story is done when:

1. Every acceptance criterion in the design packet is implemented, and every requirement ID it cites is satisfied.
2. Authorization, audit, accessibility and error behaviour are covered — not deferred.
3. `flo check` is green: `ruff`, `mypy --strict`, `import-linter`, `eslint`, `tsc`, `pytest`, `vitest`, `playwright`, `axe`, `gitleaks`.
4. Migrations are reversible and tested.
5. Logs, metrics and business monitors exist where the story adds a failure mode.
6. Field help and tooltips are updated (XFN-008); operator docs updated in the same commit (SUP-005).
7. No open `blocker` finding, and no unaccepted critical/high security finding.
8. Requirement→test traceability is recorded in the task packet.
9. `agents/roadmap.yaml` status is updated and §5 of `docs/claude-plan.md` is regenerated (`flo roadmap`).

---

## 5. Commit and PR format

Commit (one per story after squash):

```
E07-S03: reserve budget on requisition approval

Reserves the approved amount against the parent project inside the same
transaction as the requisition transition. Locks project_balance FOR UPDATE
before validating availability.

Requirements: FIN-006, BUD-005, REQ-002, ACC-001
Author: opencode | Reviewers: codex, qwen | Opus final: approved
```

Milestone pull request body:

```
## M1 — Budget spine
Stories: E05-S01 … E08-S06 (26)
Requirements closed: ORG-001…012, PROJ-001…021, BUD-001…013, IMP-001…012
Exit criteria: <each, with evidence link>
New dependencies: <name — what it replaces — why>
Deferred: <ponytail: markers introduced, with upgrade triggers>
Opus milestone review: <verdict>
```

---

## 6. Environment

```bash
flo up            # local stack: Postgres 17, MinIO (R2 stand-in), Mailpit — no cloud account (OPS-007)
flo check         # everything CI runs, locally
flo status        # roadmap state, budgets, in-flight stories
flo budget        # per-agent spend and remaining capacity
```

### Secrets

Secrets live in the macOS keychain, never in the environment, never in a file, never in a prompt.

```bash
export OPENROUTER_API_KEY="$(security find-generic-password -a "$USER" -s OPENROUTER_API_KEY -w)"
```

Read it into a variable at the point of use and let it go out of scope. Do **not** export it into a
long-lived shell: anything that dumps the environment — a crash log, a process listing, a debug
print, an agent inspecting `env` — leaks it verbatim.

Never run against Neon, R2 or Resend from a worktree. Never print a secret. Never commit `.env`.
Never paste a key into a task packet, a review file, or a commit message.

---

## 7. When you are stuck

Stop and write `notes.blocked` in `task.json`. Do not guess at:

- a financial rule, rounding behaviour, or the meaning of an amount
- a state transition or who may perform it
- an authorization or separation-of-duty rule
- anything `docs/requirements.md` marks as an open decision

Guessing at these produces code that looks right and is wrong, which is the most expensive failure mode available to you.
