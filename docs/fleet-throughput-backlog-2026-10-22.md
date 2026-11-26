# XLR8 FLO — fleet throughput story backlog

Hand-off file for a Claude Code cloud session to write detailed design packets and implement.
This is a candidate story list, not final `agents/roadmap.yaml` IDs — assign epic/story numbers
with `flo roadmap`/`flo assign` tooling when packets are written; dependencies below are by
label (T1, T2, ...), not final IDs.

**Overlap check before starting T9/T10**: `agents/roadmap.yaml` already has `E22-S12`
("Balanced E2E shards on a production build; backend static job; combined checks need detect"),
status `in_progress`, on this branch's parent. That story covers the same ground as T9 (shard
rebalancing) and T10 (production-build artifact reuse) in this backlog. **Check E22-S12's
current state (`flo review E22-S12` or read its task packet) before writing T9/T10's packets** —
either fold T9/T10 into whatever E22-S12 left undone, or drop them if it's already covered.

## Why

Operator wants story/milestone cycle time cut >2x, running the agent fleet like a real dev org,
with zero compromise on quality/security gates. Two adversarial Codex passes (22 Oct, against
the live `xlr8flo-m3run` worktree) and four operator decisions after round 2 shaped this into
the backlog below. Full evidence trail and both review rounds are in the appendix at the bottom
— read it before writing packets for T2–T4, since it has the exact code locations and the
reasoning each story's acceptance criteria should cite.

## Operator decisions applied (22 Oct)

1. **nemotron model stays as configured.** `nvidia/nemotron-3-ultra-550b-a55b:free` — confirmed
   (OpenRouter listing) 1M-token context window, MoE 55B active/550B total params, $0 cost; its
   reachability is already empirically proven by the live scorecard (10/10 clean merges, 100.0
   author score). No "super" slug search needed — this is settled, not an open question.
   Caveat worth carrying into T3's packet: OpenRouter's free-tier notice says NVIDIA logs usage
   for security/product-improvement purposes — irrelevant here since this lane never touches
   gated (money/auth/security) stories, but worth a one-line note in the packet so it isn't
   rediscovered later.
2. **Sonnet's round-churn must be analyzed before the author-priority reorder ships.** T1 is now
   a hard dependency of T2, not a parallel nice-to-have.
3. **PR cadence stays as-is.** The "1-2 PRs/day" idea is struck — operator agreed with both
   Claude and Codex that it doesn't move the throughput number and isn't worth the added
   release-review complexity. Replacement: the Opus planner may recommend (operator decides) an
   early PR to `main` **before** a milestone completes, case-by-case, for safety/security/
   dependency reasons — this is a judgment call written into the planner's role contract, not a
   scheduled cadence, and not a story in this backlog.
4. This file is the backlog; a cloud session writes the packets.

## Story backlog

### T1 — Sonnet round-churn analysis (prerequisite, blocks T2)
**Kind:** chore/analysis. **Size:** S. **Depends on:** nothing.
Slice `agents/scorecard.json`'s existing per-story records (kind, size, tags, rounds) for every
Sonnet-authored story. Report mean rounds by kind and by tag — confirm or refute whether the
fleet-wide 1.72 mean is concentrated in specific kinds (e.g. `ledger`/`migration`/`security`) or
spread evenly. **Acceptance criteria:** a written breakdown (table), not a guess; if concentrated
in gated kinds, recommend narrowing Sonnet's `allowed_kinds` in the same packet; if spread evenly,
recommend T2 proceed as scoped and flag T6 (mandatory packet pre-flight) as the likely fix
instead. **This story's output is a decision input for T2 — do not write T2's packet until T1's
finding is in hand.**

### T2 — Reorder `author_priority` by measured record
**Kind:** chore/fleet-config. **Size:** S. **Depends on:** T1's finding.
Change `agents/agents.yaml` `policy.author_priority` from `[sonnet, opencode-nemotron, codex]` to
`[opencode-nemotron, sonnet, codex]` (flat list — `cmd_assign()` already filters candidates by
role/kind/`avoid_tags`/size/capacity before this list applies, so no kind-conditional structure is
needed). Before shipping: reconcile a discrepancy found during research — nemotron's scorecard
history already includes L-sized and engine-kind records, which doesn't match its configured
`max_author_size: M` / `allowed_kinds` (crud, ui, api, chore, docs, test) read from `agents.yaml`.
Find out why (config changed after those records? manual substitution?) and correct the
config/history mismatch as part of this story, not after. **Acceptance criteria:** reorder
shipped; the nemotron config/history discrepancy is explained and reconciled; `test_flo_assign.py`
(new — zero coverage exists today on `rank()`/`author_priority` selection) locks the new order so
a future edit can't silently revert it.

### T3 — Fix nemotron's launcher model-pin (prevents silent drift)
**Kind:** chore. **Size:** S. **Depends on:** nothing (can run parallel to T1/T2).
`agents/scripts/author.sh` currently hardcodes the nemotron model string rather than reading it
from `agents.yaml`. Fix the launcher to read from config, and record the model actually launched
per story (into `task.json`/scorecard) rather than assuming it matches config. **Why this
matters:** without it, a future config edit (even just correcting a typo) silently doesn't change
what launches, and a silent model swap would invalidate the scorecard history T2 depends on.
**Acceptance criteria:** launcher reads model from config; launched model is recorded per story;
a test proves a config change actually changes the launch command.

### T4 — Capacity-aware reassignment (`flo reassign`)
**Kind:** concurrency/fleet. **Size:** M. **Depends on:** nothing, but should land after T2 so
the eligibility rules it re-derives are the corrected ones.
`fleet.py`'s `decide()` only waits when an author hits `"limit"` — it never reassigns. Add a
bounded reassignment path, gated by **all** of:
- **Full eligibility re-check**: the complete `flo assign` filter (role, kind, `avoid_tags`,
  size, capacity, overrides, reviewer independence) — not an abbreviated version.
- **Process-liveness check**: the worker's PID from `author.sh`'s launch — `task.json`/`reviews/`
  are gitignored, so a clean `git status` alone says nothing about whether a background worker
  is still running.
- **Clean-worktree check**: `git status --porcelain` empty and zero commits past the story's
  branch point — necessary but not sufficient alone; combine with the liveness check above.
- **Per-story lock** around the check-then-reassign sequence to close the race between "looks
  idle" and "actually nothing is running."
- Gated-tag stories (`opus_final_required_tags`) still get the mandatory fresh Opus review
  regardless of which agent authors — reassignment never touches that invariant.
**Acceptance criteria:** `flo reassign <SID>` implemented with all four checks; `test_fleet.py`
extended to cover the new path (including a case that proves a gated story's Opus-final
requirement survives reassignment); a stale/not-actually-idle worker cannot be reassigned out
from under itself (prove it with a planted violation per AGENTS.md §2.2's gate-testing rule).

### T5 — Headroom-ranked new-story account pick
**Kind:** chore/fleet. **Size:** XS. **Depends on:** nothing.
`fleet.py`'s `plan()` picks the first eligible account (`room[0]`) for a new story start, instead
of ranking by remaining capacity the way `opus_account()` already does for Opus reviews. Make
`room` selection headroom-ranked using the same capacity data `opus_account()` already reads.
**Acceptance criteria:** `test_fleet.py`'s existing "first eligible account wins" fixture is
deliberately updated to pin headroom-ranked selection instead, and the change is proven against
a fixture where the first-listed account has less headroom than a later one.

### T6 — Mandatory packet pre-flight before dispatch
**Kind:** chore/process. **Size:** S. **Depends on:** nothing.
The qwen/Codex packet pre-flight (checking a design packet against the real code before
dispatch) has repeatedly caught real gaps cheaply (e.g. E08-S03's resolver wiring) but is applied
inconsistently session-to-session. Make it a required step `flo start`/`flo assign` enforces
(can't dispatch a story without a recorded pre-flight pass), not a practice that depends on
whoever's driving remembering to run it. **Acceptance criteria:** `flo start` refuses to launch a
worker without a pre-flight record in `task.json`; a test proves the refusal.

### T7 — Structured, SHA-bound test-evidence records
**Kind:** api/chore. **Size:** M (bigger than it first looks — scope as its own story).
`cmd_gate()` today only checks a reviewer's self-reported `ran_tests: true` boolean — nothing
parses or attributes real pytest/vitest/playwright output to a specific reviewer, run, or commit
SHA. This has let a Codex re-review approve with `ran_tests: false` three times (22 Oct) without
being caught before merge, each occurrence costing a full extra review round. Design structured,
SHA-bound test-evidence records (what ran, against which commit, with what result) and have
`flo gate` verify evidence instead of trusting the field. **Acceptance criteria:** a review
cannot report `ran_tests: true` without an attached, verifiable evidence record keyed to the
reviewed SHA; a planted violation (a verdict claiming tests ran, with no matching evidence record)
is caught by the gate, per AGENTS.md §2.2.

### T8 — Stage-duration measurement in the scorecard
**Kind:** chore/analysis. **Size:** S. **Depends on:** nothing; land early since T2/T4/T6's real
impact can't be measured without it.
`agents/scorecard.json` records kind/size/tags/rounds but no stage durations (author start→done,
review start→verdict, gate run time). Add duration fields so before/after comparisons for every
other story in this backlog are measurable, not asserted. **Acceptance criteria:** new fields
populated going forward; a short report format (`flo score --durations` or similar) that slices
by kind/agent.

### T9 — E2E shard rebalancing
**Kind:** ci. **Size:** S. **Depends on:** needs its own packet — E22-S11's packet explicitly
excluded shard-balance work and performance-test isolation must be preserved, so this is not a
drive-by edit to that story's leftovers.
Three Playwright shards exist post-E22-S11; shard 1 runs ~12min, the other two ~30s. Redistribute
specs by measured duration instead of file count. **Acceptance criteria:** shard durations within
a reasonable spread of each other; perf-test isolation unchanged; a plant proves the rebalanced
config still fails on a real regression in any shard.

### T10 — CI dist-artifact reuse between frontend and deploy
**Kind:** ci. **Size:** S. **Depends on:** nothing.
Today only the `deploy` job builds `apps/web/dist` — there's no existing frontend production
build to deduplicate, so this is "add a build+upload step, then have deploy download it," not
pure dedup removal. Add a `dist` build + `actions/upload-artifact` step in CI, from the same
workflow run; have `deploy` use `actions/download-artifact` instead of a second `npm ci` + build,
re-running the existing secret/project-id/hostname grep checks against the downloaded bundle. No
change needed to Trivy/SBOM/digest steps — confirmed (direct read of `ci.yml`) those only ever
touch the API Docker image, never the SPA bundle. **Explicitly out of scope:** reusing the
OpenAPI schema between `backend` and `frontend` — `backend` installs unlocked pip deps while
frontend's freshness check deliberately runs against `uv.lock`; reconciling that is a separate
design decision, not bundled into this story.

### T11 (optional, unmeasured benefit — lowest priority) — Reduce fleet poll latency
**Kind:** chore/infra. **Size:** S. **Depends on:** nothing.
`agents/fleet.toml`'s tick interval is a fixed 300s poll; a completion-triggered wakeup could cut
accumulated dead time between pipeline stages. **Caveat, stated in the packet, not just here:**
benefit is unmeasured — don't ship on faith; land T8 first so this can actually be measured. Must
preserve existing concurrency caps and locking so a faster trigger can't cause duplicate launches.

## Non-negotiables — binding on every story above

- `opus_final_required_tags` (money/auth/security/migration/concurrency) forces a fresh Opus
  review on every story regardless of who authored it or any capacity number. These stories
  change **which eligible agent is picked when several already qualify** — never which review
  depth a gated story gets, and never whether Opus signs off.
- `min_reviewers`, the blocker check, migration reversibility tests, `flo check`'s full suite —
  unchanged by anything here.
- T4's reassignment can never move a story to an agent `flo assign`'s own rules wouldn't have
  picked in the first place.

## Suggested sequencing

1. T8 first (so everything else is measurable), T1 and T3 in parallel with it.
2. T2 after T1's finding; T6 any time.
3. T4, T5, T7, T10 in any order once T2 has landed (T4 specifically benefits from T2's corrected
   eligibility rules).
4. T9 and T11 last — both need their own packets and neither is on the critical path.

---

## Appendix — full evidence trail and both Codex review rounds

### Evidence (verified directly against code, not taken from memory alone)

**Throughput baseline:** "52/168 done at ~6-7 stories/day, strictly serial" (10 Oct,
`agents/project-memory.md`) — attributed to serialization and packet halts, not bad code.

**Already shipped, not re-litigated here:** per-worktree test DB; risk-scaled review (gated
tags → Codex author + Opus review, everything else → qwen + Opus train-diff read); draft PR
opens at milestone's first story merge; `E22-S11` (22 Oct) CI sharding (backend 28→14min,
frontend 13→12min) plus session-scoped migration-template fixtures (confirmed directly in
`apps/api/tests/conftest.py` — this is why T9 is the only CI item left, not a migration-replay
fix); the external fleet supervisor (`~/.claude/fleet/fleet.py`, 300s tick, `max_stories_in_flight`
raised 4→6 on 21 Oct specifically because review-bound lanes were leaving Claude accounts idle —
independent confirmation that review throughput, not account capacity, was already suspected as
the real constraint).

**Scorecard (106 merged stories, `xlr8flo-m3run/agents/SCORECARD.md`):**
`opencode-nemotron` 10 stories, 100% clean, 1.00 rounds, score 100.0. `codex` 67 stories, 97%
clean, 1.04 rounds, score 97.8. `sonnet` 29 stories, 59% clean, **1.72 rounds**, score 68.4.
`policy.author_priority: [sonnet, opencode-nemotron, codex]` means the worst-performing author is
tried first every time a Claude Pro worker slot is free (4 slots: 2 accounts × 2 workers each) —
this is T2's justification, and Sonnet's round churn is why T1 has to run first.

**Assignment mechanics:** `cmd_assign()`'s `rank()` already filters by role/kind/`avoid_tags`/
size/capacity before applying `author_priority` — confirmed by direct code read, which is why T2
is a flat list reorder, not a new conditional structure. No test file anywhere pins this ranking
behavior (zero coverage) — hence T2's and T4's test requirements.

**Migration-renumber risk (context for why PR cadence was struck, not a story here):**
`migrate.py`'s `discover_migrations()` raises on a duplicate revision id *before* checking for
multiple heads, and a revision id already applied anywhere can't be safely renamed. This was
already a recurring manual fix 2-3 times a week before this backlog existed — a reason the struck
PR-cadence idea would have made things worse, not a reason to build renumber automation now.

### Round 1 Codex pass — objections and resolutions

Round 1's research was done against the older M1-era checkout, not the live M3 worktree, and its
root-cause ranking (static assignment/capacity-blindness as dominant) didn't hold up against the
live scorecard showing Sonnet's round churn as the bigger, already-measured tax. The original
items (reassignment safety "zero commits" gate, CI artifact reuse, migration-template dedup
already shipped, PR-cadence throughput claim) were each corrected; those corrections are folded
into T2/T4/T9/T10 above and into the operator's decision to strike PR cadence.

### Round 2 Codex pass — objections and resolutions (against `xlr8flo-m3run` directly)

- **Author-priority reorder**: confirmed a flat list is sufficient (no conditional YAML needed);
  flagged the nemotron config/history mismatch now folded into T2's acceptance criteria, and the
  mistagging risk (eligibility trusts story metadata, not actual scope) as a packet-discipline
  caveat, not something the reorder itself fixes.
- **Reassignment gate**: the original two-check version (tag + clean worktree) was an
  insufficient handoff protocol — `task.json`/`reviews/` are gitignored, so clean git status says
  nothing about a live background process. T4 above has the corrected four-part gate.
- **CI artifact reuse**: no frontend production build currently exists to reuse (only `deploy`
  builds `dist`) — T10 is scoped as "add the build step, then reuse it," and the OpenAPI-schema
  reuse idea was dropped (locked-vs-unlocked install mismatch, would also serialize frontend
  behind backend).
- **PR cadence**: confirmed no stories/day win; sharpened the one legitimate angle
  (completion-to-production lead time, a different metric from throughput) — the operator then
  struck this item entirely on the basis that both reviews agreed it wasn't worth the complexity.
- **Test-evidence enforcement**: confirmed this needs new structured plumbing, not a parsing
  tweak — T7 is scoped and sized accordingly (M, not S).
- **Further improvements proposed by Codex, folded in**: stage-duration measurement before
  claiming any multiplier (T8), promoting E2E shard rebalancing into this programme (T9), fleet
  poll-interval latency as a low-priority, unmeasured-benefit item (T11), and the launcher
  model-pin drift risk (T3).

No further adversarial round scheduled — round 2's objections were concrete and code-verified,
and the remaining open items are now operator decisions already made (above), not unresolved
disagreements with Codex.
