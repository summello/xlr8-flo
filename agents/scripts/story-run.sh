#!/bin/sh
# usage: agents/scripts/story-run.sh STORY ACCOUNT [round-file]   (manual; the fleet supervisor does this itself)
# Without a round file: begin the story on ACCOUNT, wait for the author, submit, Codex review,
# and (gated stories) a fresh Opus review on the other account. With a round file: resume the
# author with it, then submit and re-review. Stops after the reviews; the planner reads verdicts.
. "$(dirname "$0")/env.sh"
flo_manual_guard
S="$1"; ACCT="$2"; ROUND="$3"; R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R" || exit 1
W="$R-$S"; log() { echo "$(date +%T) $*"; }
other() { [ "$1" = A ] && echo B || echo A; }
if [ -z "$ROUND" ]; then
  log "begin $S on $ACCT"; FLO_ACCOUNT="$ACCT" agents/scripts/begin.sh "$S" 2>&1 | tail -2
  agents/scripts/inflight.sh "$S"
else
  log "resume $S with $(basename "$ROUND")"; agents/scripts/author.sh "$S" "$ROUND" >/dev/null
fi
sleep 10; while [ -f ".scratch/$S.author.pid" ] && kill -0 "$(cat ".scratch/$S.author.pid")" 2>/dev/null; do sleep 30; done; sleep 3
tail -c 1200 ".scratch/$S.log"
grep -q "session limit\|ENOTFOUND" ".scratch/$S.log" && { log "STOP: author hit a limit or the network"; exit 2; }
python3 -c "import json,sys; b=json.load(open('$W/task.json'))['notes'].get('blocked'); print('BLOCKED:', b) if b else None; sys.exit(1 if b else 0)" || exit 4
agents/scripts/submit.sh "$S" > /dev/null 2>&1 || { log "STOP: submit failed"; tail -15 ".scratch/$S.submit.log"; exit 2; }
"$R/agents/scripts/review-wait.sh" "$S" "$(other "$ACCT")"
log "done"
