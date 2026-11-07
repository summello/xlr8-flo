#!/bin/sh
# usage: agents/scripts/review-wait.sh STORY [opus-account]   (manual helper for story-run and review-run)
# Run the Codex review and, for a gated story, a fresh Opus review; wait for each and print the
# verdicts. Codex runs first: one Codex review at a time across lanes (13 Oct).
. "$(dirname "$0")/env.sh"
S="$1"; OA="${2:-B}"; R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R" || exit 1; W="$R-$S"
log() { echo "$(date +%T) $*"; }
waitpid() { sleep 10; while [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; do sleep 15; done; sleep 3; }
show() { python3 -c "import json,sys;r=json.load(open(sys.argv[1]));print(sys.argv[2],r.get('verdict'),r.get('ran_tests'));[print(' -',x.get('severity'),x.get('file'),x.get('line'),'|',x.get('problem','')[:300]) for x in r.get('findings',[])]" "$W/reviews/$S.$1.json" "$(echo "$1" | tr a-z A-Z)"; }
log "codex review"; agents/scripts/review.sh "$S" codex >/dev/null; waitpid ".scratch/$S.codex.pid"; show codex
if python3 -c "import json,sys; sys.exit(0 if json.load(open('$W/task.json'))['opus_final'] else 1)"; then
  log "opus review on $OA"; agents/scripts/review.sh "$S" opus "$OA" >/dev/null; waitpid ".scratch/$S.opus.pid"; show opus
fi
