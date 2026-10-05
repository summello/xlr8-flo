# M1 design decisions to confirm

Written by Opus during the M1 design pass (3 Oct 2026) while the operator was away. Each is a
business or design rule the plan left open; each was decided conservatively and is recorded in
the story packet named. Reverse any of them by editing the packet **before** the story starts, or
by opening a follow-up story after it merges.

| ID | Decision | Packet |
|---|---|---|
| D-M1-1 | Settings precedence: nearest scope wins (BU/OU, then parents, then org, then default); values replace, never merge | E05-S01 |
| D-M1-3 | `allow_negative_budget` defaults to **false** | E07-S02 |
| D-M1-5 | Master data is one generic table keyed by `kind`, not eight tables | E05-S03 |
| D-M1-6 | Fiscal years named by ending calendar year, 12 monthly periods, no 4-4-5 | E05-S04 |
| D-M1-7 | Periods are `open` or `closed`; reopening needs its own permission, step-up and a reason | E05-S04 |
| D-M1-8 | FX: ECB daily rates, a weekend lookup may use the latest prior published date within 7 days (shown to the user), older blocks; no fabrication | E05-S05 |
| D-M1-9 | Every ledger row names its balance bucket; balances are `SUM(amount) GROUP BY bucket` | E07-S01 |
| D-M1-10 | No ledger partitioning in M1 (a partitioned table cannot enforce cross-partition idempotency uniqueness) | E07-S01 |
| D-M1-11 | Allocation allowed on `draft` and `active` projects only | E07-S03 |
| D-M1-12 | Releases ignore period closure so a cancellation can always free funds | E07-S04 |
| D-M1-13 | Cross-hierarchy transfers: roll-down posts hop pairs through every ancestor; roll-up posts leaf legs only (parents aggregate at read time) | E07-S08 |
| D-M1-14 | Document numbers allocated in a separate committed transaction, so a rollback leaves a gap and never a reuse | E06-S01 |
| D-M1-15 | Project re-parenting is not supported in M1 | E06-S01 |
| D-M1-16 | Project state machine and closing rule (table in the packet); `approval_pending` to `active` by `project.approve` until the M2 engine | E06-S03 |
| D-M1-17 | Funding modes: roll-down funds a child by transfer from its parent; roll-up aggregates descendants at read time; one mode per tree, locked once money exists | E06-S04 |
| D-M1-18 | Risk indicator thresholds (budget 80/95 percent, schedule overdue/14 days, recorded risk score 8/15) | E06-S08 |
| D-M1-20 | BU/OU authorization: every org unit registers as a `bu`-type authorization scope under the org; nesting drives settings precedence only | E05-S01 |
| D-M1-19 | Import: CSV and XLSX only, any formula or macro rejects the file, standard-library XLSX parsing, no new dependency | E08-S01 |

Not decided (kept out of scope on purpose): derived percent complete on projects, planned items
per phase, cross-currency posting, approval of manual adjustments (M2).

## Open topic: commercial terms (explore separately, requested by the operator)

E05-S03 stores `tax_code.rate`, `payment_term.net_days` and the optional early-payment discount as
validated reference data **and applies none of it**. How these are *used* is undecided and is its
own design topic before any story computes with them:

- **Tax:** inclusive versus exclusive pricing, per-line versus per-document rounding, compound
  taxes, tax on freight, recoverable versus non-recoverable tax and how each posts to the ledger,
  effective-dated rate changes on open documents.
- **Payment terms:** how `net_days` and the discount window derive a due date and a discount
  deadline, which date they count from (invoice, receipt, PO), and how a discount taken posts.
- **Freight and other charges:** whether freight is a line, a header charge or an allocation across
  lines, how it is apportioned to projects and ledger accounts, and its tax treatment.

Touches M3 (RFQ, PO) and M4 (reporting) most; none of it blocks M1.
