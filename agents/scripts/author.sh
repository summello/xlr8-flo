#!/bin/sh
# usage: agents/scripts/author.sh STORY [extra-instructions-file]
# Launches the story's assigned author (task.json "author") in its worktree, in the background.
# Log: .scratch/STORY.log, ending in "exit N". Run from the parent repo.
set -e
S="$1"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"; D="$R/.scratch"
mkdir -p "$D"
A="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["author"])' "$W/task.json")"
P="$D/$S.prompt"
cat > "$P" <<EOP
You are the AUTHOR for story $S. Your working directory is this story worktree; stay inside it and never touch the parent repo or another worktree.
Read AGENTS.md, agents/project-memory.md, docs/m1-conventions.md, docs/author-traps.md and design/$S.md, then implement the packet exactly.
Environment: dependencies are installed (apps/api/.venv, apps/web/node_modules); there is no network. A real Postgres 17 runs at 127.0.0.1:5432. NEVER use the shared flo_test database: get your own with
  export TEST_DATABASE_URL="\$(apps/api/.venv/bin/python agents/scripts/flo db reset)"
From apps/api: uv run --offline pytest -q, uv run --offline ruff check ., uv run --offline mypy --strict src/flo. From apps/web: npm run lint, npm run typecheck, npm run test. Regenerate the OpenAPI client when you add or change an endpoint (PATH must include apps/api/.venv/bin; npm run generate:api in apps/web) and commit the generated files.
UI authority, in order: design-system/MASTER.md and tokens.css, then design-system/pages/<page>.md, then design-system/canvas which is a drawing for layout, copy and interaction ONLY. Never copy a value from the canvas; record a stale board in notes.followup.
Rules: Decimal only for money; lock before validate; org_id only from the session; foreign-tenant ids return 404; every state-changing POST honours Idempotency-Key; every new endpoint gets a real named test in apps/api/tests/isolation/test_route_coverage.py (COVERED); no new dependency; never print or write a secret; keep to the files the packet names. Every gate, contract or guard you add needs a test that plants a real violation and proves it fails.
Blocking: follow docs/author-traps.md "Blocking". Block only on money, state transitions, authorization or an open requirement, and leave partial work uncommitted when you do. Everything else: conservative choice, notes.followup, keep going.
Done means: apps/api/.venv/bin/python agents/scripts/flo check is green from the worktree root (it builds its own fresh database), and every guard you added was planted and seen to fail. Fill task.json traceability (requirement id -> test name) and notes.recap (one or two plain sentences).
Commit in this worktree with messages starting "$S:". Do NOT run flo submit, flo done, flo gate, push, or merge. Finish with a short report: files changed, test counts, plants and their results, followups.
EOP
[ -n "$2" ] && cat "$2" >> "$P"
cd "$W"
case "$A" in
  codex)
    nohup sh -c "codex exec -s workspace-write --skip-git-repo-check \"\$(cat '$P')\" > '$D/$S.log' 2>&1; echo \"exit \$?\" >> '$D/$S.log'" > /dev/null 2>&1 &
    ;;
  opencode-nemotron)
    # isolated auth store; the key is read at the point of use and never exported to this shell
    X="$D/xdg-$A"; mkdir -p "$X"
    nohup sh -c "XDG_DATA_HOME='$X' OPENROUTER_API_KEY=\"\$(security find-generic-password -a \"\$USER\" -s OPENROUTER_API_KEY -w)\" opencode run --auto -m openrouter/nvidia/nemotron-3-ultra-550b-a55b:free \"\$(cat '$P')\" > '$D/$S.log' 2>&1; echo \"exit \$?\" >> '$D/$S.log'" > /dev/null 2>&1 &
    ;;
  *) echo "author.sh: no launcher for author '$A'" >&2; exit 1 ;;
esac
echo "launched $S ($A)"
