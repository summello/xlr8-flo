# reviews/

One file per reviewer per story: `reviews/<STORY_ID>.<agent-id>.json`, written inside the story's worktree.

Schema and rules: `AGENTS.md` §2.2. `flo gate` counts only reviews with `ran_tests: true`, requires two of them from assigned reviewers, and blocks on any `severity: blocker` finding.
