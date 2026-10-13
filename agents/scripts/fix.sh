#!/bin/sh
# usage: agents/scripts/fix.sh STORY   (after ci-watch wrote .scratch/STORY.ci-red)
# Resume the story's author session with the failing CI log. The session is keyed to the
# worktree path and its account, so the worktree is recreated at the same path if it is gone.
set -e
. "$(dirname "$0")/env.sh"
S="$1"; R="$(cd "$(dirname "$0")/../.." && pwd)"; W="$R-$S"; D="$R/.scratch"; RED="$D/$S.ci-red"
[ -f "$RED" ] || { echo "no $RED — nothing to fix"; exit 1; }
# flo reap only removes a worktree once its CI is green, so a red story still has one.
[ -d "$W" ] || { echo "$W is gone, and with it the session record; fix as a new session" >&2; exit 1; }
ST="$(grep -A12 "id: $S\$" "$R/agents/roadmap.yaml" | grep -m1 'st:' | awk '{print $2}')"
F="$D/$S.fix.prompt"
{
  echo "Story CI is red. The failing jobs' log tail is below; the first line is branch, commit and run id."
  [ "$ST" = done ] && echo "The story is ALREADY MERGED into the milestone branch. Fix on this branch anyway; the planner moves your fix commits onto the milestone. Name the fix commits in your report."
  echo "Reproduce the failure locally first, fix the cause, rerun that test file and flo check --light, commit with \"$S: fix CI ...\"."
  echo
  cat "$RED"
} > "$F"
exec "$R/agents/scripts/author.sh" "$S" "$F"
