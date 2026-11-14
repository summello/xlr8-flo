#!/bin/sh
# usage: agents/scripts/ship.sh STORY   (manual; the fleet supervisor does this itself)
# Resubmit (milestone merge + light check + push), wait for story CI on the pushed commit, finish
# when green. For approved stories whose CI went red on a milestone fault.
. "$(dirname "$0")/env.sh"
flo_manual_guard
S="$1"; R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R" || exit 1; W="$R-$S"
log() { echo "$(date +%T) $*"; }
agents/scripts/submit.sh "$S" > ".scratch/$S.submit.log" 2>&1 || { log "STOP: submit failed"; tail -15 ".scratch/$S.submit.log"; exit 2; }
SHA=$(git -C "$W" rev-parse HEAD); log "submitted $SHA; waiting for CI"
sleep 60
while :; do
  st=$(gh run list --commit "$SHA" --workflow CI --json status,conclusion -q '.[0] | "\(.status) \(.conclusion)"')
  case "$st" in "completed success") break;; completed*) log "STOP: CI $st"; exit 3;; esac
  sleep 120
done
log "CI green; finishing"; agents/scripts/finish.sh "$S"; log "finish exit $?"
