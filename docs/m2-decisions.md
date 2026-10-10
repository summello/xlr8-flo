# M2 decisions

Decisions the M2 packets build on. **Operator decisions** were answered by the operator on
10 Oct 2026 and close the matching open decision in `docs/requirements.md` §14. **Defaults to
confirm** were filled in by Opus where an answer left a value open; each is conservative and
named in the packet that uses it. Reverse one by editing the packet before its story starts.

## Operator decisions (10 Oct 2026)

| ID | Decision | Closes | Packets |
|---|---|---|---|
| D-M2-1 | **Rejection: the approver chooses on every reject**, between *return to Draft* (the requester may amend and resubmit; the reservation, if any, is released) and *reject permanently* (terminal `rejected`). No workflow-level policy restricts the choice. | §14 #9 | E10-S06, E09-S05, E09-S07 |
| D-M2-2 | **Approval-group membership is live**: who may decide is evaluated when the decision is made, not when the task was created. | §14 #10 | E10-S04, E10-S05, E10-S06 |
| D-M2-3 | **Approval depth is configurable per organization between 1 and 50.** | §14 #11 | E10-S03 |
| D-M2-4 | **Conditions may route on amount in M2** (APR-005). The named "threshold band" administration feature stays Phase 2 (APR-010). | §14 #19 | E10-S02 |
| D-M2-5 | **A standalone requisition's approval creates a new top-level project and allocates the approved amount to it as new budget**, then reserves it (REQ-004). An approver may instead assign an existing project, which then funds it like a project-linked requisition. Approval can therefore create budget; the final approver of a standalone requisition must hold `budget.allocate` on its BU/OU. | — | E09-S04, E09-S06 |
| D-M2-6 | **Requisition expiry is a per-organization setting, unset by default** (`requisition.expiry_days`, never expires). When set, an approved requisition with no RFQ started within that many days of approval expires and releases its reservation exactly once (REQ-013). | — | E09-S05, E09-S07 |
| D-M2-7 | **A requisition's currency is always its project's currency, which is the organization's base currency.** It is derived, never entered. Only supplier prices (RFQ bids and stored RFQ history, M3) may be in another currency. | §14 #8 (in part) | E09-S01, E09-S04, E09-S06 |
| D-M2-8 | **Attachments that pass allow-list, magic-byte and size checks are marked `basic_checks_passed` and are downloadable by authorized users; they are never shared externally** until full scanning exists (E19-S04, D-14). | — | E09-S10 |

## Defaults to confirm (Opus, 10 Oct 2026)

| ID | Default | Why | Packets |
|---|---|---|---|
| D-M2-9 | **Live-membership rule (APR-018 requires it written down):** an `all` step completes when every *current* member has approved — a member removed before completing no longer counts, a member added before completion must also approve; an `any` step completes on the first approval by a current member; a `quorum(n)` step counts approvals from current members only, and an approval by someone who has since left the group no longer counts. Every decision records the group's membership at that moment. | D-M2-2 chose live membership; this is the consistent rule it needs | E10-S05, E10-S06 |
| D-M2-10 | **Default approval depth 10** (D-M2-3 gave the range, not the default). | Generous for capital chains, blocks runaway definitions | E10-S03 |
| D-M2-11 | **Workflows apply per document type at organization scope, with an optional BU/OU override** (the nearest scope wins, as D-M1-1 does for settings). No document type has a workflow until one is published; with none, the document's own "no approval applies" rule holds (PROJ-002: a project activates automatically). | Mirrors settings precedence | E10-S01, E10-S13 |
| D-M2-12 | **A requisition-generated sub-project is created under the requisition's project, at depth + 1**; submission (REQ-011) blocks a requisition whose project is already at the 5-level limit (E06-S02), so approval can never fail on depth. | Approval must not fail after the money is checked | E09-S04, E09-S06 |
| D-M2-13 | **Material fields of a requisition** (APR-015) are: project, BU/OU, department, ledger account, estimate, and the set of lines with their quantities and amounts. A change to any of them after approval requires resubmission; justification, needed-by date and delivery location do not. | REQ-012 needs a definition | E09-S08, E10-S07 |

## Follow-ups raised by these decisions

- **D-M2-7 versus M1:** `ProjectCreate.currency` (E06-S01) accepts any currency, so a project can exist in a currency other than the organization's base currency. D-M2-7 says projects carry the organization currency. M2 does not change M1: a requisition simply takes its project's currency. The operator should decide whether project creation should be restricted to the base currency; that is a separate story.
