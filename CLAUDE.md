# CLAUDE.md

Claude Code entry point. The rules live in files shared with Codex CLI and OpenCode CLI so all four agents follow one rulebook.

@AGENTS.md
@agents/claude.md
@agents/project-memory.md

Architecture, requirements and roadmap: `docs/claude-plan.md`.
Requirements baseline: `docs/requirements.md`.

Omnissiah project: role holders live in `.flo/roles.json` (swap with `flo-act swap`), the run contract in `WORKFLOW.md`,
session memory via `flo-mem` (index injected at session start; pull detail with `flo-mem search|get`). Rules stay in AGENTS.md.
