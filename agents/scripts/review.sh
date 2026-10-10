#!/bin/sh
# usage: agents/scripts/review.sh STORY [qwen|kimi]   (ungated stories; Opus reviews gated ones itself)
# Runs the reviewer on the flo review packet in the story worktree, in the background.
# Log: .scratch/STORY.REVIEWER.log ending in "exit N"; verdict: <worktree>/reviews/STORY.REVIEWER.json
set -e
S="$1"; A="${2:-qwen}"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"; D="$R/.scratch"
case "$A" in
  qwen) M=openrouter/qwen/qwen3.8-27b ;;
  kimi) M=openrouter/moonshotai/kimi-k2.6 ;;
  *) echo "review.sh: unknown reviewer $A" >&2; exit 1 ;;
esac
P="$D/$S.$A.review.prompt"; X="$D/xdg-$A"; mkdir -p "$X"
"$W/apps/api/.venv/bin/python" "$R/agents/scripts/flo" review "$S" > "$P"
printf '\nYour agent id is %s. Write reviews/%s.%s.json and stop.\n' "$A" "$S" "$A" >> "$P"
cd "$W"
# another reviewer's verdict is not evidence: move existing verdicts out of reach for the run
nohup sh -c "mkdir -p '$D/$S.aside' && mv reviews/*.json '$D/$S.aside/' 2>/dev/null; \
  XDG_DATA_HOME='$X' OPENROUTER_API_KEY=\"\$(security find-generic-password -a \"\$USER\" -s OPENROUTER_API_KEY -w)\" \
  opencode run --auto -m $M \"\$(cat '$P')\" > '$D/$S.$A.log' 2>&1; echo \"exit \$?\" >> '$D/$S.$A.log'; \
  mv '$D/$S.aside/'*.json reviews/ 2>/dev/null; rmdir '$D/$S.aside'" > /dev/null 2>&1 &
echo "review launched: $S by $A"
