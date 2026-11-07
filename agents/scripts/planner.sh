#!/bin/sh
# usage: agents/scripts/planner.sh A|B NEEDS.json
# The fleet supervisor's auto-planner (operator, 24 Oct 2026): a one-shot Opus session, skill
# xlr8flo-planner, sent to the needs the fleet could not clear itself. It runs in the foreground
# under the `planner` lock, so a second planner (another tick, a manual one) waits or gives up
# instead of racing it on flo assign, the roadmap and the finish lock. Log: .scratch/planner.log
set -e
. "$(dirname "$0")/env.sh"
A="$1"; N="$2"; R="$(cd "$(dirname "$0")/../.." && pwd)"; D="$R/.scratch"; P="$D/planner.prompt"
[ -s "$N" ] || { echo "planner.sh: no needs file $N" >&2; exit 2; }
if [ "$A" = A ]; then ENV="env -u CLAUDE_CONFIG_DIR"; else ENV="env CLAUDE_CONFIG_DIR=$HOME/.claude-summello"; fi
{
  printf 'ROLE xlr8flo-planner auto account %s. Use the xlr8flo-planner skill.\n\n' "$A"
  cat <<'EOF'
The fleet supervisor sent you because these needs stayed open for two ticks (JSON below). Be miserly:
- Per need, read only the log or file it names, fix the smallest thing, and step back. The fleet
  drives every story step itself; do not run author, review, submit or finish steps by hand.
- Push only under `flo with-lock finish`; never merge to main, never force-push, no deploy.
- Never answer a money, authorization or state-transition question, and never pick a model. Write
  the question into the packet's "## Blocked" section or the story's notes.blocked and leave it.
- Spawn no agents. The only sessions you may start are packet pre-flights, one at a time:
  `agents/scripts/preflight.sh <S>` (Codex). A failed pre-flight: revise the packet from its findings,
  push, and re-run it once.
- Append one line per action to .scratch/opus-planner.log. End with one line per need:
  `fixed: ...`, `operator: ...` (what the operator must decide) or `left: ...` (why).

Needs:
EOF
  cat "$N"
} > "$P"
cd "$R"
exec python3 agents/scripts/flo with-lock planner -- $ENV claude -p --model opus -n "xlr8flo-planner auto" \
  --permission-mode auto "$(cat "$P")"
