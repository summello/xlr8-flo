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
- Not covered: Sonnet's `--effort medium` in `author.sh` while `agents.yaml` says
  `reasoning: high`. That is a value disagreement, not a missing link; the planner decides
  which is right and edits `agents.yaml` (`agents/claude.md` §5).

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
  Whether the supervisor reads `next` that way cannot be checked from this repository.
- It proves a pass was filed for this packet text. It does not prove the pre-flight was good.

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

The lock is `.scratch/<STORY>.lock`, an atomic `mkdir` as in `finish.sh`. `author.sh` takes the
same lock around reading the author and writing the pid file (30 s, then it gives up), so a launch
and a reassign cannot interleave. A crash leaves the directory behind; remove it by hand.

Not done here: the supervisor does not call `flo reassign` yet. Its source is not in this
repository, so wiring it in is a separate decision (E23-S07).

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
