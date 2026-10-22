#!/bin/sh
# usage: agents/scripts/review.sh STORY|MILESTONE [codex|qwen|kimi|deepseek|opus] [A|B]
#   (opus: fresh session on that account, default B; a milestone id such as M2 runs a release
#   review from `flo release-packet` in a read-only checkout of the milestone head)
# Runs the reviewer on the flo review packet in the story worktree, in the background.
# Log: .scratch/STORY.REVIEWER.log ending in "exit N"; verdict: <worktree>/reviews/STORY.REVIEWER.json
set -e
. "$(dirname "$0")/env.sh"
S="$1"; A="${2:-codex}"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"; D="$R/.scratch"
case "$A" in
  codex|opus) ;;
  qwen) M=openrouter/qwen/qwen3.8-27b ;;
  kimi) M=openrouter/moonshotai/kimi-k2.6 ;;
  deepseek) M=openrouter/deepseek/deepseek-v4-pro-0813 ;;
  *) echo "review.sh: unknown reviewer $A" >&2; exit 1 ;;
esac
P="$D/$S.$A.review.prompt"; X="$D/xdg-$A"; mkdir -p "$X"
RELEASE=""
case "$S" in M[0-9]*)
  # Release review: a detached checkout of the milestone head, the light packet, static only.
  RELEASE=1; MS="${S%%-*}"; W="$R-$MS-release"   # M2 or an area pass such as M2-money
  BR="$("$R/apps/api/.venv/bin/python" -c 'import sys,yaml; print(next(m["branch"] for m in yaml.safe_load(open(sys.argv[1]))["milestones"] if m["id"]==sys.argv[2]))' "$R/agents/roadmap.yaml" "$MS")"
  git -C "$R" fetch -q origin "$BR"
  if [ -d "$W" ]; then git -C "$W" checkout -q --detach "origin/$BR"; else git -C "$R" worktree add -q --detach "$W" "origin/$BR"; fi
  mkdir -p "$W/reviews" ;;
esac
# A second review by the same reviewer gets its own previous findings and the changes since the
# commit it reviewed, not the whole story again (13 Oct: a full re-review re-planted everything).
SHA="$D/$S.$A.sha"; PRIOR="$D/$S.$A.prior.json"; ARGS=""
if [ -f "$SHA" ] && [ -f "$W/reviews/$S.$A.json" ]; then
  cp "$W/reviews/$S.$A.json" "$PRIOR"; ARGS="--since $(cat "$SHA") --prior $PRIOR"
fi
if [ -n "$RELEASE" ]; then "$R/apps/api/.venv/bin/python" "$R/agents/scripts/flo" release-packet "$S" > "$P"
else "$W/apps/api/.venv/bin/python" "$R/agents/scripts/flo" review "$S" $ARGS > "$P"; fi
git -C "$W" rev-parse HEAD > "$SHA.new"  # becomes the baseline only if the run succeeds
# what this run reviews, for the supervisor's freshness check (flo facts): a verdict counts only
# for the commit it was launched on
cp "$SHA.new" "$D/$S.$A.launched"
# Keep every round's verdict: the scorecard counts findings across rounds (flo done), and the
# file in reviews/ is overwritten by this run. An identical copy (a relaunch) is not a new round.
if [ -f "$W/reviews/$S.$A.json" ]; then
  mkdir -p "$D/reviews"; LAST="$(ls "$D/reviews/$S.$A".r*.json 2>/dev/null | sort -V | tail -1)"
  if [ -z "$LAST" ] || ! cmp -s "$LAST" "$W/reviews/$S.$A.json"; then
    N="$(ls "$D/reviews/$S.$A".r*.json 2>/dev/null | wc -l | tr -d ' ')"
    cp "$W/reviews/$S.$A.json" "$D/reviews/$S.$A.r$((N + 1)).json"
  fi
fi
printf '\nYour agent id is %s. Write reviews/%s.%s.json and stop.\n' "$A" "$S" "$A" >> "$P"
cd "$W"
# a waiter polls this log for "exit N": clear the previous run's now, not when the job starts
: > "$D/$S.$A.log"
if [ "$A" = opus ]; then
  # A fresh session on the given account (never the planner's), with the reviewer skill.
  if [ "${3:-B}" = A ]; then ENV="env -u CLAUDE_CONFIG_DIR"; else ENV="env CLAUDE_CONFIG_DIR=$HOME/.claude-summello"; fi
  printf 'ROLE xlr8flo-reviewer %s account %s. Use the xlr8flo-reviewer skill.\n\n' "$S" "${3:-B}" | cat - "$P" > "$P.role"
  nohup sh -c "mkdir -p '$D/$S.aside' && mv reviews/*.json '$D/$S.aside/' 2>/dev/null; \
    $ENV claude -p --model opus -n 'xlr8flo-reviewer $S' --permission-mode auto \"\$(cat '$P.role')\" > '$D/$S.$A.log' 2>&1; echo \"exit \$?\" >> '$D/$S.$A.log'; \
    mv -n '$D/$S.aside/'*.json reviews/ 2>/dev/null; rmdir '$D/$S.aside'" > /dev/null 2>&1 &
  echo $! > "$D/$S.$A.pid"
  echo "review launched: $S by opus (account ${3:-B})"
  exit 0
fi
if [ "$A" = codex ]; then
  # Model and effort come from agents.yaml and are pinned on the command line: an inherited
  # ~/.codex/config.toml silently ran reviews at `low` on 12 Oct.
  set -- $("$W/apps/api/.venv/bin/python" -c 'import sys,yaml; c=yaml.safe_load(open(sys.argv[1]))["agents"]["codex"]; print(c["model"], c["review_reasoning"])' "$R/agents/agents.yaml")
  T0="$(date +%Y-%m-%dT%H:%M:%S%z)"
  # Hard stop: the packet's budget is advisory to the model, this is not. Gated 15 min, else 10;
  # the reviewer keeps a draft verdict current, so a stopped run still leaves one.
  CAP=$(python3 -c 'import json,sys; print(900 if json.load(open(sys.argv[1]))["opus_final"] else 600)' "$W/task.json")
  [ -n "$ARGS" ] && CAP=480
  # calls: the packet's budget plus 10-15 (re-review 20, gated 45, ungated 40; 22 Oct: 35 stopped
  # three ungated Codex reviews before they reached the tests)
  CALLS=55
  [ -n "$ARGS" ] && CALLS=30
  # At most FLO_CODEX_PARALLEL (default 2, operator 15 Oct) Codex reviews at once, and only on
  # independent stories — the planner's call. Unbounded parallel shared the five-hour window and
  # both died at the cap on 13 Oct.
  nohup sh -c "while [ \$(pgrep -f '^codex exec' | wc -l) -ge ${FLO_CODEX_PARALLEL:-2} ]; do sleep 30; done; \
    mkdir -p '$D/$S.aside' && mv reviews/*.json '$D/$S.aside/' 2>/dev/null; \
    codex exec -m $1 -c model_reasoning_effort=$2 -c project_doc_max_bytes=0 -s workspace-write --skip-git-repo-check \"\$(cat '$P')\" > '$D/$S.$A.log' 2>&1 & CP=\$!; \
    ( sleep $CAP; kill \$CP 2>/dev/null && echo 'stopped at the time cap' >> '$D/$S.$A.log' ) & KP=\$!; \
    '$W/apps/api/.venv/bin/python' '$R/agents/scripts/flo' codex-guard '$W' $CALLS \$CP $CAP >> '$D/$S.$A.log' 2>&1 & \
    wait \$CP; RC=\$?; echo \"exit \$RC\" >> '$D/$S.$A.log'; kill \$KP 2>/dev/null; { [ \$RC = 0 ] || grep -q '\"ran_tests\": *true' 'reviews/$S.$A.json' 2>/dev/null; } && mv '$SHA.new' '$SHA'; \
    mv -n '$D/$S.aside/'*.json reviews/ 2>/dev/null; rmdir '$D/$S.aside'; \
    '$W/apps/api/.venv/bin/python' '$R/agents/scripts/flo' codex-usage '$S' '$T0' > /dev/null" > /dev/null 2>&1 &
  echo $! > "$D/$S.$A.pid"
  echo "review launched: $S by codex ($1, $2)${ARGS:+, re-review}"
  exit 0
fi
# OpenRouter is metered: refuse to start below a floor rather than die halfway without a verdict.
LEFT="$(K="$(security find-generic-password -a "$USER" -s OPENROUTER_API_KEY -w)"; curl -s --max-time 20 \
  -H "Authorization: Bearer $K" https://openrouter.ai/api/v1/credits \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(round(d["total_credits"]-d["total_usage"],2))')"
python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) >= 3 else 1)' "${LEFT:-0}" \
  || { echo "review.sh: OpenRouter balance \$${LEFT:-?} is below the \$3 floor; top up first" >&2; exit 1; }
# Caps: release 50 calls / 25 min; release area pass 60 / 20 min; story 35 / 10 min; re-review 30 / 8 min.
if [ -n "$RELEASE" ] && [ "$S" != "$MS" ]; then CALLS=60; CAP=1200; elif [ -n "$RELEASE" ]; then CALLS=50; CAP=1500; elif [ -n "$ARGS" ]; then CALLS=30; CAP=480; else CALLS=35; CAP=600; fi
# another reviewer's verdict is not evidence: move existing verdicts out of reach for the run
nohup sh -c "mkdir -p '$D/$S.aside' && mv reviews/*.json '$D/$S.aside/' 2>/dev/null; \
  XDG_DATA_HOME='$X' OPENROUTER_API_KEY=\"\$(security find-generic-password -a \"\$USER\" -s OPENROUTER_API_KEY -w)\" \
  opencode run --auto -m $M \"\$(cat '$P')\" > '$D/$S.$A.log' 2>&1 & OP=\$!; \
  '$R/apps/api/.venv/bin/python' '$R/agents/scripts/flo' log-guard '$D/$S.$A.log' $CALLS \$OP $CAP & \
  wait \$OP; echo \"exit \$?\" >> '$D/$S.$A.log'; \
  mv -n '$D/$S.aside/'*.json reviews/ 2>/dev/null; rmdir '$D/$S.aside'" > /dev/null 2>&1 &
echo $! > "$D/$S.$A.pid"
echo "OpenRouter balance \$$LEFT; caps $CALLS calls, $CAP s"
echo "review launched: $S by $A"
