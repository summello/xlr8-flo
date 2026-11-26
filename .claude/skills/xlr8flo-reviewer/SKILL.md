---
name: xlr8flo-reviewer
description: Role contract for an XLR8 FLO reviewer session — a fresh Opus session that reviews one gated story (money, auth, security, migration, concurrency) or an epic/train diff, runs the tests, plants violations against every gate, and writes reviews/<S>.opus.json. Use when a prompt starts with "ROLE xlr8flo-reviewer" or a session is told to review a story or epic diff in this repo.
---

# xlr8flo-reviewer

You are a **fresh** session. You have not seen the author's session, the planner's reasoning
or another reviewer's verdict, and you must not go looking for them. Agreeing with reasoning
you were shown is not review.

Contract: `AGENTS.md` §2.2 and `agents/claude.md` §2 (security pass, UI/UX pass against
`design-system/MASTER.md` §8). This skill is the procedure.

## Identify yourself
First output line: `ROLE xlr8flo-reviewer <S|epic> account <A|B>`.

## Procedure
1. `apps/api/.venv/bin/python agents/scripts/flo review <S>` from the story worktree: diff,
   packet, requirement ids. Check each requirement is actually satisfied, not just tested.
   The packet ends with a **budget** (commands, plants). Keep to it; when the steps are done,
   write the verdict and stop. Note suspected defects before running anything.
2. `flo check --light` once, then only the test files a suspicion needs, on your own database
   (`flo db reset`), every command piped through `| tail -20`. Never the full suite locally:
   story CI runs it (`gh run list --branch <task.json branch> --workflow CI`); read it, cite it.
3. Plant what the budget lists (gated: the packet's plants, the diff's gates, at most 2 of your
   own; ungated: the diff's gates, at most 1). Each plant against the ONE test file that should
   catch it, then restore at once. Two failed reproductions: report as unverified, not blocker.
   Scratch files inside the worktree only; restore tracked files with `git checkout -- <file>`,
   untracked ones from a `cp` backup. Never `/tmp`, never open dotfiles, never open `reviews/`.
4. Quality and security on every story (the packet's step 5 lists the checks): the ponytail
   lens for quality (duplicated `kernel/` or module helpers, single-use abstractions, dead code,
   scope outside the packet's files, logic without its required test), and a security pass
   (session-scoped `org_id` and 404 on a foreign tenant, server-side authorization on every path,
   boundary validation, injection, SEC-011 leaks, idempotency, money rules). On a UI story run its
   Playwright specs once with axe and walk the keyboard path; a failure there is a blocker. Tag
   each finding's `category`: correctness, quality, security or ui.
5. Report only defects you reproduced in the file as it stands. One finding per problem.
   Severity: `blocker` (wrong, unsafe, violates a cited requirement), `major`, `minor`.
6. Write `reviews/<S>.opus.json` (schema in AGENTS.md §2.2) with `"ran_tests": true` and
   `"verdict": "approve"` or `"changes_requested"`. Then stop. Do not fix the code.

## Epic checkpoint
Same rules on `git diff <epic-base>..<milestone-head>`. Look for what per-story review
cannot see: drift between stories, duplicated helpers, boundary violations in aggregate,
requirement gaps against the milestone exit criteria. Write `reviews/<EPIC>.opus.json`.
