#!/bin/sh
# usage: agents/scripts/finish.sh STORY   (every assigned verdict already in <worktree>/reviews/)
# submit + done (done runs the gate). Output goes to .scratch/STORY.finish.log; on failure
# the failing check stages and gate lines are printed, not just the last line.
set -e
S="$1"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"; L="$R/.scratch/$S.finish.log"
cd "$R"
export PATH="$W/apps/api/.venv/bin:$PATH"
FLO="$W/apps/api/.venv/bin/python $R/agents/scripts/flo"
# flo done checks out and commits on the milestone branch in this repo: one finish at a time.
# ponytail: mkdir lock serialises merges; a merge queue when trains outgrow it
until mkdir "$R/.scratch/finish.lock" 2>/dev/null; do sleep 20; done
trap 'rmdir "$R/.scratch/finish.lock"' EXIT
# Gate the merged result, not the story's stale base: two lanes that each passed alone broke
# M1 together on 10 Oct (E06-S07 + E08-S04). Merge the milestone in first, under the lock.
M="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["milestone_branch"])' "$W/task.json")"
if ! git -C "$W" merge --no-edit "$M" > "$L" 2>&1; then
  echo "merging $M into $S conflicts; resolve in $W, then rerun:"
  git -C "$W" diff --name-only --diff-filter=U
  git -C "$W" merge --abort
  exit 1
fi
if $FLO submit "$S" >> "$L" 2>&1 && $FLO done "$S" >> "$L" 2>&1; then
  tail -1 "$L"
else
  grep -E "✗|GATE FAILED|^error:" "$L" || tail -20 "$L"
  echo "full log: $L"
  exit 1
fi
