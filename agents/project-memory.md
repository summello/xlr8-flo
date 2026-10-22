# agents/project-memory.md — Living state

Loaded into every agent session. Updated by `flo done` (status) and by Opus at each milestone (decisions, invariants, gotchas). Keep it short — if it stops being read because it is long, it stops working.

<!-- STATE:BEGIN -->
## Current state

| | |
|---|---|
| Milestone | **M3 — First sellable release** |
| Stories | 110 / 234 done |
| Branch | milestone/M3-sellable |
| In flight | E11-S06, E22-S02, E22-S06, E22-S07, E22-S12 |
| Blocked | E23-S07, E23-S10 |
<!-- STATE:END -->

## What this product is

Capital projects, budget tracking and capital expenditure management. Replaces spreadsheet-driven capital planning with one traceable system of record: project → requisition → approval → RFQ → bid → award → purchase order → asset, with every amount attributable to a dated, authorized transaction.

Multi-tenant SaaS. Free-tier infrastructure until 10 paying customers or 200 MAU.

## Invariants — never violate, never ask again

1. **Money is `Decimal` and `NUMERIC(18,4)`.** `float` is banned in money modules by lint.
2. **`ledger_entry`, `audit_log`, `stock_movement`, `approval_decision` are append-only.** Database triggers enforce it. Corrections are reversal rows.
3. **Lock before you validate.** `SELECT … FOR UPDATE` on the funding row precedes any availability check. Same transaction as the ledger write.
4. **`org_id` comes from the session.** Never from a URL, body or header. Foreign tenant → 404, never 403.
5. **Every state-changing POST is idempotent** via `Idempotency-Key`.
6. **Every external effect goes through the outbox.** Never send from a request handler.
7. **Routers hold no business logic.** Modules talk through `service` and `schemas` only.
8. **Drag-and-drop ships its keyboard equivalent in the same story.** Not as a follow-up.
8a. **UI uses tokens from `design-system/MASTER.md`.** No hardcoded colour, spacing, radius or duration. Colour never carries meaning alone. Money uses tabular figures and parenthesised negatives.
9. **Balances are maintained in-transaction; the nightly job reports drift, it never corrects it.**
10. **No PO without a completed RFQ** (SRC-018). Not negotiable without a decision-log entry.
11. **No direct pushes to `main`.** Stories squash into a milestone branch; `main` advances only by milestone PR, proposed when the work is sufficient — never on a schedule. Enforced by `.githooks/pre-push`.
12. **M0 is authored solely by Codex** (`gpt-5.6-sol`, high), reviewed by Opus + one OpenCode agent, with an Opus final review on every story. Foundations are a rewrite if wrong, not a patch. See `milestone_overrides` in `agents/agents.yaml`.

## Decisions already made — do not reopen without a `docs/claude-plan.md` §1 entry

- Cloud Run + Neon + R2 + Cloudflare Pages; $0 until the graduation trigger (D-01…D-04, D-07)
- In-app auth behind an `IdentityProvider` port; **no Keycloak** in Phase 1 (D-05)
- Pooled multi-tenant, one database, `org_id` + RLS + scoped repositories (D-06)
- Postgres job queue, no Redis (D-08); transactional outbox (D-09)
- Balances in-transaction, **not** materialized views (D-11)
- Custom fields: relational definitions + JSONB values, not EAV (D-12)
- No Terraform yet — deploy scripts in CI (D-13)
- Modular monolith with `import-linter`-enforced boundaries (D-15)
- First sellable release is **M3**, the full money loop including RFQ (D-16)
- **One public origin**, `xlr8flo.summello.com`; Cloudflare path-splits `/api/*` to Cloud Run, the rest to Pages. No CORS anywhere — if a story needs it, D-17 has been violated (D-17)
- **M2 (10 Oct 2026, full text in `docs/m2-decisions.md`):** a project-linked requisition **reserves at submit** (D-M2-15, D-21), with at most one live reservation per requisition, tracked in funding rounds (`docs/m2-conventions.md` L). Approval keeps it and generates the sub-project; a standalone request reserves at approval (D-M2-5). Return-for-changes keeps the reservation; reject (to draft or permanent), withdraw, cancel and expiry release it once. Approval-group membership is **live** (D-M2-2, D-M2-9). The material-change list is fixed: amount, project, ledger account, catalogue item, line count (D-M2-13, canvas 79). Only the requester withdraws, until sourcing starts (canvas 78). Workflow conditions use the estimate only (canvas 83). Approval depth is 1..50, default 10.

## Rejected, with reasons — do not re-propose

| Rejected | Why |
|---|---|
| Vercel / Next.js | Hobby plan forbids commercial use; no SSR value in an authenticated internal app |
| Keycloak day 1 | ~1 GB always-on JVM; incompatible with every scale-to-zero free tier |
| Clerk / Supabase Auth | Lock-in on the identity boundary; paid past free tier |
| Cloudflare D1 / SQLite | No interactive transactions, single writer — disqualifying for the ledger |
| Upstash Redis | A second dependency for a queue Postgres already serves |
| Materialized views for balances | `REFRESH` is not transactional; would break FIN-005 |
| EAV custom fields | Every report becomes a self-join pile |
| Microservices | Budget, requisition and PO operations are one transaction |
| ClamAV in-process | 300 MB signature DB will not fit the free tier (see D-14 compensating controls) |

## Gotchas discovered

| Area | Gotcha |
|---|---|
| Neon | Free tier is **512 MB** and it is the binding constraint. Attachments never touch Postgres; audit and ledger partitions archive to R2 after 90 days. |
| Neon + PgBouncer | Use the **pooled** endpoint. RLS works because `SET LOCAL` is transaction-scoped — do not use `SET`. |
| Cloud Run | `min-instances=0` costs ~1–2 s cold start. The 60 s cron keeps it warm in business hours. Excluded from PERF-001, measured under OPS-006. |
| Resend | **100/day** is a real ceiling. Notifications batch into digests; transactional PO sends always go immediately. |
| Artifact Registry | 0.5 GB free. Prune to the last 3 images or it fills quietly. |
| Secrets | Keychain only: `security find-generic-password -a "$USER" -s OPENROUTER_API_KEY -w`. Never in the environment — an env dump leaks it verbatim. |
| Free agent endpoints | Both OpenCode Zen previews died — `ox-alpha` withdrawn, then `nemotron-ultra` failing every request. **Route every agent through OpenRouter instead.** Test a suspect endpoint with a one-word prompt (`Reply with exactly: PONG`) before blaming the model or the prompt. |
| Review verdicts | Reviewers hand-write JSON against a schema that lives in prose, and nothing validates it at write time. `"approved"` for `"approve"` once read as a *rejection* and would have blocked a story on a typo. `flo gate` now normalises the obvious spellings via `verdict_of()` and fails unreadable ones loudly as malformed. |
| opencode sandbox | A reviewer run **dies outright** on a rejected permission — it does not degrade. Reading a dotfile (`apps/web/.env.production`) and writing a backup to `/tmp` each killed a qwen review mid-experiment. Reviewer prompts must say: scratch files inside the worktree only, `git checkout -- <file>` to restore after planting a violation, never open dotfiles. Inline any dotfile the reviewer needs into the packet. |
| Two Opus sessions, one worktree | On 10 Oct two sessions started the M2 design pass in the same worktree; one committed 41 s before the other. Before writing in a shared worktree, check `git log` for commits you did not make and `ListAgents` for a busy peer. Split by epic, each on its own branch and worktree, and name one owner per shared file (`docs/m2-*.md`, the roadmap, this file). |
| `/Volumes/Omnissiah` unmounted | `uv`, `gh` and `opencode` fail through the `XDG_CACHE_HOME` and `UV_CACHE_DIR` environment variables, which point at that volume. Override both to `~/.cache` for the command. |
| Design specs on unmerged branches | Page specs and DECISIONS live on `canvas-*` branches until merged. A UI packet that cites a spec missing from the milestone sends its author to a file that is not there. Merge the design branch (design-system/ only) before the design pass. |
| Parallel reviewers | Two reviewers in one worktree corrupt each other — both are told to plant violations and revert them. Run them sequentially, or give the second its own `git worktree add --detach` at the same commit (remember `npm ci` there, and copy the verdict back into the story worktree, since `flo gate` only reads that one). |

## Fleet routing changes — evidence, not impressions

**24 Aug 2026 — reviewers widened on M0 gated stories.** `opencode-nemotron` returned
`approve` with **0.0 findings per review** on its first three reviews (E01-S01, S02, S03).
Opus found six real defects in those same stories: a committed `task.json` in two of them,
five ports bound to `0.0.0.0` with credentials published in this repo, MinIO root credentials
inline in a healthcheck, an unauthenticated `/readyz` opening a Postgres connection per
request, and a tag-pinned base image. That is AGENTS.md §2.3b rubber-stamping.

Also discovered: **`opencode-ox` is dead** — the `ox-alpha` endpoint was withdrawn, so the
"two free models so a free author still gets a free reviewer" design was silently a pool of
one. This is exactly the gotcha this file already warned about.

Change: M0 override reviewers are now `[opus, qwen, deepseek, opencode-nemotron]`.
`qwen` = `openrouter/qwen/qwen3.8-27b`, `deepseek` = `openrouter/deepseek/deepseek-v4-pro-0813`,
both metered on OpenRouter credits. Authoring is unchanged — codex remains M0 sole author.
Revisit at the M0 retro with a real blocker-precision number for each.

**25 Aug 2026 — qwen replaced by kimi on reliability, not quality.** On E01-S05 `qwen`
failed **four consecutive review runs** without ever writing a verdict. Two died on sandbox
permission rejections mid-experiment (reading `apps/web/.env.production`, then writing a
backup to `/tmp`), a third repeated the second, and the fourth ran fourteen minutes emitting
zero output. Its partial work was *good* — it withdrew its own `failure()`-escape hypothesis
after researching the semantics, and correctly dismissed an `E302` as preview-only in ruff —
so this is not a review-quality judgement. A reviewer that cannot finish cannot count toward
the two-reviewer floor, and four runs of OpenRouter spend bought nothing.

`kimi` = `openrouter/moonshotai/kimi-k2.6`, same budget and roles qwen held. `agents.yaml`
milestone override reviewers are now `[opus, kimi, deepseek]`; every fallback chain naming
qwen now names kimi. qwen's scorecard history is left untouched — those records are immutable
and its earlier findings were real. Revisit if kimi shows the same completion problem.

**25 Aug 2026 — everything runs through OpenRouter now.** OpenCode Zen's
`nemotron-ultra` began failing every request with `UnknownError: Unexpected server error`;
a bare one-word prompt failed identically, so the endpoint was gone, not the model. That is
the **second** Zen endpoint withdrawn after `ox-alpha`, and it briefly left the free
reviewer pool empty — breaking AGENTS.md §1's promise that every story gets a free
reviewer. Both now point at OpenRouter, where the operator holds real credits:
`nvidia/nemotron-3-ultra-550b-a55b:free` and `stealth/ox-alpha`. Smoke-tested: nemotron
answers. Prefer OpenRouter for every agent — a `:free` variant there can degrade to the
paid model instead of disappearing.

**25 Aug 2026 — qwen reinstated, kimi demoted to fallback. Question closed.** Both ran on
E04-S01, the first `ui` story, as the comparison the previous entry asked for. qwen planted
**twelve** violations, cross-checked the contrast test's OKLCH→sRGB engine against its own
implementation, and raised the story's only real blocker: the three `--money-*` tokens passed
by *absence* — deleting all three left vitest 18/18 green. Opus reproduced it, upheld it as a
blocker, and found a second of the same class. kimi returned `approve` with **zero** findings,
its second clean-room story at 0.0 findings per review. Scorecard after the merge: qwen 2.4
findings/review (85), deepseek 0.8 (76), kimi 0.0 (n=2). Both of qwen's runs completed, which
also confirms the four E01-S05 failures were the review packet's fault, not the model's.
`agents.yaml` M0 reviewers are now `[opus, qwen, deepseek]`; kimi is qwen's fallback.

**10 Oct 2026 — parallel lanes, review scaled to risk, draft PR from story one.** Operator
approved all three. Evidence: 52/168 done at ~6–7 stories a day, strictly serial; codex merges
clean (98%, 1.05 rounds), so the time went to serialisation and halts, not to bad code.
nemotron as author: 9/9 clean merges, so it takes the ungated crud/ui/docs lane (`avoid_tags`
keeps it off anything gated); codex keeps engines and every gated story. Ungated stories are
reviewed by qwen (2.4 findings/review, best in the fleet); Opus reviews gated stories and each
train's diff. Each worktree now has its own test database, so lanes run side by side.
Why the draft PR matters: M1 CI was red for eight pushes (E05-S14's invitation CLS 0.132 at 375px,
Linux only) with nobody looking. Revisit at the M2 retro with escapes on qwen-reviewed stories.

**10 Oct 2026 — nemotron capped at size M; ox removed as an author.** nemotron's first story on
the new lane, E06-S07 (ui, L), exited 0 with nothing committed, `flo check` never run, no tests,
and an edit to `modules/identity/service.py` outside scope; codex then delivered it. Its 9/9 clean
merges were all M or smaller. `max_author_size: M` keeps it on the small crud/ui lane. ox's
endpoint has been withdrawn before and the cap would have routed E06-S08 to it, so ox is
review-only. Same day: qwen's E06-S07 review caught a dead `/projects/<id>/ledger` link (major,
upheld), and its pre-flight audit caught E08-S03's resolver wiring before codex started.

**11 Oct 2026 — M1 retro re-route, from SCORECARD.md (58 stories).** Authors: codex 48 stories, 96% clean, 1.06 rounds, 0 escapes; nemotron 10, 100%, 1.00, 0 (all size M or smaller, which is why the cap stays). Reviewers: qwen 15 findings in 8 reviews (1.9), deepseek 6 in 6 (1.0), kimi 0 in 2, nemotron 0 in 5; no escapes anywhere, and no reviewer has raised a blocker yet, so precision is still undefined. Changes: (1) `ungated_reviewers` is now `[qwen, deepseek]`: kimi has the 0.0 pattern of a rubber stamp and deepseek has a real record; (2) nemotron's roles are `[author]` only (its notes already said it reviews nothing; the list now agrees); (3) deepseek gets a $20 monthly cap since it is now a primary reviewer, not only a release reviewer. Not changed: codex stays off crud/docs by `avoid_tags` routing, not by kind (7 of its 48 are chore at 1.29 rounds, all M0). Revisit at the M2 retro with M2's blocker counts: if qwen still raises none on the 8 ungated M2 stories, its 1.9 findings per review are minors and the train diff is doing the catching.

**13 Oct 2026 — Sonnet authors, Codex reviews, one light gate. Operator-approved.** Evidence: a
finish took 35-95 min and bounded M2. The full `flo check` ran **three** times per story on one tree
(author, submit, done); pytest is **25 min on an idle GitHub runner** (function-scoped fixtures run
migrations per test) and 83 min locally at load average 74, where Playwright then timed out. The
repo is public, so hosted CI minutes are unmetered. Changes: `flo check --light` (no full
pytest/e2e; the story's own tests) cached per tree; `flo submit` pushes `story/<id>` and CI runs
there during review; gated stories need that run green before merge, ungated ones merge and
`ci-watch.sh` routes a red run back to the author's session (`fix.sh`). Authors: `author_priority`
[sonnet, nemotron, codex]; Sonnet on two Claude Pro accounts, 1 worker each for the six-story
pilot, then 2 (drop an account back to 1 if it caps more than once a day). Reviewers: Codex every
story at `gpt-5.6-sol`/medium pinned on the command line (config.toml had silently run `low`),
fresh Opus on gated stories too; `.scratch/codex-usage.jsonl` records the cap per review. Codex's
weekly window read **94% used** on 13 Oct 00:04 IST, reset Sun 18 Oct 10:54 IST: until then qwen
takes the reviews Codex cannot. One planner; the other account's Opus is reviewer and standby.
Epic checkpoints replace train reviews; one PR per milestone stays. Revisit after the pilot with:
finish wall time, red-after-merge rate, Codex findings and blocker precision against Opus on the
first ten gated stories (Codex ≥ Opus → Opus moves to epic level only).

**21 Oct 2026 — `max_stories_in_flight` 4 → 6.** Four lanes sat in review or CI for hours while
both Claude accounts had 75–80% of their five-hour window left and the ready queue held five stories.
Review and CI are the bottleneck, not authoring, so more lanes overlap them. New lanes take stories
with no migration first (migration lanes renumber at merge, three times on 21 Oct). Revisit if a
milestone merge conflict or a gate collision traces to lane count.

**22 Oct 2026 — Codex story reviews: 55 calls for every story, tests within the first 10.**
Three ungated Codex reviews (E11-S10, E22-S04 twice, E22-S10) hit the 35-call cap after a milestone
merge while still reading, and left `approve, ran_tests: false`, which the gate rejects; each cost a
second review and a manual finish. Ungated budget 25 -> 40 commands, hard cap 35 -> 55 (gated was
already 55), and the packet says to run `flo check --light` and set `ran_tests` within 10 commands.
Revisit if Codex's 5-hour window starts capping.

**22 Oct 2026 — `author_priority` is now `[opencode-nemotron, sonnet, codex]` (E23-S04).** Nemotron
is free and has no worker cap; Sonnet draws a shared Claude window. Evidence and its limits, from
`agents/scorecard.json` (106 records): Sonnet 29 stories, 1.72 mean rounds, 59% clean; gated 2.17 and
33% (n=12), ungated 1.41 and 76% (n=17). That gap cannot yet be put on Sonnet: since 13 Oct 29 of 30
records are Sonnet, reviewed by Codex plus Opus, while Codex's 1.04 comes from M0/M1 under Opus-alone
review, and recorded findings do not explain the rounds (`docs/ops/sonnet-round-churn.md`). So the
reorder does **not** narrow Sonnet and does not reach gated work (nemotron's `avoid_tags`). Next 6
gated stories go to Codex as an A/B against Sonnet's 2.17; revisit Sonnet's `avoid_tags` then.
**Correction to the 10 and 11 Oct entries:** they say nemotron's merges were "all M or smaller". Four of
its 10 records are size L (`E05-S03`, `E06-S02`, `E06-S03`, `E05-S10`), two are engine, two carry `auth`,
all 10 are early-October M1 stories written before the 10 Oct `allowed_kinds`/`avoid_tags`/size cap, and
all were reviewed by Opus alone. Only 5 of 10 (`E05-S01`, `E05-S02`, `E06-S01`, `E06-S05`, `E06-S06`)
match today's lane. `E06-S07` (the one attempt under the cap) went to Codex and sits in Codex's
record, so 10 clean of 11 attempts is the honest figure. **Revisit trigger:** if any of nemotron's first
three stories after this change needs a second round or is re-authored by another agent, restore the
old order.

**22 Oct 2026 — Sonnet's effort now comes from `agents.yaml`, and it says `medium` (E23-S12).** The
config said `reasoning: high` while `author.sh` passed `--effort medium`. The launcher was right:
commit `5ba2460` (13 Oct) records the operator decision "claude-sonnet-5-5 at medium, not high", and
the config line, written earlier that day, was never updated. Nothing that runs changed. `author.sh`
now reads model and effort through `flo launch-model` and `task.json` records `sessions.sonnet.effort`,
so a scorecard row says which effort produced it. Trying `high` is a separate, measured experiment,
after the Codex A/B on gated authorship (`docs/ops/sonnet-round-churn.md`).

**Merge authority:** the operator has delegated GitHub merges for M0 to Opus, with the
instruction to space them out so the repository does not read as bot-driven. Outside M0 the
standing rule holds: push, never merge.

## M1 shipped (11 Oct 2026)

M1 (32/32) merged to `main` as `e51bea9` (PR #15, merge commit); migrations 0027 to 0036 ran in the deploy job. The first deploy migrated and shipped the API, then stopped at the SPA upload: the bundle guard's bare `sk-` grep matched `/risk-indicators`. PR #17 tightened it to a key shape (`sk-` at a word start plus 20 key characters, or a PEM header) with a planted test, and the re-run deployed the SPA and Worker (smoke: `/readyz` 200, bundle served). Lesson: a deploy guard that greps source text needs a test that plants both a true and a false positive, and a story that adds an API path should be checked against it. Not done before merge, by the operator's call: the Neon staging rehearsal of 0027 to 0036; the 19 decisions in `docs/m1-decisions-to-confirm.md` are still unconfirmed (reversible by follow-up story). Still open on M1: two minors carried (ratio rounding outside `kernel/money.py`; five `_audit` wrappers, which M2 approvals would make six); the fleet re-route from `agents/SCORECARD.md` owed at milestone end (claude.md section 5).

## Latest handoff

**M2 design pass (10 Oct 2026): all 25 packets are written** (`design/E09-S01..S12.md`, `design/E10-S01..S13.md`, on `docs/m2-design` until merged into `milestone/M2-governed-demand`). Two Opus sessions split the pass by epic. Before authoring or reviewing any M2 story:

1. **Read order:** this file → `docs/m1-conventions.md` → **`docs/m2-conventions.md`** (G modules, H document-adapter registry, I permissions, J statuses, K live membership, L funding rounds, M settings, O `If-Match`) → **`docs/m2-decisions.md`** (D-M2-1..15) → `docs/author-traps.md` → your packet. For UI stories, also MASTER.md and the page spec the packet names (`design-system/pages/requisition-create.md`, `requisitions.md`, `approval-inbox.md`, `approval-workflow-builder.md`, now on the milestone).
2. **Two lanes:** E09 (requisitions) and E10 (approvals) run in parallel until E09-S05, which needs E10-S05. `flo next` and the roadmap deps give the order.
3. **Frozen cross-lane contracts; do not change one in a story:** the `DocumentAdapter` protocol and `OutcomeContext` (E10-S05 Contract); `approvals.service.start(..., resume_from)`, `withdraw`, `preview_route`, `instances_for`, `approval_state` and `assert_current_approval` ("latest instance" = greatest `(started_at, id)`, E10-S07); the material digest (E10-S07 item 2). `approvals` never imports `requisitions`.
4. **Shared file, first merge creates it:** `api/document_types.py::register_document_types()` is created by whichever of E09-S05 and E10-S13 merges first; the other adds to it.
5. **Packet authority:** a packet beats a page spec on API paths and money rules. The canvas copy that shows a reservation at submission is **current** (D-M2-15); the copy that says group membership is "snapshotted" or the depth is 25 is **stale** (record it in `notes.followup`, never copy it).
6. **Pre-flight:** qwen runs over every packet before authoring, and Opus settles the findings on `docs/m2-design` before the first story starts. On M1 it caught a real import-contract break (E08-S03), and every codex block was a gap between the packet and the code.
7. **Fleet process since 10 Oct:** `agents/scripts/{begin,author,review,finish}.sh`. `finish.sh` merges the milestone into the story before gating (two lanes once broke M1 together). Each worktree has its own test database (`flo db reset`). Review is risk-scaled: gated tags get Opus, everything else gets qwen. nemotron is capped at size M and ox is review-only.
8. **Codex's ChatGPT usage cap is the binding throughput limit** (it hit about 15:00 on 10 Oct, after about 7 runs). Resume with `author.sh STORY .scratch/STORY.cont`, and never switch authors on a money story.

M1 handoff: `docs/handoff-2026-10-09.md` (9 Oct 2026): where M1 stands, the E05-S05 dispatch steps, the packet pre-flight technique, and what is open for the operator. Read it before dispatching any story.

## Open questions for the human

**Closed 21 Oct 2026** (operator, in batches; canvas DECISIONS 214 to 232): every question parked in
E11-S08, E12-S19 and E13-S22 and the M2 follow-ups. Decision 99 became E30-S01 (organisation pool);
standalone-to-project assignment stays with the final approver (216); baseline read grants are
E22-S06 (217); requisition tones are in MASTER §2.3 and E22-S08 (231); people by name are E22-S07 and
E22-S09 (232); a PO commits against the requisition's sub-project (209).
The delegation screen is E22-S10 (233).

_(none open)_

## Session handoff — 25 Aug 2026

M0 is **11/161 overall, 11 of M0's 25**. `main` stays behind `milestone/M0-rails` by design —
it advances only by milestone PR when M0's exit criteria are met, never on commit count.
`flo ack` raises a `pr:M0` item by itself once every M0 story is done. Do not merge early.

**`E04-S01` merged (`e75892c`).** Design tokens, Tailwind v4, light/dark/system, MASTER-derived
contrast + coverage test. Two rounds. Both round-1 blockers were the same defect class the
packet exists to prevent — passing by absence — and neither was found by the two reviewers that
approved: deleting all three `--money-*` tokens left the suite green, and a malformed MASTER.md
table row dropped silently out of coverage with a bare `continue`. Both now fail by name,
plant-verified by Opus and again by qwen on the re-review.

**MASTER.md §2.4 was unimplementable and is now fixed.** It promised a light-text/light-tint and
dark-text/dark-tint quad per tag swatch and shipped one hex each — 42 of 56 values did not exist,
so codex correctly refused to author rather than invent them. `design-system/derive-tag-swatches.py`
now derives the missing values from each published hue and chroma and searches lightness for the
least-contrasty value clearing §2.4's floors; every pair lands ≥5.9:1 light, ≥7.5:1 dark. **If a
future MASTER.md section makes a claim its table does not carry, expect the author to block — that
is the system working.**

**Three MASTER.md debts, all Opus's, all before E04-S02 renders a control:**
1. **§2.7 border rule.** `--border-strong` is 1.48:1 light and 1.95:1 dark against `--canvas`, and
   no gate measures a border. Write the rule: a control is identified by its label, its ground and
   its focus ring, never by the hairline alone; any control whose boundary is its only identifying
   mark uses a 3:1 token, and §2.7 gains that pair so the derived-pair mechanism enforces it.
2. **§3.1 dark `--shadow-drag`.** The dark block omits it, so dark inherits the light definition
   through the light `--shadow-color` and paints a pale shadow on a near-black canvas. Fix
   MASTER.md; the next shadow-touching UI story transcribes it. Never patch `tokens.css` first —
   that breaks the transcription contract E04-S01 establishes.
3. **`--fg-muted`'s comment claims 4.8:1; it measures 4.59:1.** Still above 4.5, so not urgent.
   qwen found it by cross-checking the test's colour engine against its own.

**Two test debts on `contrast.test.ts`,** both recorded in E04-S01's `notes.followup`: the
*addition* direction is unguarded (an undocumented non-colour token redefined identically in both
dark blocks passes), and the chrome-pair regex captures MASTER.md's documented ratio then hardcodes
4.5.

**Deferred to E04-S02:** the stored theme is applied from the module body, so an explicit override
flashes the OS theme first. The fix is a blocking inline script in `index.html`'s `<head>`; it
lands with the toggle.

**Story worktrees ship without dependencies.** `flo start` gives you the tree, not the installs.
Before dispatching any author: `npm ci` in `apps/web`, `uv sync --frozen` in `apps/api`. Codex runs
under `-s workspace-write`, which has no network, so an install it has to do itself will fail.

**Still owed on the merged `E01-S05`, and invisible on the board.** Four `notes.followup`
entries live in a done story where nobody will look, three of them blocked on operator accounts
that now exist:
1. live deploy evidence run — failing migration aborts, vulnerable image never pushed, both SARIF
   categories survive, rollback works
2. attach `xlr8flo.summello.com` to Pages, deploy the `/api/*` Worker route, Full (strict), HSTS
3. measure login p50 on the live 1 GiB / 2 vCPU revision against E02-S01's 250–500 ms target
4. **Cloud Run ingress is open.** `run.app` bypasses the Worker *and* E19-S03's future edge rate
   limiting. Two tracked closes: the free Secret Manager shared secret (needs E01-S06) or a paid
   load balancer at graduation. `infra/SETUP.md` has the detail.

The `prereq:` board rule added today only surfaces prerequisites for **non-done** stories, so
these vanished the moment E01-S05 merged. That is a design error: prerequisites should follow
followups, not story status. Fix it before relying on the board.

**Reviewer harness was rebuilt today — read this before dispatching any reviewer.** Three
reviewers failed on E01-S05 for reasons that were the *packet's* fault:
- `flo review` now excludes generated lockfiles (`uv.lock` was 1,108 of 3,178 lines and stalled
  two models before their first token), inlines dotfiles from the diff (a sandbox refuses to open
  them and the refusal **kills the run**), and states its rules as numbered commands with the
  `git checkout -- <file>` restore spelled out.
- Two new prohibitions, both earned: reviewers must not open `reviews/` (one read another's
  verdict two steps before writing its own and restated both findings), and must not report a
  defect not reproduced in the file as it stands (the same reviewer reported a double pipe in a
  regex that has one).
- Enforce the first one physically: move `reviews/*.json` aside for the duration of a run.
- Run reviewers **sequentially**, or give the second its own `git worktree add --detach` at the
  same commit — both plant and revert violations and will corrupt each other otherwise.
- `opencode run --auto` is required, or the sandbox kills the run on the first permission prompt.

**Open question for the next gated story: qwen vs kimi.** Run both, compare findings that survive
the Opus final. Comparable → keep kimi (qwen costs more). qwen clearly better → revert. The
confound: qwen's four failures were all packet-caused and are now fixed, so it was probably never
the problem. kimi's only clean-room outing produced **zero** independent findings.

**Driving `flo`:** run it from the parent repo, but with the story worktree's venv first on
`PATH` *and* as the interpreter, or `flo done` dies on `ruff` and then on missing PyYAML:

```
PATH="<worktree>/.venv/bin:$PATH" <worktree>/.venv/bin/python agents/scripts/flo done <STORY>
```

`pip install pyyaml` into each new story venv once.

---

## Staging database (8 Oct 2026)

Rehearse migrations and the first-tenant bootstrap on a Neon branch named `staging` (copy-on-write
from production) before touching production; secrets `FLO_STAGING_DATABASE_URL` (keychain) and
`flo-database-url-staging` (Secret Manager), never the production names. Procedure in
`infra/SETUP.md` ("Rehearse on a staging branch first"). A full second environment waits for the
graduation trigger. Agents never run against Neon from a worktree, staging branch included.

## Branch protection

Live on `main`: PR required, merge-commit only, `detect`/`governance`/`security` required.
`backend`, `frontend` and the two CodeQL `analyze` checks report now and must be added to the
required list — recorded as a done criterion on E01-S04.
