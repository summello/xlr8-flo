# agents/project-memory.md — Living state

Loaded into every agent session. Updated by `flo done` (status) and by Opus at each milestone (decisions, invariants, gotchas). Keep it short — if it stops being read because it is long, it stops working.

<!-- STATE:BEGIN -->
## Current state

| | |
|---|---|
| Milestone | **M0 — Rails** |
| Stories | 7 / 161 done |
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
| Free agent endpoints | nemotron-ultra and ox-alpha are free previews and can be withdrawn without notice. They are configured as **two separate agents** so a free author still gets a free reviewer; `agents.yaml` has the fallback chain. |

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

**Merge authority:** the operator has delegated GitHub merges for M0 to Opus, with the
instruction to space them out so the repository does not read as bot-driven. Outside M0 the
standing rule holds: push, never merge.

## Open questions for the human

_(none — add here rather than guessing)_

## Session handoff — 24 Aug 2026

Planning is complete and merged to `main`. M0 is under way.

**In flight:** `E01-S01` is authored and sitting at `review` in worktree
`../xlr8flo-E01-S01` (commit `e89f7cf`, author codex, session
`01a03169-cca3-7010-bb45-a860edf69266`). It needs two reviews before `flo gate`:
Opus and opencode-nemotron. Neither has been written yet — `reviews/` is empty.

**Gotcha discovered this session:** E01-S01's output appeared staged in the *main*
repo tree as well as its worktree. All 17 files were byte-identical to the worktree
commit, so nothing was lost, and the main tree was cleaned. Watch for this — files
committed to the milestone branch outside the story flow bypass gate and review
entirely. `flo` writes only `agents/roadmap.yaml` to ROOT by design; anything else
appearing there is contamination.

**Branch protection is live** on `main`: PR required, merge-commit only, and
`detect`/`governance`/`security` required. `backend`, `frontend` and the two CodeQL
`analyze` checks start reporting once E01-S01 lands and must be added to the
required list then — recorded as a done criterion on E01-S04.
