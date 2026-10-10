# Page spec: Approval inbox, decision panel, withdraw, resubmit and delegation

**Stories:** E10-S10 (inbox and decision panel UI), E10-S06 (decisions), E10-S09 (routing explanation), E10-S07 (stale guard), E10-S08 (delegation engine, **no UI story yet**), E09-S08 (return, amend, resubmit engine, **no UI story yet**), REQ-013 (withdraw). See `design-system/canvas/STORY-NOTES.md` section 3 for the stories this page needs. **Read first:** `design-system/MASTER.md`, `design-system/pages/requisitions.md` (status words, money), then this file. Layout reference: canvas page **Approval Inbox**, boards `ApprovalInbox`, `Appr*`, `RequisitionDetail` (withdraw, returned), `RequisitionResubmit`, `Resub*`, `ApprovalDelegation`, `Deleg*`.

Requirements: APR-004, 009, 013 to 021, REQ-012, REQ-013, WF-001, WF-006, USR-007, ACC-007, A11Y-004, A11Y-006, UX-004, UX-005, DATA-002.

Every amount is a server figure. The screens compute none of them except to display a difference, and each carries `Decimal`/`NUMERIC(18,4)` on the server as everywhere.

---

## 1. Approval inbox (`/approvals`)

Heading `Approval Inbox`, subline `9 waiting on you, 2 overdue · amounts in USD`. Header action `Set Out-of-Office` (link to section 5). When the signed-in user has an active or upcoming out-of-office, a `role=status` notice says who receives their approvals and links to change it.

One card, `Approval Queue`. Tabs (`role=tablist`, arrows, Home, End, sliding indicator, counts): **Pending, Overdue, Completed, Delegated** (APR-020). Overdue is the part of Pending past its due date; the same task is in both counts. The tab is in the query string.

- **Toolbar** (`role=search`): `Search` (number, title, requester), `Type`, `Clear filters` when either is on.
- **Table** (`role=table`, `.cols-appr`): `Document` (mono number, title, requester and version beneath; the whole cell is a button that opens the decision panel), `Type`, `Amount` (right, sortable), `Step` (`1 of 2`), `Due` (sortable; `Decided` on the Completed tab), `Status` (pill with icon and words). Below 900 card width `Type` and `Step` drop out of the row and return in the stacked card below 660.
- **Status words:** Pending: `Your Turn`, `Overdue`, `Changed` (the document changed since the list loaded), `Resubmitted`. Completed: `Approved`, `Returned for Changes`, `Rejected`, and under it `On behalf of <name>` when decided as a delegate (APR-014). Delegated: `Covering <name>`, `Your Own Request` (cannot be decided), `With <name>` (you passed it on).
- **Overdue** shows `3 days overdue` in words and an icon, plus a start-edge rule; never colour alone.
- **Delegated tab** has two groups: `Covering For <name>, Until <date>` and `Passed To Others While You Were Out`.
- A total row: `Showing 9 of 9` and `Total waiting <amount> USD` (the sum of the visible pending rows that have an amount; vendors have none and say `Not applicable`).
- Server-side filtering, sorting and paging (PERF-005); the sample shows nine rows only because it is a sample.

States: loading (skeleton rows at final height), empty per tab (`All Caught Up`, `Nothing Overdue`, `No Decisions Yet`, `Nothing Delegated`), no match (`No Approvals Match`, `Clear filters`), error (`Could not load your approvals`, search and filters kept, reference id), partial (`Due dates delayed`: Due and Step show `Not available`, everything else is current and decisions still work).

## 2. Decision panel (side sheet)

Opens from a row as a `role=dialog`, `aria-modal`, 640px (`.sheet.wide`), full width on a phone. Focus goes to Close; Tab is trapped; Escape and the scrim close it and return focus to the row. Nothing is decided by opening it.

Header: `REQ-00418 · Capital Requisition · v1`, the title, the status pill. Body, in order:

**Order (operator, 9 Oct 2026):** notices, key facts, the link, **Decision Trail**, the comment, then **Why It Reached You** folded. Details follow in the order they are listed here only where the order is unchanged.

1. **Notices** (`.summary`, icon and words), only when they apply: `This Changed While You Had It Open`, `Already Decided`, `You Cannot Decide Your Own Request`, `With <name>`, `You Are Covering For <name>`, `You Decided This`.
2. **Key facts:** requested by, project, amount (large, whole figure first), needed by, version, then the hyperlink `Open the full requisition` (`a.link`: coloured, underlined, underline thickens and the arrow moves 3px on hover).
3. **Changes Since Your Last Decision** (only on a resubmitted or reloaded document): the diff of section 4, then one sentence saying whether the change was material (APR-015).
4. **Why It Reached You** (APR-021), **a collapsed `<details class="fold">` after the comment, labelled `for audit`**, open by default only when it failed to load: the numbered route with your step marked, then `Rule Matched`, `Why You`, `Group Rule`, `Group Fixed` (membership is snapshotted when the task is created, APR-018) and `Separation` (APR-019). If this card fails to load it shows its own error and `Try again`; decisions stay possible.
5. **Decision Trail** (placed before the audit fold, and the most prominent block after the facts; `t-heading` with an event count): first a `Waiting for you, step 1 of 1, due <date>` entry with a filled dot, then earlier steps and comments, newest first, each with who, when and the note.
6. **Comment or Reason** (textarea) with the tag `Needed to reject or return` that becomes `Done` once there is text (`data-req`, DECISIONS 66). Required to reject or return; `aria-invalid` and a field error when missing. The hint says it is recorded with your name, the delegation you act under, and the version you decided.

Footer buttons: `Close`, `Post Comment`, `Return for Changes`, `Reject`, `Approve` (primary, last). They are never disabled to signal an error (the same rule as requisition submit, DECISIONS item 45); a refused attempt produces the alert below. For a document you cannot decide, the decision buttons are absent, not disabled, and a notice says why (WF-006).

**Refusals** (`role=alert`, `id=sheet-err`, focus moves to it, typed comment kept): `Not decided. A reason is needed.` and `Not decided. This requisition changed.` with a `Load Version 3` button that reloads the diff. The server enforces both independently (APR-016); an old `If-Match` version returns 409 and the same text.

**After a decision** the panel body is replaced by a `role=status` result: a tick that draws in 260 ms (transform-free stroke, reduced motion shows the finished tick), the outcome, what happens next, and an audit line (`Recorded: you, <time>, version N. Decisions cannot be edited.`). Buttons: `Back to Inbox` (focus) and `Next: <number>` which opens the next pending document and moves focus to its Close button. The row leaves Pending and appears in Completed; counts update. This is signature moment 4, kept as quiet as moment 2.

Transaction and idempotency (server): `POST /approvals/tasks/{id}/decision` with `Idempotency-Key` and the document version; lock the task row `FOR UPDATE`, check it is still open and the version current, write the append-only `approval_decision`, advance routing, and for a reject or withdrawal write the reservation reversal, all in one transaction (WF-003). Two simultaneous approvals by an any-one group: exactly one succeeds, the other gets the `Already Decided` response.

## 3. Withdraw confirmation (on the requisition detail page)

`Withdraw requisition` opens an `alertdialog` (`.dialog`), focus on **Keep Requisition** (the safe button), Tab trapped, Escape closes. Content: what stops (`APR-00302 is cancelled and Amara Okafor is told`), what is released (`Releases 196,000.00 USD to Plant 4 Line Retrofit, exactly once`), what follows (`Status becomes Withdrawn ... cannot be reopened`), an optional reason shown to the approver. Confirm is `Withdraw Requisition`; while the request runs it is `aria-busy` and reads `Withdrawing…`, and Escape is ignored.

Outcomes: success (`Withdrawn` pill, reservation card `Reserved 0.00`, `Released` equals what was reserved, ledger entries linked, toast, focus to the lifecycle heading); failure (`Not withdrawn`, nothing changed, reason kept, `Try Again`); already approved (`Already Approved`, nothing released, dialog offers only Close). Withdraw is allowed for a draft, a pending and a returned requisition (open question 1). Discarding a draft needs no dialog: nothing is reserved.

Server: `POST /requisitions/{id}/withdraw`, `Idempotency-Key`, lock the funding row `FOR UPDATE` before releasing, one reversal entry, release exactly once (REQ-013). A retry returns the first outcome.

## 4. Return, amend and resubmit (`/requisitions/:id/resubmit`)

A returned requisition shows a `Returned for Changes` banner with the approver's name, time and words, `Edit and Resubmit` as the primary action, and keeps the reservation. After editing, the requester reviews a diff before sending.

Heading `Review Changes Before Resubmitting`. Layout is the form layout (`.req-grid`, 8/4) with a sticky action bar.

- **What Changed:** one `.diff` table, `Field | Version 1 | Version 2 | Change`. The old value is struck through, the new one is bold, the change is a pill with an icon and a word (`Changed`, `Added`, `Removed`); money shows a computed delta (`Down 3,500.00 USD (2%)`). Below 520px card width each row stacks and keeps its column names. Prior values are preserved (REQ-012); version 1 is never overwritten.
- **What Happens When You Resubmit:** `Material Change` (routing restarts from the first step, earlier decisions no longer apply, reservation adjusts) or `Not a Material Change` (same step, the approver sees the diff). The server decides from the policy (open question 2); the screen shows its answer.
- **Funding Check:** reserved now, reserved after, `Released Once` or `Reserved More`, project available after. An increase is checked under the `FOR UPDATE` lock at resubmit (FIN-006); a shortfall is refused, with the shortfall in the alert and the draft kept.
- **Note to the Approver** (optional), kept if the send fails. **Versions:** a lineage list.
- **Resubmit for Approval** is never disabled. Nothing changed: `Not resubmitted. Nothing has changed.` Short of funds: `Funds: ... a shortfall of 62,000.00 USD`. Success: `Resubmitted as Version 2` with who is now asked. The summary takes focus.

The approver sees the same diff in the decision panel, labelled `Changes Since Your Last Decision`.

## 5. Delegation and out-of-office (`/approvals/availability`)

Heading `Approval Availability`. Starts, Ends and Pass Approvals To are required: each has the `Required` tag and rail, and the form says whether required fields are still empty (DECISIONS 66). Tabs `My Delegations` and `Organization` (administrators only; they can revoke but not create for someone else).

- **Out-of-Office** form: `Starts`, `Ends` (native date inputs), `Pass Approvals To` (a select of eligible people only, APR-004, re-checked on the server), `Covers` (`All my approvals` or `Purchase orders only`). `Save Out-of-Office` is never disabled. Errors: a summary at the top (`Not saved. 3 problems.`, `role=alert`, focus), each item `Category: link` that focuses its field (`Required field`, `Dates`, `Overlap`); inline field errors with `aria-invalid="true"`. Typed values are kept.
- **Your Delegations** table (`.cols-deleg`): delegate, period, covers, status (`Active`, `Upcoming`, `Ended`, `Revoked`), `Revoke`. Ended and revoked rows say `Kept in the record`.
- **Revoke** opens an `alertdialog`, focus on `Keep Delegation`. Text: new approvals stop; tasks still undecided **come back to the owner** (open question 3); decisions already made stay recorded as made by the delegate, acting for the owner (APR-017).
- **Decided on Your Behalf:** a lineage of past decisions with the original attribution, unchanged by later edits or revocation.
- Side cards: `Eligible Delegates` (why someone is missing), `Covering for Others` (and that you still cannot decide your own requests), `Reminders and Escalation` (set per step in the workflow builder, read-only here; while a delegation is active reminders go to the delegate and the due date does not move).

States: loading, empty (`No Delegations`), error (`Could not load your delegations`, typed values kept), form errors.

Server: dates are inclusive and in the organization's time zone; overlapping active or upcoming delegations from one person are refused; a delegate may not decide anything they requested (APR-019); every decision made under a delegation records both the delegate and the owner.

## 6. Open questions (put in `notes.blocked`; the canvas shows each assumption)

1. Who may withdraw, and until which status? The sample allows draft, pending and returned.
2. What counts as a material change (APR-015)? The sample: amount, project, ledger account, a catalogue item, or the number of lines. Is it configurable per workflow?
3. When a delegation is revoked, do undecided tasks return to the owner (sample) or stay with the delegate?
4. Can a delegate re-delegate? The sample says no.
5. Does a task returned to the requester keep its place in the approver's Completed tab as `Returned for Changes` (sample), and does the resubmitted document create a new task or reopen the old one?

## 7. Tests the stories must leave behind

Inbox: tab counts, arrows between tabs, filters, sort order and `aria-sort`, the total equals the visible rows, every state. Panel: focus in, trap, Escape returns to the row, reason required, stale refused then diff shown, own request and already decided offer no buttons, covering is recorded as on behalf, a result with the audit line, next moves focus. Withdraw: focus on the safe button, trap, success releases exactly once and the figures agree, failure keeps everything, already approved releases nothing. Resubmit: every change is a row with words, money delta equals the funding check, material and minor, no changes and short funds give an alert with focus and keep input. Delegation: each problem links to its field, overlap, a good save, revoke asks first, ended rows cannot be revoked. Authorization (TEN-010): another tenant's task, requisition or delegation returns 404, never 403; a user who is not the assigned approver gets 404 on the decision endpoint; an administrator-only tab is refused on the server. axe in light and dark, keyboard only, phone no sideways scroll.
