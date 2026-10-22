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
