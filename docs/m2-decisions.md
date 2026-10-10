# M2 decisions

Decisions the M2 packets build on. **Operator decisions** were answered by the operator on
10 Oct 2026 and close the matching open decision in `docs/requirements.md` §14. **Defaults to
confirm** were filled in by Opus where an answer left a value open; each is conservative and
named in the packet that uses it. Reverse one by editing the packet before its story starts.

## Operator decisions (10 Oct 2026)

| ID | Decision | Closes | Packets |
|---|---|---|---|
| D-M2-1 | **Rejection: the approver chooses on every reject**, between *return to Draft* (the requester may amend and resubmit; **the reservation is released**) and *reject permanently* (terminal `rejected`; the reservation is released). No workflow-level policy restricts the choice. **"Return for changes" (APR-013) is a separate action that keeps the reservation while the requester amends**; resubmit adjusts it under the lock (D-M2-15). | §14 #9 | E10-S06, E09-S05, E09-S07 |
| D-M2-2 | **Approval-group membership is live**: who may decide is evaluated when the decision is made, not when the task was created. | §14 #10 | E10-S04, E10-S05, E10-S06 |
| D-M2-3 | **Approval depth is configurable per organization between 1 and 50.** | §14 #11 | E10-S03 |
| D-M2-4 | **Conditions may route on amount in M2** (APR-005). The named "threshold band" administration feature stays Phase 2 (APR-010). Together with canvas `DECISIONS.md` 83 (accepted 9 Oct: "workflow conditions use the estimate only; department and risk wait for Phase 2"): the condition **engine** is generic (APR-005), but in M2 the requisition field catalogue exposes only `amount`, so a builder can only route on the estimate. Phase 2 widens the catalogue; the engine does not change. | §14 #19 | E10-S02, E10-S03, E10-S11 |
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
| D-M2-13 | **Material fields of a requisition** (APR-015) are the operator-accepted fixed list from canvas `DECISIONS.md` 79 (9 Oct 2026): **amount (the estimate), project, ledger account, catalogue item, line count**. Not configurable per workflow; the builder has no setting for it. A change to any of them after approval requires resubmission; anything else is a non-material edit. | Operator decision, transcribed (it replaced an Opus default) | E09-S08, E10-S07 |
| D-M2-14 | **Delegation periods are instants** (`starts_at`, `ends_at`), converted by the browser from the user's chosen dates to the start of the first day and the end of the last day in the browser's time zone. Organizations have no time zone until user preferences (E17-S06). | The page spec says "inclusive, in the organization's time zone", which does not exist yet | E10-S08, E10-S10 |
| D-M2-15 | **Reserve at submit (operator, 10 Oct 2026, confirmed directly).** A **project-linked** requisition reserves its estimate **when it is submitted**: submit locks the project's funding row, validates (REQ-011's funds check is therefore authoritative, under the lock), reserves and starts the approval instance in one transaction. Approval keeps that reservation and generates the sub-project. REQ-002 is read as "approval confirms the reservation made at submit and generates the sub-project". Withdraw from pending, reject (either mode) and cancel release it exactly once; "return for changes" keeps it; resubmit releases the round's remainder and reserves the new total under the same lock. Two concurrent submissions against funds for one: exactly one succeeds (ACC-001). **A standalone requisition still reserves only at approval** (D-M2-5). Recorded as D-21 in `docs/claude-plan.md` §1. | Operator decision (it replaced the reserve-at-approval reading) | E09-S04, E09-S05, E09-S06, E09-S07, E09-S08, E10-S10, E10-S11 |

## Canvas decisions this pass transcribes or overrides

The design canvas (`design-system/canvas/DECISIONS.md`) records operator-accepted decisions from
9 Oct 2026. Where one bears on M2 behaviour it binds the packets (78 withdraw until sourcing starts,
79 material-change list, 80 revoking a delegation returns undecided tasks to the owner, 81 no
re-delegation, 82 duplicate approval only per step with an explicit tick, 83 estimate-only
conditions). Where a page spec or board disagrees with a **later** operator decision or with a
requirement, the later decision wins and the board is stale (AGENTS.md, "Authority for UI values"),
recorded as a `notes.followup` in the story that meets it:

- **Membership:** `approval-inbox.md` §2.4 and `approval-workflow-builder.md` §3 say *snapshot*; D-M2-2 (10 Oct) says *live*.
- **Depth:** `approval-workflow-builder.md` §4 says a fixed 25; D-M2-3 says 1..50, default 10.
- **API paths** in the page specs (`/approval-workflows/{id}/publish`, `/approvals/tasks/{id}/decision`) are illustrative; the packets' routes are authoritative.
- **Reservation timing — resolved by the operator (D-M2-15):** the boards that show a reservation at submission (`requisition-create.md`, `requisitions.md`, the inbox withdraw dialog, the builder Start node) are **current**, not stale.

## Follow-ups raised by these decisions

- **D-M2-7 versus M1:** `ProjectCreate.currency` (E06-S01) accepts any currency, so a project can exist in a currency other than the organization's base currency. D-M2-7 says projects carry the organization currency. M2 does not change M1: a requisition simply takes its project's currency. The operator should decide whether project creation should be restricted to the base currency; that is a separate story.
- **D-M2-5 versus canvas `DECISIONS.md` 99 (operator: "an allocation draws on the organization's available budget; there is no separate unallocated pool"):** a standalone requisition's approval allocates its amount to a new top-level project through `budget.service.allocate` in Direct mode, which today has no organization-pool check, so it creates budget. Either 99 needs an organization-level budget pool that Direct allocations draw from (a new story), or D-M2-5 stands as an explicit exception. **Operator to decide**; E09-S06 records it in `notes.followup` and implements D-M2-5 as written meanwhile.
