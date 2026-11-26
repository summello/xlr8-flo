#!/bin/sh
# usage: agents/scripts/inflight.sh STORY   (after begin.sh, from the runner checkout)
# Commit and push the roadmap with STORY in flight. flo start leaves roadmap.yaml dirty, and a
# later flo done would sweep it into an unrelated story commit (16 Oct); committing it without
# `flo roadmap` turned milestone CI red (13 Oct). One roadmap commit at a time across lanes.
set -e
. "$(dirname "$0")/env.sh"
S="$1"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"
cd "$R"
M="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["milestone_branch"])' "$W/task.json")"
PY="$W/apps/api/.venv/bin/python"
until mkdir "$R/.scratch/roadmap.lock" 2>/dev/null; do sleep 5; done
trap 'rmdir "$R/.scratch/roadmap.lock"' EXIT
git pull -q --no-rebase origin "$M"
$PY agents/scripts/flo roadmap > /dev/null
$PY agents/scripts/flo roadmap --check > /dev/null
git add agents/roadmap.yaml docs/claude-plan.md agents/project-memory.md
git diff --cached --quiet || { git commit -q -m "chore: $S in flight"; git push -q origin "$M"; }
