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

| Gate | Deliberate break | Expected failure signal | Pull-request evidence |
|---|---|---|---|
| Ruff / strict mypy | Add an untyped Python function | `mypy --strict` reports a missing function annotation | Add failed/restored run links |
| import-linter | Make `flo.modules.a` import `flo.modules.b` | `Business modules are independent BROKEN` | Add failed/restored run links |
| Float ban | Add `def f(x: float)` under `modules/budget` | `float is banned in money modules` | Add failed/restored run links |
| Hardcoded colour | Add `color: #ff0000` in a `.tsx` file | `raw colour outside tokens.css` | Add failed/restored run links |
| Roadmap drift | Edit generated section 5 in `docs/claude-plan.md` | `flo roadmap --check` reports drift | Add failed/restored run links |
| Scorecard drift | Edit `agents/SCORECARD.md` | `flo score --check` reports drift | Add failed/restored run links |
| Story-id commit | Use a commit subject without a story id or maintenance type | Governance reports an untraceable commit | Add failed/restored run links |
| Gitleaks | Commit a synthetic Anthropic-key-shaped value | Gitleaks reports a leak | Add failed/restored run links |

## Branch protection

Once the application source exists, the `Protect main` ruleset must require `backend`,
`frontend`, `analyze (python)`, and `analyze (javascript-typescript)` in addition to the
existing required checks. The CodeQL `code_scanning` rule uses the `CodeQL` tool and
blocks security alerts at `high_or_higher`; it is enabled only after both language
analyses have completed successfully on `main`. `deploy` is never a required pull-request
check because it runs only on pushes to `main`.
