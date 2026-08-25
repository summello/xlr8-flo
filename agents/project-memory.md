# agents/project-memory.md — Living state

Loaded into every agent session. Updated by `flo done` (status) and by Opus at each milestone (decisions, invariants, gotchas). Keep it short — if it stops being read because it is long, it stops working.

<!-- STATE:BEGIN -->
## Current state

| | |
|---|---|
| Milestone | **M0 — Rails** |
| Stories | 11 / 161 done |
| Branch | milestone/M0-rails |
| In flight | — |
| Blocked | — |
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

**Open: qwen vs kimi, decide on the next story.** Run **both** as reviewers on the next
gated story and compare findings that survive the Opus final. If comparable, keep **kimi**
— qwen is materially more expensive. If qwen clearly outperforms, revert to qwen. Context:
qwen was replaced after four failed runs on E01-S05, but every one of those failures was
later traced to the *review packet* — dotfile reads, `/tmp` writes, and a 1,108-line
lockfile — all since fixed in `flo review`. qwen was probably never the problem. kimi's
first outing was weak on its own merits: given a clean room it produced **zero**
independent findings, and its earlier three were two copied from deepseek's verdict file
plus one fabricated regex defect. Neither agent has a fair sample yet.

**Merge authority:** the operator has delegated GitHub merges for M0 to Opus, with the
instruction to space them out so the repository does not read as bot-driven. Outside M0 the
standing rule holds: push, never merge.

## Open questions for the human

_(none — add here rather than guessing)_

## Session handoff — 25 Aug 2026

M0 is **10/25**. `main` is 79 commits behind `milestone/M0-rails` and that is correct —
`main` advances only by milestone PR when M0's exit criteria are met, never on commit count.
`flo ack` raises a `pr:M0` item by itself once every M0 story is done. Do not merge early.

**Next up: `E04-S01` — design tokens, Tailwind v4, light/dark.** Worktree
`../xlr8flo-E04-S01` was rebuilt today on the milestone head (`4378023`); the old one was six
commits stale with a `task.json` naming qwen. Author codex, reviewers opus + kimi + deepseek,
Opus final required. **Nothing has been authored yet — codex has not been dispatched.** This is
the first `ui` story, so `design-system/MASTER.md` is binding and its §8 is the UI definition of
done (§7 is Components — AGENTS.md and agents/claude.md were corrected today).

Its packet was revised today: an unverifiable "46 documented pairs" acceptance criterion is gone
because the real count across MASTER.md §2 is well over sixty. The contrast test must now derive
its pair list *from MASTER.md* and fail when a documented token is **absent** from `tokens.css` —
otherwise it passes by testing nothing, which is the E01-S07 defect class in a new costume.

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

## Branch protection

Live on `main`: PR required, merge-commit only, `detect`/`governance`/`security` required.
`backend`, `frontend` and the two CodeQL `analyze` checks report now and must be added to the
required list — recorded as a done criterion on E01-S04.
