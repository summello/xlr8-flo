# CI gates

The CI workflow is the release-control entry point for SEC-012. Superseded pull-request
runs are cancelled, Python and npm downloads are cached, and the backend and frontend
jobs are enabled through outputs from the post-checkout `detect` job. Do not replace
those outputs with `hashFiles()` in a job-level condition: job conditions are evaluated
before checkout.

## Release controls

| Job | Control | Result |
|---|---|---|
| `governance` | Roadmap, scorecard, story commit, and roadmap consistency gates | Blocks drift or untraceable changes |
| `backend` | Ruff, strict mypy, import-linter, money-module float ban, pytest and isolation suite | Blocks backend quality and boundary regressions |
| `frontend` | ESLint, TypeScript, generated-client drift, token colour gate, Vitest, Playwright and axe | Blocks frontend quality and accessibility regressions |
| `security` | Gitleaks and Trivy at `CRITICAL,HIGH` with `exit-code: 1` | Blocks committed secrets and high-severity dependency findings |
| `backend` / `frontend` | Syft CycloneDX JSON generation | Attaches `sbom-python.cyclonedx.json` and `sbom-node.cyclonedx.json` to every successful run |
| `security` | Trivy SARIF upload | Attaches `trivy-dependency-scan` and publishes the same findings to code scanning |

Artifacts are retained for 14 days to provide release evidence without consuming
unbounded artifact storage.

## Deliberate-failure evidence

Before submission, each gate is checked from a deliberately broken local commit and
then restored. The final pull request must link the corresponding failed and restored
GitHub Actions runs in its body; local evidence cannot prove GitHub artifact attachment,
Security-tab ingestion, or hosted-run duration.

| Gate | Deliberate break | Broken / restored local commits | Observed failure | Pull-request evidence |
|---|---|---|---|---|
| Ruff / strict mypy | Add an untyped Python function | `8c18159` / `ae096a7` | `mypy_exit=1`, `[no-untyped-def]` | Add failed/restored run links |
| import-linter | Make `flo.modules.a` import `flo.modules.b` | `b514d35` / `d18a85f` | `Business modules are independent BROKEN` | Add failed/restored run links |
| Float ban | Add `def f(x: float)` under `modules/budget` | `911de76` / `89708f3` | `float is banned in money modules` | Add failed/restored run links |
| Hardcoded colour | Add `color: #ff0000` in a `.tsx` file | `d04ef5c` / `609c881` | `raw colour outside tokens.css` | Add failed/restored run links |
| Roadmap drift | Edit generated section 5 in `docs/claude-plan.md` | `7e2d2d9` / `8b65468` | `docs/claude-plan.md §5 is stale` | Add failed/restored run links |
| Scorecard drift | Edit `agents/SCORECARD.md` | `ada88b2` / `fe045f4` | `agents/SCORECARD.md is stale` | Add failed/restored run links |
| Story-id commit | Use a commit subject without a story id or maintenance type | `02760d8` / `d854ee6` (amended) | `has neither a story id nor a maintenance type` | Add failed/restored run links |
| Gitleaks | Commit a synthetic Anthropic-key-shaped value | `dd3815c` / `1209067` (amended) | `RuleID: anthropic-api-key`, one leak | Add failed/restored run links |

The story-id and Gitleaks evidence commits were amended instead of retained: keeping the
bad subject would make governance permanently red, and keeping a reverted secret-shaped
value in reachable Git history would correctly keep Gitleaks red.

## Branch protection

Once the application source exists, the `Protect main` ruleset must require `backend`,
`frontend`, `analyze (python)`, and `analyze (javascript-typescript)` in addition to the
existing required checks. The CodeQL `code_scanning` rule uses the `CodeQL` tool and
blocks security alerts at `high_or_higher`; it is enabled only after both language
analyses have completed successfully on `main`. `deploy` is never a required pull-request
check because it runs only on pushes to `main`.

The follow-up was applied on 2026-08-24 after successful CodeQL analysis for both
languages in [run 32683930099](https://github.com/summello/xlr8-flo/actions/runs/32683930099).
The active [Protect main ruleset](https://github.com/summello/xlr8-flo/rules/21258071)
requires all seven checks listed above and the CodeQL high-or-higher scanning threshold.
