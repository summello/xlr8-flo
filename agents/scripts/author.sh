#!/bin/sh
# usage: agents/scripts/author.sh STORY [extra-instructions-file]
# Launches the story's assigned author (task.json "author") in its worktree, in the background.
# Log: .scratch/STORY.log, ending in "exit N". Run from the parent repo.
set -e
. "$(dirname "$0")/env.sh"
S="$1"; EXTRA="${2:-}"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"; D="$R/.scratch"
# the launch cds into the worktree, so a relative findings path would be lost
[ -n "$EXTRA" ] && EXTRA="$(cd "$(dirname "$EXTRA")" && pwd)/$(basename "$EXTRA")"
mkdir -p "$D"
# one launch or reassign per story at a time (E23-S06, E23-S15): flo reassign takes the same flock
# (launch.STORY), so it cannot move the story between this read of task.json and the pid file below.
# The kernel frees it if this process is killed. Bounded wait, then exit 75.
if [ -z "${FLO_LAUNCH_LOCKED:-}" ]; then
  exec python3 "$R/agents/scripts/flo" with-lock "launch.$S" --wait 30 -- env FLO_LAUNCH_LOCKED=1 sh "$R/agents/scripts/author.sh" "$@"
fi
# one live author per story: a live pid means a running session, and a second would write the same branch
PID=; [ -f "$D/$S.author.pid" ] && { read -r PID _ < "$D/$S.author.pid" || true; }
case "$PID" in ''|*[!0-9]*|0) ;; *) kill -0 "$PID" 2>/dev/null && { echo "author of $S already running (pid $PID)" >&2; exit 75; } ;; esac
A="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["author"])' "$W/task.json")"
P="$D/$S.prompt"
cat > "$P" <<EOP
You are the AUTHOR for story $S. Your working directory is this story worktree; stay inside it and never touch the parent repo or another worktree.
Read AGENTS.md, agents/project-memory.md, docs/m1-conventions.md, docs/m2-conventions.md, docs/author-traps.md and design/$S.md, then implement the packet exactly.
Write it the ponytail way (use the ponytail skill if you have it): trace the real flow first, then take the laziest solution that is correct. In order: does it need to exist, is it already in kernel/ or a module service, does the stdlib do it, does Postgres or the browser do it, does an installed dependency do it, can it be one line. No interface with one implementation, no config for a value that never changes, no scaffolding for later, the shortest diff in the right place. A deliberate shortcut with a ceiling gets a "ponytail:" comment naming the ceiling and the upgrade path (AGENTS.md 3.6). Never simplify away validation at a trust boundary, authorization, money rules, accessibility or a test.
Environment: dependencies are installed (apps/api/.venv, apps/web/node_modules); there is no network. A real Postgres 17 runs at 127.0.0.1:5432. NEVER use the shared flo_test database: get your own with
  export TEST_DATABASE_URL="\$(apps/api/.venv/bin/python agents/scripts/flo db reset)"
From apps/api: uv run --offline pytest -q, uv run --offline ruff check ., uv run --offline mypy --strict src/flo. From apps/web: npm run lint, npm run typecheck, npm run test. Regenerate the OpenAPI client when you add or change an endpoint (PATH must include apps/api/.venv/bin; npm run generate:api in apps/web) and commit the generated files.
UI authority, in order: design-system/MASTER.md and tokens.css, then design-system/pages/<page>.md, then design-system/canvas or design-system/canvas-2 which are drawings for layout, copy and interaction ONLY. Never copy a value from the canvas; record a stale board in notes.followup.
Rules: Decimal only for money; lock before validate; org_id only from the session; foreign-tenant ids return 404; every state-changing POST honours Idempotency-Key; every new endpoint gets a real named test in apps/api/tests/isolation/test_route_coverage.py (COVERED); no new dependency; never print or write a secret; keep to the files the packet names. Every gate, contract or guard you add needs a test that plants a real violation and proves it fails.
Blocking: follow docs/author-traps.md "Blocking". Block only on money, state transitions, authorization or an open requirement, and leave partial work uncommitted when you do. Everything else: conservative choice, notes.followup, keep going.
Done means: apps/api/.venv/bin/python agents/scripts/flo check --light is green from the worktree root (lint, types, boundaries, web checks, secrets, and the tests your diff touches; the full suite runs on GitHub after submit), and every guard you added was planted and seen to fail. Fill task.json traceability (requirement id -> test name) and notes.recap (one or two plain sentences).
Commit in this worktree with messages starting "$S:". Do NOT run flo submit, flo done, flo gate, push, or merge. Finish with a short report: files changed, test counts, plants and their results, followups.
EOP
[ -n "$EXTRA" ] && cat "$EXTRA" >> "$P"
cd "$W"
# a waiter polls this log for "exit N": clear the previous run's now, not when the job starts
: > "$D/$S.log"
case "$A" in
  sonnet)
    # One session per story with a pre-assigned id on the least-loaded Claude account; a second
    # launch (continuation, review round, CI fix) resumes that same session (flo account).
    set -- $("$W/apps/api/.venv/bin/python" "$R/agents/scripts/flo" account "$S")
    MODE="$1"; CFG="$2"; SID="$3"; ACCT="$4"
    # model and effort come from agents.yaml and are recorded in task.json by the same calls (E23-S12)
    M="$("$W/apps/api/.venv/bin/python" "$R/agents/scripts/flo" launch-model "$S")"
    E="$("$W/apps/api/.venv/bin/python" "$R/agents/scripts/flo" launch-model "$S" --effort)"
    # A resumed session already holds the packet and its own work: send only the new input
    # (review findings, a CI log, a continuation note), never the whole brief again.
    { printf 'ROLE xlr8flo-worker %s account %s. Use the xlr8flo-worker and ponytail skills.\n\n' "$S" "$ACCT"
      if [ "$MODE" = new ]; then cat "$P"; elif [ -n "$EXTRA" ]; then cat "$EXTRA"
      else echo "Continue the story from where you stopped; the tree is as you left it."; fi; } > "$P.role"
    if [ "$MODE" = new ]; then
      RUN="claude -p --model $M --effort $E --session-id $SID -n 'xlr8flo-worker $S' --permission-mode auto"
    else
      RUN="claude -p --model $M --effort $E --resume $SID --permission-mode auto"
    fi
    # "-" is the default login: unset the variable rather than point it at ~/.claude.
    if [ "$CFG" = - ]; then ENV="env -u CLAUDE_CONFIG_DIR"; else ENV="env CLAUDE_CONFIG_DIR='$CFG'"; fi
    nohup sh -c "$ENV $RUN \"\$(cat '$P.role')\" > '$D/$S.log' 2>&1; echo \"exit \$?\" >> '$D/$S.log'" > /dev/null 2>&1 &
    echo "session $SID on account $ACCT ($MODE)"
    ;;
  codex)
    nohup sh -c "codex exec -s workspace-write --skip-git-repo-check \"\$(cat '$P')\" > '$D/$S.log' 2>&1; echo \"exit \$?\" >> '$D/$S.log'" > /dev/null 2>&1 &
    ;;
  opencode-nemotron)
    # isolated auth store; the key is read at the point of use and never exported to this shell
    X="$D/xdg-$A"; mkdir -p "$X"
    # the model comes from agents.yaml and is recorded in task.json by the same call (E23-S03);
    # set -e stops the launch if the config has no model for this agent
    M="$("$W/apps/api/.venv/bin/python" "$R/agents/scripts/flo" launch-model "$S")"
    nohup sh -c "XDG_DATA_HOME='$X' OPENROUTER_API_KEY=\"\$(security find-generic-password -a \"\$USER\" -s OPENROUTER_API_KEY -w)\" opencode run --auto -m openrouter/$M \"\$(cat '$P')\" > '$D/$S.log' 2>&1; echo \"exit \$?\" >> '$D/$S.log'" > /dev/null 2>&1 &
    ;;
  *) echo "author.sh: no launcher for author '$A'" >&2; exit 1 ;;
esac
# the fleet supervisor (flo facts) and the manual runners tell a running author from a finished one by this
echo $! > "$D/$S.author.pid"
echo "launched $S ($A)"
