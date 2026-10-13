#!/bin/sh
# usage: agents/scripts/review.sh STORY [codex|qwen|kimi|opus] [A|B]   (opus: fresh session on that account, default B)
# Runs the reviewer on the flo review packet in the story worktree, in the background.
# Log: .scratch/STORY.REVIEWER.log ending in "exit N"; verdict: <worktree>/reviews/STORY.REVIEWER.json
set -e
. "$(dirname "$0")/env.sh"
S="$1"; A="${2:-codex}"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"; D="$R/.scratch"
case "$A" in
  codex|opus) ;;
  qwen) M=openrouter/qwen/qwen3.8-27b ;;
  kimi) M=openrouter/moonshotai/kimi-k2.6 ;;
  *) echo "review.sh: unknown reviewer $A" >&2; exit 1 ;;
esac
P="$D/$S.$A.review.prompt"; X="$D/xdg-$A"; mkdir -p "$X"
"$W/apps/api/.venv/bin/python" "$R/agents/scripts/flo" review "$S" > "$P"
printf '\nYour agent id is %s. Write reviews/%s.%s.json and stop.\n' "$A" "$S" "$A" >> "$P"
cd "$W"
if [ "$A" = opus ]; then
  # A fresh session on the given account (never the planner's), with the reviewer skill.
  if [ "${3:-B}" = A ]; then ENV="env -u CLAUDE_CONFIG_DIR"; else ENV="env CLAUDE_CONFIG_DIR=$HOME/.claude-summello"; fi
  printf 'ROLE xlr8flo-reviewer %s account %s. Use the xlr8flo-reviewer skill.\n\n' "$S" "${3:-B}" | cat - "$P" > "$P.role"
  nohup sh -c "mkdir -p '$D/$S.aside' && mv reviews/*.json '$D/$S.aside/' 2>/dev/null; \
    $ENV claude -p --model opus -n 'xlr8flo-reviewer $S' --permission-mode auto \"\$(cat '$P.role')\" > '$D/$S.$A.log' 2>&1; echo \"exit \$?\" >> '$D/$S.$A.log'; \
    mv '$D/$S.aside/'*.json reviews/ 2>/dev/null; rmdir '$D/$S.aside'" > /dev/null 2>&1 &
  echo "review launched: $S by opus (account ${3:-B})"
  exit 0
fi
if [ "$A" = codex ]; then
  # Model and effort come from agents.yaml and are pinned on the command line: an inherited
  # ~/.codex/config.toml silently ran reviews at `low` on 12 Oct.
  set -- $("$W/apps/api/.venv/bin/python" -c 'import sys,yaml; c=yaml.safe_load(open(sys.argv[1]))["agents"]["codex"]; print(c["model"], c["review_reasoning"])' "$R/agents/agents.yaml")
  T0="$(date +%Y-%m-%dT%H:%M:%S%z)"
  nohup sh -c "mkdir -p '$D/$S.aside' && mv reviews/*.json '$D/$S.aside/' 2>/dev/null; \
    codex exec -m $1 -c model_reasoning_effort=$2 -s workspace-write --skip-git-repo-check \"\$(cat '$P')\" > '$D/$S.$A.log' 2>&1; echo \"exit \$?\" >> '$D/$S.$A.log'; \
    mv '$D/$S.aside/'*.json reviews/ 2>/dev/null; rmdir '$D/$S.aside'; \
    '$W/apps/api/.venv/bin/python' '$R/agents/scripts/flo' codex-usage '$S' '$T0' > /dev/null" > /dev/null 2>&1 &
  echo "review launched: $S by codex ($1, $2)"
  exit 0
fi
# another reviewer's verdict is not evidence: move existing verdicts out of reach for the run
nohup sh -c "mkdir -p '$D/$S.aside' && mv reviews/*.json '$D/$S.aside/' 2>/dev/null; \
  XDG_DATA_HOME='$X' OPENROUTER_API_KEY=\"\$(security find-generic-password -a \"\$USER\" -s OPENROUTER_API_KEY -w)\" \
  opencode run --auto -m $M \"\$(cat '$P')\" > '$D/$S.$A.log' 2>&1; echo \"exit \$?\" >> '$D/$S.$A.log'; \
  mv '$D/$S.aside/'*.json reviews/ 2>/dev/null; rmdir '$D/$S.aside'" > /dev/null 2>&1 &
echo "review launched: $S by $A"
