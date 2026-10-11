# M2 — Governed demand: Codex handoff index (11 Oct 2026)

For the operator, the authors (Codex), and the reviewers (Opus on gated stories, qwen on the rest).
25 stories: E09 requisitions (S01..S12) and E10 approvals (S01..S13). Design is done and was audited
against the code by qwen in two passes; every finding is settled in the packets.

**Read order for an author:** this file § 1 and § 5 → the story's packet **to its last line** (later
"Pre-flight amendments" sections override earlier text) → its brief → `docs/m2-conventions.md` →
`docs/m2-decisions.md` → `docs/author-traps.md`.

**Precedence:** packet (with amendments) > its brief > `docs/m2-conventions.md` / `docs/m2-decisions.md` >
`design-system/pages/*` > canvas. MASTER and `tokens.css` outrank all UI values (AGENTS.md).

**Per-story briefs:** E09 → `docs/handoff/m2/E09-S01..S12.md`, written by the M2-E09 handoff session and
summarised in `docs/handoff-m2-e09.md`. E10 → `docs/handoff/m2/E10-S01..S13.md`, generated from the
roadmap and each packet's own Files and Tests sections. Pass the brief to the author:
`agents/scripts/author.sh <STORY> docs/handoff/m2/<STORY>.md`.

## 1. Before the first story (operator or Opus, once)

1. **Create the M2 branch with the design on it.** `milestone/M2-governed-demand` exists (from M1 `ad23ad1`).
   Merge M1's newer commits (`37fc1d3` CodeQL fixes and anything since) and then `docs/m2-design` into it
   (docs only), and push. `flo start` branches story worktrees from it, so the packets must be on it.
2. **PR 15 (M1) is not merged to `main`.** M2 stacks on M1; M2's draft PR therefore carries M1's diff too until
   PR 15 merges. Merge order: PR 15 first (operator; it deploys 23 migrations), then M2's PR. Do not reorder.
3. **Open the draft milestone PR for M2 when its first story merges** (AGENTS.md 2.4). Red CI stops the line.
4. **Environment:** `/Volumes/Omnissiah` must be mounted, or export `XDG_CACHE_HOME` and `UV_CACHE_DIR` to `~/.cache`
   first (opencode, uv and gh fail otherwise). Codex has no network: `begin.sh` installs everything.
5. **Codex cap.** Its usage cap, not the design, is the throughput limit (it stopped after about seven runs on
   10 Oct). Resume with `agents/scripts/author.sh STORY .scratch/STORY.cont`; never change authors on a money story.

## 2. Build order (from the roadmap's dependency graph)

The critical path is **13 stories deep**. Level N needs every dependency at lower levels merged.

| Level | Stories (kind/size · tags) |
|---|---|
| L1 | **E10-S01** (migration/L) · **E09-S01** (crud/M · migration) |
| L2 | E10-S02 (engine/L · money) · E09-S02 (crud/M · money, migration) · E09-S10 (security/L · migration, security) |
| L3 | E10-S03 (engine/M) · E09-S03 (engine/M · migration) · E09-S04 (engine/L · money) |
| L4 | E10-S04 (engine/M · auth) · E10-S11 (ui/L) |
| L5 | **E10-S05** (engine/L · auth, concurrency) |
| L6 | **E10-S06** (engine/M · security) · E10-S12 (ui/L) |
| L7 | **E09-S05** (engine/L · money, concurrency, migration) · E10-S07 (concurrency/M) · E10-S08 (engine/L · migration) |
| L8 | **E09-S06** (ledger/L · money, concurrency) · E10-S09 (api/M) · E10-S13 (engine/M · migration, money) |
| L9 | E09-S07 (ledger/M · money, migration) · E10-S10 (ui/L) |
| L10 | E09-S08 (engine/M · migration) |
| L11 | E09-S09 (api/M) |
| L12 | E09-S11 (ui/L) |
| L13 | E09-S12 (ui/L) |

Critical path: E10-S01 → S02 → S03 → S04 → S05 → S06 → E09-S05 → S06 → S07 → S08 → S09 → S11 → S12.
**E09 cannot reach S05 until E10-S06 has merged**, so the first six levels belong to E10; E09's S01..S04
and S10 are the work that runs beside it.

## 3. Lanes

Three Codex lanes at most (`policy.max_stories_in_flight` is 4; each lane is serial, because migrations chain
and the test database is per worktree). `finish.sh` merges one story at a time under its lock.

| Lane | Stories, in order | Why |
|---|---|---|
| **A, approvals spine** | E10-S01 → S02 → S03 → S04 → S05 → S06 → S07 → S08 → S09 → S13 | The critical path; every story is gated, so Opus reviews each |
| **B, requisitions** | E09-S01 → S02 → S03 → S04, then (after E10-S06) S05 → S06 → S07 → S08 → S09 → S11 → S12 | Starts with four stories that need nothing from E10 |
| **C, beside** | E09-S10 (after E09-S01), then E10-S11 (after E10-S03) → E10-S12 (after S05 and S11) → E10-S10 (after S09) | Off the critical path; E10-S11 and S12 are UI |

Only E10-S09 and E09-S09 are nemotron-eligible (api/M, no gated tag); everything else is Codex by the
routing rules (L stories exceed nemotron's M cap, UI included). Run `flo assign <STORY>` to confirm.

## 4. Review plan

- **Opus (17 stories, gated tags):** E09-S01, S02, S03, S04, S05, S06, S07, S08, S10; E10-S01, S02, S04, S05,
  S06, S07, S08, S13. Opus plants the packet's **Plants** against each gate and runs the tests.
- **qwen (8 stories):** E10-S03, S09, S10, S11, S12 and E09-S09, S11, S12. Opus then reads each lane's
  combined diff once (AGENTS.md 2.5) and the whole milestone before the PR leaves draft.
- **Release review:** qwen and deepseek on the milestone diff, plus Opus's milestone review, before the PR
  leaves draft. The M1 release reviews are already stale and are re-run for M1 separately.
- **Bot comments:** read the PR's CodeQL threads after each push; fix real ones, dismiss false ones with a reason.

## 5. Shared-file hazards (where parallel lanes collide)

| File | Stories that edit it | Rule |
|---|---|---|
| `migrations/` head, `tests/kernel/test_migrate.py` | every migration story (E09-S01, S02, S03, S05, S07, S08, S10; E10-S01, S05, S06, S07, S08, S13) | next revision after the head **at start**; the second lane to merge re-points `down_revision`; append the revision to the test list |
| `modules/identity/models.py`, `tests/authz/test_models.py`, `test_migration.py`, `conftest.py` | E10-S01, E10-S08, E09-S05 | new permissions ship in a permissions-only migration (convention I) |
| `kernel/authz.py` | E10-S04 (holders protocol), E10-S05 (`require_resolved`, `require_member`) | E09 stories read it; convention P |
| `kernel/session/middleware.py` | E09-S10 only (a handler-supplied `Content-Security-Policy: sandbox` is kept in addition to the fixed header; anything else is still replaced) | the one authorised kernel security edit in E09; Opus reviews it with its planted test in `tests/session/` |
| `modules/identity/service.py` | E10-S06, E09-S05 (`emails_by_id`, read-only) | whichever merges first adds it |
| `api/health.py` router includes | every story that adds a router | one include line each; trivial conflicts |
| `api/internal.py` | E10-S08, E10-S13, E09-S05, S07, S10 | one registration call each |
| `api/document_types.py` | E09-S05, E09-S10, E10-S13 | created by whichever merges first; the others add an adapter |
| `modules/org/settings.py` | E10-S03, E10-S04, E09-S07 | one setting each, with its validator |
| `modules/projects/*`, `modules/budget/*` | E09-S01, S05, S06, S07, E10-S13 | M1 modules; edits are authorized only where the packet names them |
| `apps/web/worker/api-proxy.ts` and `src/api-proxy.test.ts` | E10-S08, E09-S07 | one scheduled path each |
| `apps/web/src/routes/__root.tsx`, `route-paths.ts` | E09-S11, S12; E10-S10, S11, S12 | routing only, plus E09-S11's single navigate-guard call |
| `apps/web/src/lib/navigation-guard.ts` | E09-S11 creates; E09-S12 uses | one mechanism |
| generated OpenAPI client (`apps/web/src/api/generated/*`) | every API story | on a conflict, regenerate on the merged tree; never hand-merge |
| `tests/isolation/test_route_coverage.py` | every story with a path-parameter route | one `COVERED` entry per route |

## 6. Frozen cross-lane contracts (do not change in a story)

Defined in `design/E10-S05.md`, `E10-S06.md`, `E10-S07.md`; conventions H, K, P, R.

- `DocumentAdapter` (`document_type`, `approve_permission`, `read_permission`, `accepts_project_assignment`,
  `fields()`, `snapshot()`, `summaries()`, `on_outcome()`), `Outcome`, `OutcomeContext`.
- `approvals.service`: `start(…, resume_from)`, `preview_route`, `get_instance`, `active_instance`,
  `instances_for`, `withdraw`, `approval_state`, `requires_resubmission`, `supersede`, `assert_current_approval`,
  `material_digest`, `resolve_version`.
- Response shapes: `RouteStep`, `RouteProblem`, `RoutePreview`, `TaskRead`, `InstanceRead` (people by email).
- `approvals` never imports `requisitions` or `projects`; document modules register adapters
  (`api/document_types.py`). Lock order: instance → `project_balance` ascending → document (convention R).

## 7. What blocks a story legitimately

A money rule, a state transition, an authorization rule, or a field a frozen contract does not name. Every
block on M1 was a packet-versus-code gap, which the pre-flight now catches; if you meet one anyway, write
`notes.blocked` with the exact field and leave partial work uncommitted. The packet owner answers by
appending an amendment, never by editing the body.

## 8. Operator decisions still open (none blocks starting)

Listed in `docs/m2-decisions.md` "Follow-ups" and `agents/project-memory.md` "Open questions":
org-level budget pool versus standalone requisitions (decision 99 versus D-M2-5); project currency versus
organization currency (D-M2-7); who may assign a standalone request to an existing project; the MASTER status
map for rejected, withdrawn, expired; baseline read access to projects and master data; a stored profile
name (people are emails in M2, D-M2-16); and the defaults D-M2-9, D-M2-10, D-M2-14 to confirm.

## 9. Known-stale lines in `docs/handoff-m2-e09.md`

It predates the second pass: E09-S07 now has a migration (`project_status_event.actor_id` nullable plus
`system_actor`); E09-S08 never calls `reduce_to` (an estimate change releases and re-reserves in round + 1);
the E09-S05, S08, S09 and S10 dependency lines changed (§ 2 above is current). Prefer this file where they differ.
