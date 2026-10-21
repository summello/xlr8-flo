#!/bin/sh
# usage: agents/scripts/review-run.sh STORY [opus-account]   (manual; the fleet supervisor does this itself)
# Resubmit without the author (milestone merge + light check + push), then the Codex review and,
# for gated stories, a fresh Opus review. For stories whose code is done but whose submit or CI
# broke on a milestone change.
. "$(dirname "$0")/env.sh"
flo_manual_guard
S="$1"; R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R" || exit 1
log() { echo "$(date +%T) $*"; }
agents/scripts/submit.sh "$S" > ".scratch/$S.submit.log" 2>&1 || { log "STOP: submit failed"; tail -15 ".scratch/$S.submit.log"; exit 2; }
agents/scripts/review-wait.sh "$S" "${2:-B}"
log "done"
