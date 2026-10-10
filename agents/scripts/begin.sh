#!/bin/sh
# usage: agents/scripts/begin.sh STORY   (run from the parent repo on the milestone branch)
# flo start, install the worktree's dependencies (authors have no network), launch the author.
set -e
S="$1"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"
cd "$R"
uv run --no-project --with pyyaml python agents/scripts/flo start "$S" > /dev/null
( cd "$W/apps/api" && uv sync --frozen --extra dev -q && uv pip install --python .venv/bin/python pyyaml -q )
( cd "$W/apps/web" && npm ci --silent )
test -z "$(git -C "$W" status --porcelain)"
"$R/agents/scripts/author.sh" "$S"
