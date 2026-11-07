#!/bin/sh
# usage: agents/scripts/codex-run.sh STORY [round-file]   (manual)
# Codex authors STORY (operator, 19 Oct: Codex is underused; the planner reviews its work).
# Without a round file: flo start, installs, author=codex, in-flight commit, then the author.
# Waits for the author and submits. Review is the planner's, not this script's.
. "$(dirname "$0")/env.sh"
flo_manual_guard
S="$1"; ROUND="$2"; R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R" || exit 1; W="$R-$S"
log() { echo "$(date +%T) $*"; }
if [ -z "$ROUND" ]; then
  log "start $S for codex"
  uv run -q --no-project --with pyyaml python agents/scripts/flo start "$S" > /dev/null || exit 1
  ( cd "$W/apps/api" && uv sync --frozen --extra dev -q && uv pip install --python .venv/bin/python pyyaml -q )
  ( cd "$W/apps/web" && npm ci --silent )
  python3 - "$W/task.json" <<'EOF'
import json, sys
p = sys.argv[1]; t = json.load(open(p))
t["author"] = "codex"
t.setdefault("notes", {})["routing"] = "operator 19 Oct: codex authors, planner (Opus) reviews"
json.dump(t, open(p, "w"), indent=2)
EOF
  agents/scripts/inflight.sh "$S"
  agents/scripts/author.sh "$S" > /dev/null
else
  log "round $S with $(basename "$ROUND")"
  # codex exec has no resume here: a round is a fresh run with the findings appended
  agents/scripts/author.sh "$S" "$ROUND" > /dev/null
fi
sleep 20; while [ -f ".scratch/$S.author.pid" ] && kill -0 "$(cat ".scratch/$S.author.pid")" 2>/dev/null; do sleep 30; done
tail -c 1500 ".scratch/$S.log"
python3 -c "import json,sys; b=json.load(open('$W/task.json'))['notes'].get('blocked'); print('BLOCKED:', b) if b else None; sys.exit(1 if b else 0)" || exit 4
agents/scripts/submit.sh "$S" > ".scratch/$S.submit.log" 2>&1 || { log "STOP: submit failed"; tail -15 ".scratch/$S.submit.log"; exit 2; }
log "submitted $(git -C "$W" rev-parse --short HEAD); planner reviews"
