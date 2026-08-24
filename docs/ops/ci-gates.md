# CI gates

The local `flo check` command mirrors the repository's lint, type, boundary,
test, accessibility, and secret-scanning gates. Run it before submitting a
story so failures are fixed before reviewer capacity is used.

| Gate | Local check |
|---|---|
| Python lint | `ruff check apps/api` |
| Python types | `mypy --strict apps/api/src/flo` |
| Module boundaries | `lint-imports` |
| Backend tests and coverage | `pytest -q apps/api --cov=flo --cov-report=term-missing` |
| Frontend lint | `npm --prefix apps/web run lint` |
| Frontend types | `npm --prefix apps/web run typecheck` |
| Frontend unit tests | `npm --prefix apps/web run test` |
| End-to-end and accessibility | `npm --prefix apps/web run test:e2e` |
| Secret scanning | `gitleaks detect --no-banner` |

## Identity hashing configuration

`FLO_IDENTITY_ARGON2_MEMORY_COST_KIB` is expressed in KiB. The deployed Cloud
Run service has a 512 MiB per-instance memory ceiling; this knob accepts 8 MiB
through 256 MiB (8,192 through 262,144 KiB), leaving memory for the application
process. The deployment must also bound concurrent password hashing against that
same instance ceiling before login is exposed publicly.

## Secret scanning

Gitleaks scans the repository with its built-in rules. The root
`.gitleaks.toml` explicitly extends those defaults and has one path-scoped
allowlist for
`apps/api/src/flo/kernel/identity/data/blocklist.txt`.

That file is a bundled k-anonymity password blocklist containing SHA-1 suffixes.
Two suffixes begin with `EAAA`, the Square access-token prefix, and therefore
match the default `square-access-token` heuristic even though they are hashes
of compromised passwords rather than credentials. The allowlist is anchored to
that exact data-file path. It does not disable the Square rule, change the rule's
pattern, or allowlist any other repository path.
