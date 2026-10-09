# Page spec: Budget transfer (transfers, allocations and adjustments)

**Stories:** E07-S07 (same-level transfer, one `transfer_group_id`) and E07-S08 (cross-hierarchy transfer, up then down, one transaction) are **engine stories with no UI story**; this page needs one (see `design-system/canvas/STORY-NOTES.md` section 5). Allocation and adjustment (BUD-001) have no story yet either. **Read first:** `design-system/MASTER.md`, then this file. Layout reference: canvas page `Budget Transfer` (boards `BudgetTransferList`, `BudgetTransferCreate`, `BudgetTransferDetail`, `TransferPosting`, their widths and states) and `ApprSheetTransfer*` on the Approval Inbox page.

Requirements: BUD-001 to 008, BUD-011, APR-003, ACC-002, WF-003, FIN-006, A11Y-003, A11Y-004, A11Y-006, A11Y-009, UX-004, UX-005.

Every figure on these screens is a server figure. The screens compute none of them except to preview an amount the server will recompute under the lock (FIN-006). Money is `Decimal`/`NUMERIC(18,4)` on the server; the canvas holds whole cents so a total cannot drift (decision 46).

---

## 1. Routes

| Route | Screen |
|---|---|
| `/budget/transfers` | List (transfers, allocations, adjustments) |
| `/budget/transfers/new` | Create |
| `/budget/transfers/:id/edit` | Edit a draft (same screen) |
| `/budget/transfers/:id` | Detail |

Entry points: `Transfer Funds` in the project dashboard header, and `N transfers this year` in the Budget Summary card of project detail (links to the list filtered to the project). One id family, `BT-`, for all three types (assumption, section 8). The inbox's `BT-` rows open the existing decision panel in its transfer variant (section 6).

## 2. List

Tabs `Mine`, `Awaiting Approval`, `Posted`, `All` (counts in each tab; `Posted` includes `Reversed`). Search (number, project, requester), project filter, type filter (`Same Level`, `Cross Hierarchy`, `Allocation`, `Adjustment`). Columns: Transfer (id, with a caption: step, `Reverses BT-...`, `Reversed by BT-...`), From and To (two lines; an allocation reads `Available Budget` / `to <project>`, an adjustment `<project>` / `decrease of its allocation`), Amount (a decrease in parentheses and red), Status (icon and words), Type (in words), Effective. Sort on Transfer, Amount, Effective. 10 per page, server filtered and paged (PERF-005). Below 900 card width Type and Effective drop out and return as labelled values in the stacked card below 660. No total row: a column of mixed signs has no meaning.

States: loading (skeleton rows at final height), empty (`No Transfers Yet` with the primary action), no match (`No Transfers Match` and `Clear filters`), error (filters kept), partial (`Status delayed`, statuses read `Not available`, amounts stay).

## 3. Create (and edit a draft)

Two columns from 1100px of content width: main (8/12) cards **Transfer Details**, **Ancestry Preview**, **Entries That Will Post**; side (4/12) cards **Checks** and **Supporting Evidence**. Sticky action bar with the draft state, the required-field line (decision 66) and `Save draft`, `Submit for approval`.

### 3.1 Type (decision 67)

A radio group `Type`: `Transfer between projects` (default), `Allocation`, `Adjustment`. The heading follows (`New Transfer`, `New Allocation`, `New Adjustment`).

| Type | Fields | Hidden |
|---|---|---|
| Transfer | Source Project, Target Project, Amount, Effective Date, Reason | |
| Allocation | Project, Amount, Effective Date, Reason | Source, ancestry preview. Funds come from the organization's **available budget** (decision 99); its availability is checked. |
| Adjustment | Project, Direction (Increase or Decrease), Amount, Effective Date, Reason | Source, ancestry preview. A decrease is checked against the project's available (BUD-008). |

Currency (read-only, `USD`) and Requester (read-only) always show. Evidence is in the side card.

### 3.2 Rules (decisions 68 to 77)

| Rule | Behaviour |
|---|---|
| Eligibility (BUD-002, 69) | Same organization, same hierarchy depth, both projects open, same currency. Anything else is **Cross Hierarchy** and needs the organization policy flag (name to be decided; the sample says "Transfers across levels") plus approval. A closed project, a different currency, the same project twice or a disabled policy gives an `Eligibility` entry. |
| Kind | `Same Level` or `Cross Hierarchy` in words, from the two projects, as soon as both are chosen. |
| Funds (BUD-008, 71) | A transfer, an allocation or a decrease that would take the source below zero is **blocked, with no override on the screen**. The shortfall shows as a parenthesised, red figure and a `Funds` entry. The server decides under `SELECT ... FOR UPDATE` (FIN-006); the figure is advisory. |
| Currency (72) | Same currency only. No rates, no conversion. |
| Effective date (73) | Today or later, in an open period. A past date gives `Period: ... is in the past`; a date in a closed period gives `Period: ... closed period (<month>)`. |
| Evidence (74) | An organization setting: off, Cross Hierarchy only, or always. When it demands evidence the card shows `Required, Not Attached` and submit gives a `Required field` entry; otherwise `Optional`. Files show scan states as on project detail. |
| Approval (70) | The Checks card states the route, prefixed `Set by your approval thresholds`. **Thresholds are configurable by tenant and by entity, with a provision for user-based thresholds** (assumption: the most specific scope wins; the routing explanation names it). Same Level: the workflow by amount (sample: source owner, then the Plant Manager; over 100,000.00 USD adds the Finance Director). Cross Hierarchy: always routed, source owner, target owner, then the Finance Director. Ancestor owners are notified, not asked. **The sample amounts are not rules.** |
| Race (77) | If the funds went between the preview and the submit, the server refuses. The screen shows `Not submitted. The available amount changed` as a `role=alert` with focus, the current figure and `Another transfer used the funds first`, keeps every typed value, and offers one button, `Edit Amount`, which focuses the field. No queue and no automatic retry. |

### 3.3 Ancestry Preview (decision 68, signature moment 3)

Shown for a valid transfer only (both projects, eligible, amount greater than zero); until then a card says why (`Choose Both Projects`, `Not Eligible Yet`, `Enter an Amount`).

Two tables, stacked: **Source, Going Up** (the source, its business unit, the organization) and **Target, Going Down** (the organization, the target's business unit, the target). Columns Level, Available Before, Change, Available After. A level shared by both paths reads `No net change` and says `shared by both paths`. A level that moves shows a meter with its share of that level's available as text. Tabs `Chart` and `Table`: the table view is the same levels in one list in recount order with a State column (`Recounted`, `Recounting`, `Waiting`). The totals strip shows Moved Out, Moved In and Net Change (zero when every level is recounted).

**Ancestors are recounted, not posted to (68).** Only the two projects get entries. **This reads BUD-003 and requirements 7.3 ("balanced, linked ledger entries for every affected level") as the recounted roll-up lines of this preview (DECISIONS 96). It needs an Opus decision-log entry before E07-S08 is built (`notes.blocked`); locks are taken on the two project rows only, in ascending id order.** The canvas shows the assumption as a caption under the entries.

### 3.4 Entries That Will Post

Transfer: `Transfer Out` (negative, parenthesised, red) and `Transfer In` on the Allocated bucket of the two projects; net zero. Allocation: one entry, `Allocation From Available Budget`. Adjustment: one entry, `Adjustment, Increase` or `Decrease`. Every entry shares one transfer id, assigned on submit.

### 3.5 Validation (UX-005, decision 45)

Submit and Save are never disabled to signal an error. A failed submit gives a summary at the top (`role=alert`, focus moves to it), `Nothing was submitted. Everything you entered is kept.`, each problem as `Category: link` focusing its control. Categories: **Required field, Eligibility, Funds, Period, Routing**. Typed input is always kept. After a good submit: `BT-00033 Submitted for Approval`, who it is routed to, and that nothing posts until the last step approves.

## 4. Detail

Dashboard layout rule (same as requisition detail): under 760 one column; 760 to 1099 cards 7/5; 1100 and up 8/4; cards in a row share a height. Header: title (`Transfer <from> To <to>`), id, kind, requester, status (icon and words) and the kind pill. Actions: `Withdraw` (pending, requester), `Reverse Transfer` (posted, **Finance Administrator only**, decision 98; anyone else sees `Only a Finance Administrator can reverse a transfer.`), `Create Revised Transfer` (rejected).

Cards: **Transfer** (amount, from, to with owners, effective date, reason, requested, transfer id), **Approval Trail** (the first entry says who it waits for, with delegation attribution as on the inbox), **Ancestry As Posted** (the same component as 3.3; for a pending transfer `Ancestry As It Will Post`), **Supporting Evidence**, **Ledger Entries** (entry ids that link to the ledger sheet, bucket, amount, posted time; `Will post` before posting; net zero; entries are never edited), **Linked Records** (the reversal or the original).

A banner explains a terminal state: `Reversed By BT-00030` (the original entries stay), `Rejected` (with the reason; nothing posted).

### 4.1 Reversal (decision 76)

`Reverse Transfer` opens a confirmation (`role=alertdialog`, focus on the safe button `Keep As Posted`, Escape closes and returns focus to the opener, Tab stays inside). It says that a **new linked draft** is created, that it goes through the **same approval route**, that the original is untouched (append-only), and that it is refused if the target no longer has the funds. Confirming with too little available gives `Not reversed. The funds are not there` as a `role=alert` with focus, the figures, and `Nothing was created`. A reversed transfer offers no second reversal.

## 5. The posting moment (MASTER 6.2, moment 3)

When a transfer posts, the ancestry trace plays once per session: each level in turn (up the source, down the target) takes a state, the dot fills and pulses once (180ms, `--dur-base`, **transform only**), and the totals recount. Static frames exist for every stage, and **the end state says the same thing**: `Posted`, every level recounted, net change zero. Under `prefers-reduced-motion` nothing pulses and the finished picture is shown at once, with a note. State is always a word (`Recounted`, `Recounting`, `Waiting`) as well as a dot. `Replay Posting` is a prototype-only button. The Table tab is the accessible data equivalent (A11Y-009). The live region (`role=status`) reads `Recounting <level>, step N of M.`.

## 6. Decision panel, transfer variant (inbox)

The existing panel (DECISIONS 52 to 55, 65) gains **What Posts If You Approve** after the key facts: kind, source availability **at decision time** (available now, this transfer, available after, with a pill), both ancestries summarised (level and change), and the two entries that would post, with `Funds are checked again under a lock when you approve`. `Needed By` becomes `Effective Date`. If the funds went, **approving is refused, not warned about** (like a stale version, APR-016): `Not decided. The funds changed.` with the figures, the comment kept, the Approve button still present, and Reject and Return still available.

## 7. Dashboard entry points

`Transfer Funds` (a plain button) in the project dashboard header, shown to authorized users only (server-side authorization on the route as well; hiding the button is not authorization). `N transfers this year` and `N awaiting approval` in the project detail Budget Summary card.

## 8. Open questions (put in `notes.blocked`; the canvas shows each assumption)

Answered 9 Oct 2026 (DECISIONS 98 to 103): thresholds are configurable by tenant, entity and user; the policy flag is `Allow Cross-Level Transfers`, administrator only; only a Finance Administrator reverses; one `BT-` prefix; an allocation draws on available budget; allocation and adjustment reuse the transfer workflow. Still open:

1. **BUD-003 / 7.3 wording** versus decisions 68 and 96 (ancestors recounted, no entries of their own): needs the Opus decision-log entry proposed in `STORY-NOTES.md` section 5.
2. **Threshold resolution order** across user, entity and tenant (assumed most specific wins) and what a user-based threshold means exactly (an approval limit per person, assumed).
3. **Which administrator role** changes `Allow Cross-Level Transfers` and the evidence setting.

## 9. Tests the stories must leave behind

Unit tests on the recount (before plus change equals after on every level, shared levels net zero, the two entries net zero), a real-Postgres concurrency test (two transfers racing for the same funds: exactly one wins, the loser gets the refusal), Idempotency-Key and ACC-002 (retrying an approved transfer posts once), a TEN-010 isolation case (a foreign tenant's transfer is a 404), authorization per role, a state-machine test for Draft, Approval Pending, Returned, Rejected, Posted, Reversed, and Playwright with `@axe-core/playwright` on each route, a keyboard-only path, the tab keys, the reversal dialog's focus handling, and a reduced-motion check. The canvas's own checks are in `design-system/canvas/tests/transfers.spec.mjs`.

## 10. Notification copy (draft, decision 103)

Who is told and when is decided (decision 75). All of it goes through the outbox (never sent from a request handler), is plain text with one link, shows every amount with its currency, and never includes attachment contents or another organization's data. Resend's 100 a day is a real ceiling: everything except a refusal or a failed posting is batched into the recipient's digest, and an organization may choose immediate delivery for approval tasks. Each event has one subject line and a short body; `[Link]` is the only link.

| Event | To | Delivery | Subject | Body |
|---|---|---|---|---|
| Submitted | Approvers of the current step | Approvals digest (or immediate, per setting) | `Approval needed: BT-00031, 40,000.00 USD, Plant 4 Line Retrofit to Plant 2 Dock Upgrade` | `Luis Moreno asks you to approve a transfer of 40,000.00 USD from Plant 4 Line Retrofit to Plant 2 Dock Upgrade, effective 12 Oct 2026. Reason: Move unused budget before the winter shutdown. You are step 2 of 2. Available on Plant 4 Line Retrofit now: 546,000.00 USD. [Review Transfer]` |
| Returned for changes | Requester | Immediate | `BT-00031 was returned for changes` | `Amara Okafor returned your transfer: "<reason>". Nothing posted. You can amend it and resubmit; the approvers will see what changed. [Open Transfer]` |
| Rejected | Requester | Immediate | `BT-00027 was rejected` | `Amara Okafor rejected your transfer of 90,000.00 USD: "<reason>". Nothing posted. [Create Revised Transfer]` |
| Posted | Requester, source and target project owners (and ancestor owners, as a notice, for Cross Hierarchy) | Digest | `BT-00031 posted: 40,000.00 USD moved` | `40,000.00 USD moved from Plant 4 Line Retrofit to Plant 2 Dock Upgrade, effective 12 Oct 2026. Available now: Plant 4 Line Retrofit 506,000.00 USD, Plant 2 Dock Upgrade 252,000.00 USD. Entries LE-01944 and LE-01945. [View Transfer]` |
| Posted, across levels | Ancestor owners | Digest | `A transfer across levels posted under <level>` | `BT-00032 moved 60,000.00 USD from Corporate Contingency to Plant 2 Dock Upgrade. The total available for <level> changed from X to Y. You did not need to approve it. [View Transfer]` |
| Could not post | Requester and Finance Administrators | Immediate | `BT-00031 could not post on 12 Oct 2026` | `Plant 4 Line Retrofit has 24,000.00 USD available and 40,000.00 USD is needed. Nothing moved. [Edit Amount]` |
| Reversal drafted | Approvers (as Submitted), requester of the original | Digest | `Reversal of BT-00021 awaits approval` | `Luis Moreno drafted a reversal of BT-00021 (18,000.00 USD). The original stays in the ledger as posted. [Review Transfer]` |
| Reversed | Same as Posted | Digest | `BT-00021 was reversed by BT-00030` | `18,000.00 USD moved back from Plant 2 Dock Upgrade to Plant 4 Line Retrofit. [View Transfer]` |

Allocations and adjustments use the same events with `Allocation` or `Adjustment` in the subject and one project in the body.
