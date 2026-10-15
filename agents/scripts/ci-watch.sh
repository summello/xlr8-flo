#!/bin/sh
# usage: agents/scripts/ci-watch.sh   (run from the parent repo, in the background, once)
# Polls GitHub every 120 s. Red story CI -> .scratch/STORY.ci-red (then agents/scripts/fix.sh STORY);
# red milestone CI -> .scratch/MILESTONE.ci-red (stop the line); green merged story -> flo reap.
# A shell loop, not an agent: polling needs no model tokens. Log: .scratch/ci-watch.log
. "$(dirname "$0")/env.sh"
R="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$R/.scratch"
while :; do
  uv run --no-project --with pyyaml python "$R/agents/scripts/flo" ci-watch >> "$R/.scratch/ci-watch.log" 2>&1
  sleep 120
done
