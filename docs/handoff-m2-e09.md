# M2 · E09 Requisitions — Codex handoff (11 Oct 2026)

For the author (Codex), the reviewers (Opus on gated stories, qwen on the rest) and the operator.
Twelve packets (`design/E09-S01..S12.md`) were read end to end against the code on `milestone/M1-budget-spine`
(head `ad23ad1`), the frozen E10 contracts, the page specs and the canvas decisions. What that found is
appended to each packet as **Pre-flight amendments (11 Oct 2026)**, which are binding. Per-story briefs are in
`docs/handoff/m2/E09-Sxx.md`.

**Precedence:** packet (with amendments) > its brief > `docs/m2-conventions.md` / `m2-decisions.md` > page spec > canvas.

## 1. How to run a story

```bash
agents/scripts/begin.sh  E09-S01                                   # worktree, installs, task.json
agents/scripts/author.sh E09-S01 docs/handoff/m2/E09-S01.md        # the brief is appended to the author prompt
agents/scripts/review.sh E09-S01 qwen                              # ungated only; Opus reviews gated stories
agents/scripts/finish.sh E09-S01                                   # merges the milestone into the story, gates, squashes
```

Codex has no network and its cap is the binding limit. Resume with `author.sh STORY .scratch/STORY.cont`. Never
switch authors on a money story. A packet block costs a run: the briefs list the places where a block is legitimate.

## 2. Order, lanes, prerequisites

| Story | Kind/size · tags | Roadmap deps | **Also needs merged** (not in the roadmap) | Author · reviewer |
|---|---|---|---|---|
| S01 header, numbering | crud/M · migration | E06-S03 | — | codex · Opus |
| S02 lines | crud/M · money, migration | S01 | — | codex · Opus |
| S03 catalogue, lookups | engine/M · migration | S02 | — | codex · Opus |
| S04 submission validation | engine/L · money | S02, E07-S06 | — | codex · Opus |
| S05 lifecycle | engine/L · money, concurrency, migration | S04, S03, E10-S05, E10-S06 | `approvals.service.withdraw`, decisions; `RoutePreview`/`RouteStep`/`TaskRead` are frozen in E10-S05 (§6) | codex · Opus |
| S06 approval effects | ledger/L · money, concurrency | S05, E07-S04, E10-S05 | E10-S06 (decision route in tests) | codex · Opus |
| S07 release, expiry | ledger/M · money, migration | S06 | — | codex · Opus |
| S08 return, amend | engine/M · migration | S05, S07, E10-S07 | `approval_state`, `material_digest`, `assert_current_approval` (E10-S07), `resume_from` (E10-S05) | codex · Opus |
| S09 links, upstream guard | api/M · — | S06, S10, E10-S07, S08 | `instances_for` (E10-S05), `assert_current_approval` (E10-S07) | nemotron-eligible (M, untagged) · qwen |
| S10 notes, attachments | security/L · migration, security | E03-S06, S01 | — | codex · Opus |
| S11 screens | ui/L · ui | S09, E04-S03, S08, E10-S06 | E10-S05 route/instance read | codex (L exceeds nemotron's M cap) · qwen |
| S12 resubmit, amend screens | ui/L · ui | S08, S11 | — | codex · qwen |

Critical path: S01 → S02 → S04 → S05 → S06 → S07 → S08 → S11 → S12. S03 runs beside S04 (both after S02). **S10
needs only S01** (the document-access registry is exercised against the requisition header): start it on the second lane as soon as S01 merges; it shares only the router include, the migration
head and `document_types.py` with the rest. Run `flo assign <STORY>` to confirm each author and reviewer.

**Roadmap dependencies:** the corrections found in the pre-flight (E10-S06 on S05; E10-S07 on S08 and S09; S08 on S09; S01 on S10) are applied in `agents/roadmap.yaml`; the table above matches it.

## 3. Rules every E09 story obeys

1. **Lock-order law.** `approval instance → project_balance rows (ascending project id) → requisition row`. E10's
   decision path locks the instance first; anything that takes balance or requisition first deadlocks (40P01, a 500).
   Submit is safe (no running instance). Withdraw and cancel call `approvals.service.withdraw` **first** (E09-S05 amendment 1).
2. **Money:** `Decimal`/`NUMERIC(18,4)`, `kernel.money.quantize` only, decimal strings on the wire, a body
   `currency`/`estimate`/`amount` is a 422 `derived_field`. Reservation and release only through `budget.service`.
3. **Idempotency keys** (convention L): `req:<id>:reserve:<round>`, `req:<id>:release:<round>:<reason>`,
   `…:reduce:<version>`, `req:<id>:allocate:1`. A release key without its round is a double release (plant 4 of S05/S07).
4. **`If-Match`** on every editable write: missing 400, stale 409 `stale_version` (convention O). `org_id` from the
   session; foreign tenant 404; closed error taxonomy.
5. **Frozen cross-lane contracts, do not change in a story:** `DocumentAdapter` (now with `read_permission`, convention P,
   not `can_read`), `Outcome`, `OutcomeContext`; `approvals.service.{start(resume_from), withdraw, preview_route,
   instances_for, approval_state, assert_current_approval}`; the material digest. `approvals` never imports `requisitions`.
6. **People are shown by email** (D-M2-16, convention Q). `identity` has no name column.
7. **Every new route:** a `COVERED` entry with a real foreign-tenant test (`tests/isolation/test_route_coverage.py`);
   every new migration appends to `tests/kernel/test_migrate.py` with down-then-up data preservation; test through
   `flo.api.health.app` with the CSRF pair; no machine paths, no secret-shaped literals.
8. **Plants:** every guard you add is broken, watched failing, restored with `git checkout -- <file>`, and listed in the report.
9. **Regenerate the OpenAPI client on the merged tree** before `finish.sh`; a conflict in `src/api/generated/*` is regenerated, never merged by hand.

### Shared files (edit only the named part; the later story merges onto the earlier)

| File | Stories | Rule |
|---|---|---|
| `migrations/*`, `tests/kernel/test_migrate.py` | S01, S02, S03, S05 (two revisions), S07, S08, S10 | next revision after the head at story start; rebase `down_revision` at finish |
| `api/requisitions.py`, `modules/requisitions/{service,schemas}.py` | S01–S09 | additive; register `lookups/{kind}` before `/{id}` (S03) |
| `api/document_types.py` | S05, S10, E10-S13 | the first to merge creates it; later ones add |
| `api/health.py`, `api/internal.py` | S01, S05, S07, S10 | one include or registration call each |
| `modules/budget/service.py` | S04 `lock_balance`, S05 `reservation_remaining`→`ReservationState`, S09 `entries_for_source` | three additive exports; keep `__all__` sorted |
| `modules/projects/{models,schemas,service}.py`, `lifecycle.py` | S01 marker, S05 counter registry, S06 `create_generated_project`/`generated_for`, S07 `abandon_generated` | named functions only |
| `modules/org/{numbering,service,settings}.py` | S01 (default map, `unit_chain`), S07 (setting) | named parts only |
| `modules/identity/models.py`, `tests/authz/*` | S05; E10-S01, S08 | catalogue tests updated in the same story; second lane unions |
| `modules/imports/multipart.py`, `kernel/multipart.py` | S10 | import tests unchanged and green first and last |
| `apps/web/src/routes/{__root.tsx,route-paths.ts}`, `components/status/{map,labels}.ts`, `styles/requisitions.css`, `tests/a11y/routes.spec.ts` | S11, S12 | S12 touches S11's files only for the two entry buttons |
| `apps/web/worker/api-proxy.ts`, `apps/web/src/api-proxy.test.ts` | S07 | one ternary branch and its test |

## 4. Coverage: nothing in the decisions or packets is unowned

### Operator and default decisions (`docs/m2-decisions.md`)

| Decision | Where implemented (E09) | Where proved |
|---|---|---|
| D-M2-1 reject modes, return keeps reservation | S05 transition table, S07, S08 | S05 `on_outcome` test, S08 return/resubmit cases |
| D-M2-2/3/4/9/10/11/14 (live membership, depth, estimate-only conditions, delegation) | E10 only. E09 contributes `fields() = {amount}` (S05) | S05 adapter test |
| D-M2-5 standalone creates budget | S04 (no funds check), S06 | S06 standalone cases |
| D-M2-6 expiry setting | S07 | S07 expiry tests, setting validators |
| D-M2-7 derived currency | S01, S02, S04, S06 | S01 `derived_field` |
| D-M2-8 basic checks, no external share | S10 | S10 scan table, no share route |
| D-M2-12 sub-project depth | S04, S06 | S04 project cases |
| D-M2-13 material fields | S05 adapter digest, S08 | S08 per-field material tests |
| D-M2-15 reserve at submit | S04, S05, S06, S07, S08, S11 | S05 ACC-001 (20 runs) |
| D-M2-16 people by email | S09, S11, S12 labels; S05 route emails | S11 stubs |

### Canvas decisions that bind E09

31 attachments tab (S10 API; **no UI story**, §7) · 33 funds block (S04, S11) · 45 never-disabled submit, 46 cents, 47 lines
and catalogue, 48 generated project disabled, 49 status, 50 quantities (S11, S02, S03) · 56/57 diff and materiality shown (S08, S12) ·
58 withdraw dialog (S11) · 66 required marks (S11) · 78 withdraw until sourcing (S05, S07, S11) · 79 material list (S08) ·
99 org budget pool (open, `notes.followup` in S06) · 112 destructive buttons, dark values owed (S11). 80–83 and 100/113 are E10.

### Requirements

REQ-001 S01 · 002 S06 · 003 S01+S04+S06 · 004 S06 · 005 S05 · 006 S02 · 007/008 S03 · 009 S09 · 010 S01 · 011 S04+S05 ·
012 S08 · 013 S05+S07 · 014 S02 (CHECKs) · 015 S02+S11 · 016 S05 · SEQ-001 S01 · XFN-009 S02+S03 · XFN-008 S11/S12 ·
WF-001 S09 · WF-002 S04+S05 · WF-003 S05+S06 · WF-004 S05 (idempotency) · WF-005 S07 · WF-006 S04+S11 · PROJ-003 S06 ·
PROJ-004 S06 · PROJ-018 S05 · ACC-001 S05+S06 · ACC-006 S07 · FIN-006/011/012 S05+S07 · DATA-002 S01/S02/S08 ·
COL-001..008, D-14 S10 · UX-003 S11/S12 · UX-004 S11/S12 · UX-005 S11 · UX-007 S11.

## 5. What the code pre-flight found (and where it is fixed)

| # | Finding | Evidence | Fixed in |
|---|---|---|---|
| 1 | No exported way to lock `project_balance`; requisitions may not import `ledger` | `budget/service.py __all__`, `ledger.py balance(lock=)` | S04 am. 1 |
| 2 | Lock-order cycle between decision (instance first) and withdraw (balance first) | E10-S06/S07 vs E09-S05 b.4 | S05 am. 1, S07 am. 2, S08 am. 1 |
| 3 | Numbering falls back to `PRJ-` for requisitions | `org/numbering.py` fallback row | S01 am. 1 |
| 4 | No unit-ancestor helper in `org.service` | only `effective_setting` recursion | S01 am. 2, S03 am. 4 |
| 5 | `reservation_remaining -> Decimal` cannot say which project holds it | `release_reservation` uses the entry's project | S05 am. 2 |
| 6 | A returned draft moved to another project: wrong funds `need`, unordered locks | S01 PATCH + S05 b.2.3 | S05 am. 4, S04 am. 2, S08 am. 2 |
| 7 | Routing problems answered as both 422 and 409; RoutePreview shape undefined | S05 contract; E10-S05 | S05 am. 5, §6 |
| 8 | Confirmation and Reservation cards need figures the API does not return | page specs §6 / §2 | S05 am. 6, S11 am. 1 |
| 9 | Line amount overflow `NUMERIC(18,4)` is a 500 | quantity×price digits | S02 am. 1 |
| 10 | `project_status_event.actor_id` is an identity FK; expiry used the nil UUID | migration 0025 | S07 body (plan ruling 9: the actor is made optional, with a `system_actor` column; S07 am. 1 is superseded) |
| 11 | Multipart reader is import-specific (template part, import error helper) | `imports/multipart.py` | S10 am. 1 |
| 12 | Open-documents stub takes only a project id; closure already blocks on `reserved` | `projects/lifecycle.py` | S05 am. 7 |
| 13 | `lookups/{kind}` shadowed by `/{id}`; `LineCreate` required fields | route order | S03 am. 1–2 |
| 14 | Worker test path, ledger label spelling, `posting_not_allowed` | `src/api-proxy.test.ts`, `reservations.py` | S07 am. 3–4, S05 am. 8 |
| 15 | Field help (XFN-008) and locale (UX-007) unowned in UI packets | `E04-S04` forms kit | S11 am. 6, S12 am. 3 |

## 6. Verify in merged E10 code before the story that consumes it

- **S05:** `RoutePreview`/`RouteStep`/`TaskRead` carry per step `{label, approver_emails, state}` and per problem `{code, step_label, message}` (not defined in E10-S05's packet).
- **S05/S06:** `OutcomeContext.permitted`, `assign_project_id` only on the final step; `withdraw(conn, scope, *, document_type, document_id, actor_id, reason)`.
- **S08:** `approvals.service.material_digest`, `start(..., resume_from)`, `approval_state`; digest input is exactly the five D-M2-13 fields.
- **S09:** `instances_for`, `assert_current_approval` (non-material edit keeps approval in force, E10-S07 revision of 11 Oct).
- **S11:** the instance read route and its `route`/`tasks` for the confirmation card; the decision list for History.
- **All:** `read_permission` on the adapter, and the `require_resolved` guard, both from E10-S05 (convention P).

## 7. Open for the operator (each packet implements the conservative reading; none blocks)

1. Decision 99 vs D-M2-5: standalone approval creates budget (S06 follow-up).
2. Who may assign a standalone request to an existing project (S06).
3. No UI story owns the **project detail Attachments tab** (canvas 31); S10 only supplies the API.
4. How long an abandoned **draft** lives (page spec question 4); no purge in M2.
5. MASTER §2.3 has no tones for `rejected`, `withdrawn`, `expired`; and 7.13's dark-mode values are unmeasured (112).
6. M1 gap: no baseline role holds `project.read` or `master.read` (S03 serves lookups instead).
7. D-M2-7 vs `ProjectCreate.currency` accepting any currency.
8. The approval node's inbox deep link (S09) until E10-S10 names a parameter.
9. `requisition_status_event` / project history names the requester for an expiry (S07 am. 1).
