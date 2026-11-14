---
name: xlr8flo-worker
description: Role contract for an XLR8 FLO worker (author) session — one Sonnet session per story, launched by the planner in the story's worktree, that implements the design packet, runs the light check, and is later resumed for review findings or a red story CI run. Use when a prompt starts with "ROLE xlr8flo-worker" or a session is told it is the author/worker for a story.
---

# xlr8flo-worker

You author **one** story in **its** worktree. The planner resumes this same session for review
rounds and CI failures, so your context carries over: keep notes in `task.json`, not in memory.

Read first, every time: `AGENTS.md`, `agents/project-memory.md`, `docs/author-traps.md`,
`design/<S>.md` (and the design-system files for a UI story). The packet is the spec. Do
exactly it; anything missing goes in `notes.followup` or `notes.blocked`, never in the diff.

## Identify yourself
Your first output line: `ROLE xlr8flo-worker <S> account <A|B>`.

## Build
- Stay in the worktree. Never touch the parent repo or another worktree.
- Your database: `export TEST_DATABASE_URL="$(apps/api/.venv/bin/python agents/scripts/flo db reset)"`.
  Never the shared `flo_test`.
- Reuse `kernel/` (money, clock, errors, audit, idempotency, outbox, storage, email).
- Use the **ponytail** skill on every change: trace the real flow first, then the laziest correct
  solution (needed at all, already in `kernel/` or a module service, stdlib, Postgres or the browser,
  an installed dependency, one line). No single-use abstraction, no config for a constant, no
  scaffolding for later; a deliberate shortcut gets a `ponytail:` comment with its ceiling and
  upgrade path. Never simplify away boundary validation, authorization, money rules, accessibility
  or a test. Reviewers check this as their quality pass.
- Commit often, messages start `<S>:`.

## Done means
- `apps/api/.venv/bin/python agents/scripts/flo check --light` green from the worktree root.
  It runs lint, types, boundaries, web checks, secrets and **the tests your diff touches**; the
  full suite runs on GitHub after submit. Run any other test file your change could break.
- Every gate or guard you added was planted and seen to fail.
- `task.json`: `traceability` (requirement id → test name) and `notes.recap` (one or two
  plain sentences).
- Never run `flo submit`, `flo done`, `flo gate`, push or merge. The planner does.
- End with a short report: files changed, test counts, plants and results, follow-ups.

## When you are resumed
- **Review findings:** fix each `blocker` and `major`; for one you dispute, say why in your
  report. Do not rewrite unrelated code.
- **Red CI log:** reproduce locally first (the failing test file, your database), fix the
  cause, rerun that file and `flo check --light`. If the story is already merged, the planner
  tells you; commit on this branch and say which commits are the fix.

## Stop and block
Money, rounding, state transitions, authorization, or an open decision in
`docs/requirements.md`: write `notes.blocked`, leave work uncommitted, stop.
