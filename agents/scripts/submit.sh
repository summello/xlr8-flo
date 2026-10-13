#!/bin/sh
# usage: agents/scripts/submit.sh STORY   (the author has committed; run before launching reviewers)
# Merge the milestone in, run the light gate, push the story branch so hosted CI runs the full
# suite while the reviewers work. Output goes to .scratch/STORY.submit.log.
set -e
. "$(dirname "$0")/env.sh"
S="$1"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"; L="$R/.scratch/$S.submit.log"
cd "$R"
export PATH="$W/apps/api/.venv/bin:$PATH"
FLO="$W/apps/api/.venv/bin/python $R/agents/scripts/flo"
M="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["milestone_branch"])' "$W/task.json")"
if ! git -C "$W" merge --no-edit "$M" > "$L" 2>&1; then
  echo "merging $M into $S conflicts; resolve in $W, then rerun:"
  git -C "$W" diff --name-only --diff-filter=U
  git -C "$W" merge --abort
  exit 1
fi
# The light check runs unlocked and records the green tree; flo submit then hits that cache, so
# the lock (flo submit writes the parent repo's roadmap.yaml) is held for seconds, not minutes.
if ! (cd "$W" && $FLO check --light >> "$L" 2>&1); then
  grep -E "✗" "$L" || tail -20 "$L"; echo "full log: $L"; exit 1
fi
until mkdir "$R/.scratch/finish.lock" 2>/dev/null; do sleep 20; done
trap 'rmdir "$R/.scratch/finish.lock"' EXIT
$FLO submit "$S" >> "$L" 2>&1 || { tail -20 "$L"; exit 1; }
tail -3 "$L"
