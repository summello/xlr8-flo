# Delivery status: board, Mission Control and the release steps

One snapshot of "where is everything" feeds both the board and the Mission Control pane, so you
never have to ask for progress. This page says what the states mean, where the files are, what
refreshes them and what to do when they look wrong.

## States

| Story | Meaning |
|---|---|
| **To Do** | nothing exists yet |
| **Ready** | the design packet is written; nothing started |
| **In Progress** | being coded, tested or reviewed (a story worktree exists) |
| **Committed** | coded and reviewed; only `flo done` is left |
| **Merged** | in its milestone branch |
| **Deployed** | on `main` (its milestone pull request was merged) |

| Epic / milestone | Meaning |
|---|---|
| **To Do** | no story has started |
| **On Train** | work in progress, or everything merged but CI is not green (the note says why) |
| **Ready to Merge** | every story merged and the milestone branch's CI is green |
| **Deployed** | every story is on `main` |

## Files

| File | What | Committed? |
|---|---|---|
| `.flo/status.json` (primary checkout) | the snapshot both views read | no |
| `board.html` (primary checkout) | the board: Delivery and Release steps tabs | no |
| `agents/scripts/flo_delivery.py` | derives the states from refs, worktrees and GitHub | yes |
| `agents/scripts/flo_release.py` | favicon, tabs, copy buttons, release runbook | yes |

## Refreshing

The Mission Control pane runs `flo board` every minute and after any `flo`, `git` or `gh` shell
call, so the board is current whenever a Claude session is open. From a plain terminal:

```sh
cd ~/Workbench/xlr8flo
uv run --with pyyaml python agents/scripts/flo board --watch 60
```

One refresh: drop `--watch 60`. Add `--no-gh` to skip GitHub (CI and PR state show as unknown) and
`--no-fetch` to skip `git fetch`. It works from any checkout and always writes to the primary one.

## When it looks wrong

| You see | Cause | Fix |
|---|---|---|
| "updated 25 min ago" in amber | nothing is refreshing it | open Mission Control, or run `flo board --watch 60` |
| A milestone is On Train with "CI unknown" | `gh` is not signed in or timed out | `gh auth status`; the next refresh retries |
| A merged story shows Merged, not Deployed | its milestone PR is not merged to `main` yet | expected |
| A story shows In Progress with commits | no approving review has been recorded | expected until a reviewer writes `reviews/<ID>.<agent>.json` |
| `no agents/roadmap.yaml on any ref` | the repo has no fetched branches | `git fetch origin` |

The Release steps tab is the checklist for shipping a milestone; its Neon step follows the
`neondb-staging-rehearse` skill and `infra/SETUP.md`.
