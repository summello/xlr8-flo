# Fleet tooling: what the `flo` commands record and enforce

One section per command added by epic E23 (fleet throughput). Each says what the command
does, what it leaves on disk, and what it cannot tell you.

## Stage durations: `flo score --durations`

Every story merged after E23-S01 carries a `durations` object in `agents/scorecard.json`:

| Key | Meaning |
|---|---|
| `wall_s` | `flo start` to `flo done` |
| `author_s` | `flo start` to the **first** `flo submit` (review rounds are not in it) |
| `review_s` | per reviewer: launch of the **last** review round to the verdict file's write time |
| `gate_s` | wall time of the passing `flo gate` run (a cached light check makes this small) |

The raw timestamps live in the story's `task.json` (`started_at`, `submitted_at`,
`resubmitted_at`, `done_at`, `gate_seconds`); `review.sh` writes the launch time to
`.scratch/<STORY>.<REVIEWER>.started`.

`flo score --durations` prints median minutes by kind, by author and by reviewer. It writes
nothing: `SCORECARD.md` and the immutable records are unchanged. A cell with fewer than
`min_sample` records prints a dash.

What it cannot tell you:

- A key is **omitted** when its inputs are missing, and when an interval is negative (clock
  skew; `flo done` prints a warning). It is never recorded as 0.
- The 106 records merged before E23-S01 have no durations and are not backfilled: a duration
  from git commit times would include waiting, not work. The last line of the report says how
  many records carry them.
- Only the last review round is timed.

## Launch models: `flo launch-model`

`author.sh` (the nemotron lane) and `review.sh` (qwen, kimi, deepseek) no longer name a model.
They ask `flo launch-model <STORY> [--reviewer NAME]`, which prints the `model` that
`agents/agents.yaml` gives that agent and writes it to `task.json` as `sessions.<agent>.model`.
The scorecard copies `sessions`, so each story's record says which model authored or reviewed it.

- Changing `model` in `agents.yaml` now changes the next launch. Before, it changed nothing.
- An agent with no `model` stops the launch. There is no default model.
- A release review (a milestone id) has no story packet, so the model is printed and not recorded.
- **Effort.** `flo launch-model <STORY> --effort` prints and records the agent's `reasoning`
  (`sessions.<agent>.effort`) the same way. `author.sh` passes Sonnet's model and effort from it
  (E23-S12). `agents.yaml` says `medium` for Sonnet: the operator decided that on 13 Oct 2026
  (`5ba2460`), and the old `high` line had never been updated. Trying `high` is a separate,
  measured experiment; only the planner edits `agents.yaml` (`agents/claude.md` §5).

## Packet pre-flight: `flo preflight`

`flo start` refuses a story unless a passing pre-flight was filed for its design packet. The
pre-flight itself (a reviewer checking the packet against the real code before an author starts)
is still run by hand or by the supervisor; this only records that it happened.

```bash
flo preflight E23-S05 --reviewer qwen --verdict pass [--findings N]
```

- The record is `.scratch/<STORY>.preflight.json` (runner-local, git-ignored). It holds the
  SHA-256 of `design/<STORY>.md` **as it was when you ran the command**. Any later edit to the
  packet, an amendment included, makes `flo start` refuse again until the pre-flight is re-run.
- A `fail` verdict is recorded and does not unlock the start. There is no bypass flag.
- `flo start` copies the record into `task.json` as `preflight`.
- `flo facts` leaves a story without a valid record out of `next` and lists it in
  `needs_preflight`, so a supervisor that starts from `next` never attempts a refused start.
  The supervisor starts from `next` (`plan()` begins `facts["next"][0]`). It ignores
  `needs_preflight`, so when every ready story lacks a record the fleet idles silently until the
  supervisor notification added in E23-S11 is deployed.
- It proves a pass was filed for this packet text. It does not prove the pre-flight was good.
- `agents/scripts/preflight.sh <STORY> [reviewer]` runs the pre-flight with qwen (the ten checks of
  `docs/handoff-2026-10-09.md`) in a detached checkout under `.scratch/preflight-wt`, then files the
  record from the `PREFLIGHT: pass|fail N` line it ends with. Run it when the supervisor raises
  "nothing can start: ... no pre-flight record".

## Author order: `policy.author_priority`

`flo assign` first drops every agent that may not take the story (role, `allowed_kinds`,
`avoid_tags`, `max_author_size`, account capacity, budget), then takes the first of
`author_priority` that is left. Since E23-S04 the order is `opencode-nemotron`, `sonnet`, `codex`.

- Nemotron cannot take a story tagged money, auth, security, migration or concurrency, a story of
  kind engine, ledger, security, migration or concurrency, or a story of size L. So the order does
  not reach gated work, and a gated story keeps its fresh Opus review whoever authors it.
- The case for the order is cost and capacity, not a demonstrated quality gain; see
  `docs/ops/sonnet-round-churn.md` and the 22 Oct entry in `agents/project-memory.md`.
- `apps/api/tests/gates/test_flo_assign.py` pins the order, the nemotron limits and the
  reviewer-is-not-author rule. Changing the order means changing that test on purpose.
- Only the planner edits `agents/agents.yaml` (`agents/claude.md` §5).

## Reassigning an author: `flo reassign`

```bash
flo reassign E23-S06 --reason "codex weekly cap reached"
```

Gives a story whose author has not submitted to the agent `flo assign` would pick **without the
current author**. It uses the same eligibility code as `flo assign` (role, kind, `avoid_tags`,
size, capacity, budget, milestone overrides, reviewer independence), so it can never choose an
agent `flo assign` would refuse. It refuses unless all of these hold, checked in this order while
holding the story lock:

1. The story is `design` or `in_progress`.
2. The author did not change while this command waited (a second reassign cannot move it again).
3. `.scratch/<STORY>.author.pid` is not a live process. A reused pid reads as alive, which errs
   toward refusing.
4. The worktree has no uncommitted or untracked change.
5. The story branch has no commit of its own past the milestone branch.

On success `task.json` gets the new `author`, `reviewers` and `opus_final` (always recomputed from
the story's tags, never from the author), and a `substitutions` entry. The old author's session
record stays. A gated story keeps its fresh Opus review whoever authors it.

The lock is `.scratch/launch.<STORY>.lock`, a kernel `flock` (see Locks below). `author.sh` holds it
around reading the author and writing the pid file (30 s, then exit 75), so a launch and a reassign
cannot interleave; `flo reassign` does not wait and exits 75 while a launch holds it.

Not done here: the supervisor does not call `flo reassign` yet. Its source is not in this
repository, so wiring it in is a separate decision (E23-S07).

## Locks: `flo with-lock`

`flo with-lock <name> [--wait SECONDS] -- <command...>` runs a command holding `flock` on
`.scratch/<name>.lock` (stdlib `fcntl`, no `flock(1)`, which macOS lacks). The kernel releases the lock
when the holder exits or is SIGKILLed, so a crash strands nothing. The file holds the holder's pid as
a diagnostic only; nobody unlocks on its text or age, and **lock files are never deleted** (deleting
one lets two processes lock different files of the same name).

| Lock | Taken by | Wait |
|---|---|---|
| `finish` | `finish.sh` (whole body); the final `flo submit` phase of `submit.sh` (both write the parent roadmap) | 1800 s |
| `submit.<STORY>` | `submit.sh` (merge, light check, submit) | 0: a second submit of the story exits 75 untouched |
| `launch.<STORY>` | `author.sh`, `flo reassign` | 30 s / 0 |

`--wait` is bounded; **exit 75** means the lock was not acquired in time (the message names the lock
and the recorded holder pid) and is safe to retry. Any other status is the command's own, and a
signal death is repeated by the wrapper. The child does not inherit the lock, so a SIGKILLed wrapper
frees it while its child runs on.

`author.sh` also refuses (exit 75, `author of <STORY> already running (pid N)`) when
`.scratch/<STORY>.author.pid` names a live process. A missing, malformed or dead pid does not block.

## Test evidence: what `ran_tests` now means

`ran_tests: true` in a review used to be the reviewer's word. For every story started after
E23-S08 (`task.json` has `evidence_required: true`) `flo gate` checks it against a record that
`flo` itself wrote.

- **The record.** Every `flo check` run (`--light` or full) appends to `task.json` `evidence`:
  profile, commit, tree, whether the tree was clean, `pass` or `fail`, the steps that ran, seconds,
  time. A red run and a dirty-tree run are recorded too; neither can vouch for anything. A cache hit
  on a commit with no record yet (same tree, new commit) adds a `cached: true` record. The last 50
  are kept. Only `flo` writes them.
- **The claim.** A verdict with `ran_tests: true` must carry
  `"evidence": {"commit": "<submitted sha>", "profile": "light"}`. `flo review` prints the sha and
  says whether `flo` already holds a passing light check for it.
- **The check.** The gate counts the review only if the commit equals `task.json` `submitted_sha`
  **and** a record exists for that commit that is `pass`, clean and for that profile (a full run
  also covers a light claim). Otherwise it fails, naming the reviewer and why, so the cause is not
  hidden behind "0/1 reviews". It is the **submitted** commit, not `HEAD`: `finish.sh` merges the
  milestone in before the gate, so `HEAD` never equals what the reviewer saw.
- **Citing is allowed.** A reviewer on the submitted commit may cite the record the author's submit
  step already made instead of re-running the check. That is the point: the 22 Oct Codex reviews ran
  out of calls before testing. As a result the scorecard's `test_compliance` now means "a passing
  check exists for the reviewed commit" and will read near 100%; it no longer says the reviewer ran
  anything.
- **Not changed.** The reviewer floor, the blocker check, and the Opus final review are as before; a
  valid record never substitutes for the Opus verdict on a gated story. A story started before the
  flag gates exactly as it did.
- **Limit.** The records live in runner-local state next to the verdicts. They guard against a
  wrong or stale claim, not against someone editing `task.json`.

## The fleet supervisor: `agents/fleet/fleet.py`

The supervisor runs one tick every `[project].interval` seconds under launchd. A tick reads
`flo facts`, decides the next step for every in-flight story, launches each step detached and exits.
It never chooses a model, never merges to a protected branch and never answers a blocked question.
Since E23-S11 its source is in this repository, with tests for its decision rules
(`apps/api/tests/gates/test_fleet_supervisor.py`).

**Deploy by copy.** The repository holds the source of truth; `~/.claude/fleet/fleet.py` is the
deployed copy. launchd runs the script from wherever it lives, so installing it from the runner
checkout would let a merged story change the supervisor between two ticks.

```bash
cp agents/fleet/fleet.py ~/.claude/fleet/fleet.py        # after a reviewed merge
cmp agents/fleet/fleet.py ~/.claude/fleet/fleet.py       # no output means no drift
python3 ~/.claude/fleet/fleet.py status --config <runner>/agents/fleet.toml
```

The supervisor needs no restart; the next tick runs the new file. Pause it first
(`fleet.py pause --config ...`) if a step is mid-launch.

E23-S11 added exactly two things to the original (sha256 `f22da510...`), both output only:

- **A note when a ready story does not start** because no Claude account has start headroom
  (`start blocked: no Claude account has room (next: <id>)`). It appears in `status.json` and
  `tick.log`. Nothing is launched for it.
- **A notification when nothing can start because no ready story has a pre-flight record.** It
  names the first such story and is de-duplicated like any other operator need.

Everything else is the file as uploaded on 22 Oct 2026.

## What `rounds` means in the scorecard

Since E23-S13 a new scorecard record takes `rounds` from the round files
(`.scratch/<STORY>.roundN.md`), the same count the supervisor caps at three
(`flo_facts.rounds_done`). One file is written per review that sent findings back to the author.

| Field | Meaning |
|---|---|
| `rounds` | rounds that sent itemised findings back, plus 1 for the first submission |
| `void_rounds` | round files with no itemised finding; **not charged** to the author |
| `escalations` | `flo escalate` runs; this is all that `task.json["rounds"]` counts now |
| `review_runs` | `{reviewer: n}`: how often each reviewer ran, reviewer failures included |
| `rounds_basis` | `"round_files"` on every record written since E23-S13 |

- **Void rounds never start.** A `changes_requested` verdict with no findings (a reviewer that hit its
  cap with a draft, as on E22-S02 round 3) is read as `malformed` by `flo facts`, so the supervisor
  asks the operator to rerun the reviewer instead of resuming the author with nothing to fix. It is
  `malformed` and not `missing` on purpose: the supervisor has no attempt counter for reviews, so
  `missing` would relaunch a reviewer that keeps writing empty verdicts on every tick.
- **Older records are marked, not rewritten.** Records without `rounds_basis` took `rounds` from a
  counter whose only writer was `flo escalate`: six records carrying observed review-run counts say
  1 round while their reviewers ran 2, 2, 2, 1, 7 and 6 times. `SCORECARD.md` says so in a Data
  quality block and shows *Rounds verified* per agent. The formulas are unchanged.
- To compare authors fairly, read only records with `rounds_basis`.
- **Progress toward Judgement day.** `SCORECARD.md` carries a line counting Sonnet's verified gated
  records against `JUDGEMENT_FLOOR` (10) and says "It is due" when it is reached; then run the script in
  `docs/ops/sonnet-round-churn.md`. "Gated" means one of `GATED_TAGS` in `agents/scripts/flo`, which mirror
  `policy.opus_final_required_tags`; a test keeps the two lists and the doc script identical.
