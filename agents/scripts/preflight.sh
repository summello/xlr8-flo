#!/bin/sh
# Packet pre-flight (E23-S05): Codex (or an OpenRouter reviewer such as qwen) checks design/<S>.md
# against the code before an author starts, then files the record `flo start` requires.
# Usage: agents/scripts/preflight.sh <STORY_ID> [codex|qwen|...]   (default codex, operator 23 Oct 2026:
# qwen hit its 40-call cap without a verdict three times that day)
# Runs in a detached checkout of the milestone under .scratch, so a stray write cannot reach the runner.
set -eu
S="$1"; A="${2:-codex}"
R="$(cd "$(dirname "$0")/../.." && pwd)"; D="$R/.scratch"
# same invocation as agents/fleet.toml: the venv has no PyYAML after a uv sync
export XDG_CACHE_HOME="$HOME/.cache" UV_CACHE_DIR="$HOME/.cache/uv"
FLO="uv run -q --no-project --with pyyaml python $R/agents/scripts/flo"
MS="$(git -C "$R" rev-parse --abbrev-ref HEAD)"
[ -f "$R/design/$S.md" ] || { echo "preflight.sh: no design/$S.md" >&2; exit 1; }
W="$D/preflight-wt"
[ -d "$W" ] || git -C "$R" worktree add --detach "$W" "$MS" > /dev/null
git -C "$W" checkout -q --detach "$MS" && git -C "$W" reset -q --hard && git -C "$W" clean -qfd
M="$($FLO launch-model "$S" --reviewer "$A")"
P="$D/$S.preflight.prompt"; L="$D/$S.preflight.log"
cat > "$P" <<EOF
ROLE packet-preflight $S

Read design/$S.md. Check it against the code in this checkout, not against its own claims. Do not edit
any file; read only. Never open dotfiles, never write outside this directory. For each check, report
only a real mismatch with the file and line that proves it:
1. Names that exist: every router, permission, table, column, setting, ErrorCode, helper and file the packet cites.
2. Gates in the request path: middleware that would reject the new route before the handler (CSRF, MFA, idempotency).
3. Tenancy: job and outbox rows are tenant-scoped; anything global cannot be a queue row.
4. Who calls it and when: a job nobody schedules does not exist.
5. Dependencies in the production image, not only dev.
6. Every UI value the packet states numerically exists as a token in apps/web/src/styles/tokens.css.
7. Test assets the author must create because it has no network.
8. Linux CI: no macOS paths.
9. Closed taxonomies (ErrorCode) are mapped to existing values.
10. A rule the packet leaves undecided on money, authorization, a state transition or a value: that is a fail.
Also check every story in its deps is merged (git log --oneline | grep "^.\{7,\} <DEP>:" style search).

End with exactly one line: \`PREFLIGHT: pass <N>\` or \`PREFLIGHT: fail <N>\`, N = number of mismatches.
Pass when nothing found would make an author block or build the wrong thing; minor naming slips are pass.
EOF
cd "$W"
if [ "$A" = codex ]; then
  # effort pinned on the command line, as in review.sh: an inherited config.toml once ran `low`
  codex exec -m "$M" -c model_reasoning_effort=medium -c project_doc_max_bytes=0 -s read-only \
    --skip-git-repo-check "$(cat "$P")" < /dev/null > "$L" 2>&1 || true
else
  XDG_DATA_HOME="$D/xdg-preflight" OPENROUTER_API_KEY="$(security find-generic-password -a "$USER" -s OPENROUTER_API_KEY -w)" \
    opencode run --auto -m "openrouter/$M" "$(cat "$P")" > "$L" 2>&1 & OP=$!
  $FLO log-guard "$L" 40 $OP 900 &
  wait $OP || true
fi
git -C "$W" reset -q --hard
V="$(grep -Eo 'PREFLIGHT: (pass|fail) [0-9]+' "$L" | tail -1)"
[ -n "$V" ] || { echo "preflight.sh: no verdict line in $L" >&2; exit 1; }
set -- $V
cd "$R" && $FLO preflight "$S" --reviewer "$A" --verdict "$2" --findings "$3"
echo "preflight $S: $2 ($3 findings), log $L"
