#!/bin/sh
# usage: agents/scripts/finish.sh STORY   (submitted, every assigned verdict in <worktree>/reviews/)
# Gate once on the merged result and squash-merge. The full suite already ran on GitHub against
# the submitted commit (gated stories must be green there; flo gate checks). Output goes to
# .scratch/STORY.finish.log; on failure the failing check and gate lines are printed.
set -e
. "$(dirname "$0")/env.sh"
S="$1"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"; L="$R/.scratch/$S.finish.log"
cd "$R"
# flo done checks out and commits on the milestone branch in this repo: one finish at a time.
# The kernel drops a flock when its holder dies, so a SIGKILL cannot strand it (E23-S15).
if [ -z "${FLO_FINISH_LOCKED:-}" ]; then
  exec python3 "$R/agents/scripts/flo" with-lock finish --wait 1800 -- env FLO_FINISH_LOCKED=1 sh "$R/agents/scripts/finish.sh" "$@"
fi
export PATH="$W/apps/api/.venv/bin:$PATH"
FLO="$W/apps/api/.venv/bin/python $R/agents/scripts/flo"
# Gate the merged result, not the story's stale base: two lanes that each passed alone broke
# M1 together on 10 Oct (E06-S07 + E08-S04). Merge the milestone in first, under the lock.
# When the milestone has not moved since submit the tree is unchanged and the gate's check is
# a cache hit.
M="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["milestone_branch"])' "$W/task.json")"
if ! git -C "$W" merge --no-edit "$M" > "$L" 2>&1; then
  echo "merging $M into $S conflicts; resolve in $W, then rerun:"
  git -C "$W" diff --name-only --diff-filter=U
  git -C "$W" merge --abort
  exit 1
fi
if $FLO done "$S" >> "$L" 2>&1 && git push origin "$M" >> "$L" 2>&1; then
  tail -2 "$L"
else
  grep -E "✗|GATE FAILED|^error:" "$L" || tail -20 "$L"
  echo "full log: $L"
  exit 1
fi
