# agents/plan.md — Operating plan

How the build actually runs, day to day. Architecture and roadmap: `docs/claude-plan.md`. Coding rules: `AGENTS.md`.

## 1. Capacity is the design constraint

| Agent | Plan | Capacity | Consequence |
|---|---|---|---|
| Opus | Claude **Pro** | Scarcest | Designs a **whole milestone in one pass**. Final review only on gated stories. Never authors routine code. |
| Codex | ChatGPT **Plus** | Tight weekly cap | Reserved for ledger, approvals engine, concurrency, migrations. ~1 story per cycle. |
| OpenCode-nemotron | nemotron-ultra, **free** | Unmetered | Default author. Absorbs the volume. |
| OpenCode-ox | ox-alpha, **free** | Unmetered | Second free model, so a free author still gets a free reviewer. |
| Qwen | qwen-3-coder, **< $20/mo** | Spend-metered | Reviewer first, author of `size: S` stories second. Hard cap in `agents.yaml`. |

Two of four agents are rationed, so the pipeline is built to spend their tokens only where they are irreplaceable:

- Opus never reconstructs context — `flo` writes the task packet containing the design, the diff, the reviews and the requirement text.
- Codex never gets a CRUD screen.
- At least one reviewer is always from the free pool, so a rationed agent is never the reason a story cannot merge. Running **two** free models rather than one is what makes this hold when a free agent is also the author.

## 2. Milestone cycle

```
1. flo design M1              Opus writes design/ packets for every story in the milestone, one pass
2. git checkout -b milestone/M1-budget-spine
3. loop, up to 3 stories in flight:
     flo next                 pick a story whose dependencies are done
     flo start <ID>           branch + worktree + task packet
     author implements, flo check, flo submit
     two reviewers, flo gate, Opus final if gated
     flo done <ID>            squash-merge into the milestone branch
4. milestone exit criteria met (docs/claude-plan.md §4)
5. Opus reviews the whole milestone diff
6. Opus judges the milestone sufficient — exit criteria met, review clean — and only
   then proposes the PR. There is no calendar trigger, and no direct push to main.
7. open PR milestone/M1 → main, merge commit (one commit per story preserved)
```

**Three stories in flight is the ceiling.** Not because of git — because two reviewers per story means three in flight already saturates the fleet.

## 3. Design pass

Opus writes `design/<STORY_ID>.md` for every story in the milestone before any coding starts. Batched, because Pro cannot afford per-story design.

Each packet contains, and nothing else:

```markdown
# E07-S03 — Reserve budget on requisition approval

## Requirements
FIN-006, BUD-005, BUD-006, REQ-002, REQ-013, ACC-001   (text inlined, not referenced)

## Contract
POST /api/v1/requisitions/{id}:approve   Idempotency-Key required
→ 200 RequisitionRead | 409 InsufficientBudget | 409 StaleVersion | 403

## Behaviour
1. Load requisition FOR UPDATE; assert status == approval_pending and version matches.
2. Resolve funding project. Lock project_balance FOR UPDATE.
3. If available < approved_amount and not org.allow_negative_budget → InsufficientBudget.
4. Insert ledger_entry(type=reservation, source=requisition, idempotency_key).
5. Update project_balance.reserved and .available.
6. Create the requisition-generated sub-project (REQ-002); it may not receive requisitions (REQ-003).
7. Transition requisition → sourcing_in_progress. Audit. Emit outbox notification.
All of the above in one transaction.

## Acceptance criteria
- [ ] Two concurrent approvals against a project with funds for one: exactly one succeeds (ACC-001)
- [ ] Replayed Idempotency-Key returns the first response, creates no second entry (FIN-012)
- [ ] Rejection after approval releases the reservation exactly once (REQ-013, FIN-011)
- [ ] project_balance reconciles to SUM(ledger_entry) after every case

## Files
apps/api/src/flo/modules/budget/service.py, requisitions/service.py,
migrations/<rev>_reservation.py, tests/budget/test_reservation.py

## Out of scope
Commitment on PO issue (E13-S05). Transfers (E07-S07).
```

If a design packet cannot be written without guessing at a business rule, the story is blocked and the question comes to the human. It does not get guessed.

## 4. Assignment

`flo assign` computes it from `agents.yaml`:

```
author    = cheapest agent with capacity whose allowed_kinds contains story.kind
reviewers = two agents ≠ author, at least one from the free pool
opus_final= required if story.tags ∩ {money, auth, security, migration, ui, concurrency}
```

Unavailable agent (429, cap reached, endpoint withdrawn) → fallback chain in `agents.yaml`, substitution recorded in the task packet. **Two reviewers is a floor, never one.**

## 5. Cadence

| When | What |
|---|---|
| Milestone start | Opus design pass for the whole milestone |
| Per story | assign → author → 2 reviews → ≤3 rounds → gate → (Opus final) → squash-merge |
| Daily | `flo status`, `flo budget`; drain any escalations |
| Per merge | `flo done` records the story to the scorecard automatically |
| Milestone end | Opus milestone review → PR to `main` → update `project-memory.md` |
| Milestone end | Read `agents/SCORECARD.md`; re-route `allowed_kinds` in `agents.yaml` where a role is underperforming |
| Weekly | Free-tier headroom check (OPS-001); prune Artifact Registry |
| Monthly | Restore drill (OPS-003), evidence committed |

## 6. Escalation

| Situation | Action |
|---|---|
| 3 rounds, blockers remain | `flo escalate` → Opus arbitrates, ruling recorded |
| Reviewers disagree (approve vs blocker) | `flo escalate` — never merge on the optimistic verdict |
| Agent unavailable | Fallback chain; if fewer than two reviewers are reachable, the story waits |
| Qwen budget exhausted | Qwen drops out; OpenCode + Codex review; Codex spend rises — flag it |
| Requirement ambiguous | **Stop.** Human decision, then a row in `docs/claude-plan.md` §1 |
| Free-tier limit hit | OPS-004 degradation, then the §7 graduation trigger |

## 7. Throughput expectation

At 3 stories in flight and 3–5 stories/day, the 154 stories in `docs/claude-plan.md` §4 run roughly:

| Milestone | Stories | Indicative |
|---|---:|---|
| M0 Rails | 22 | ~1 week |
| M1 Budget spine | 26 | ~1.5 weeks |
| M2 Governed demand | 24 | ~1.5 weeks |
| M3 **First sellable** | 28 | ~2 weeks |
| M4 Full Phase-1 scope | 30 | ~2 weeks |
| M5 Customer-ready | 24 | ~1.5 weeks |

Indicative only, and the first estimate that is wrong is M0's — foundations always cost more than they look. Re-forecast from measured throughput after M0, not before.
