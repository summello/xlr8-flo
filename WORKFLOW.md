---
# Omnissiah WORKFLOW.md — edited by the operator; hot-reloaded by flo-fleet (last good config kept on error).
project: xlr8flo
rulebook: AGENTS.md              # the one file every role reads
check: "env XDG_CACHE_HOME=$HOME/.cache UV_CACHE_DIR=$HOME/.cache/uv uv run -q --no-project --with pyyaml python agents/scripts/flo check --light"       # must be green before submit
design_system: design-system/    # authority for UI values (flo-designer)
packets: design/                 # one <STORY>.md per story
protected_branches: [main]       # the fleet never merges here
polling: { interval_s: 300 }
agent:
  max_in_flight: 4
  max_rounds: 3
  max_workers_per_account: 2
  stall_timeout_s: 300
  retry_backoff_s: [60, 300, 900]
limits:                          # percent left (5-hour, week)
  claude_start: [25, 0]    # weekly ignored (operator, 28 Oct 2026): the operator overrides by hand if A and B drift
  claude_continue: [10, 0]
  codex: [20, 10]
budget:
  milestone_reviewer_usd_month: 20
  monitor_paid_fallback_usd_day: 0.5
memory:
  index_tokens: 600              # SessionStart injection cap per session
hooks: { after_create: "", before_run: "", after_run: "", before_remove: "" }
---
Roles and who holds them: `.flo/roles.json` (swap with `flo-act swap` or the Office).
Prompt template: each role reads its skill (flo-planner, flo-worker, flo-designer, flo-reviewer); this body is project-specific addenda only.

## Project addenda
- (add project-specific rules here; keep it short)
