# Page spec: Requisition list and requisition detail

**Stories:** E09-S11 (list, detail; the linked-record graph is a gap, E09-S09). **Read first:** `design-system/MASTER.md`, `design-system/pages/requisition-create.md` (money, status words, shared patterns), and `design-system/pages/projects.md` is not written yet, so use the canvas boards `ProjectList` / `ProjectDetail` for the table and tab patterns. Layout reference: canvas boards `RequisitionList`, `RequisitionDetail`, `ReqList*`, `ReqDetail*` on the Requisition Create page of the design canvas.

Requirements: REQ-002, 005, 009, 010, 013, 015, 016, FIN-006, A11Y-004, UX-004, UX-005.

---

## 1. Requisition list (`/requisitions`)

Heading `Requisitions`, subline `FY27 · 12 requisitions · amounts in USD`. Header actions `Export` and `New requisition` (primary).

One card, `Requisition List`, with a live count (`role=status`).

- **Tabs** (`role=tablist`, arrow keys, Home, End, sliding indicator): `Mine`, `Drafts`, `Submitted`, `All`, each with its count. A tab never changes the URL without the route doing so; keep the tab in the query string.
- **Toolbar** (`role=search`): `Search` (number, title, project), `Status`, `Project`. `Clear filters` appears when any filter is on.
- **Table** (`role=table`): `Number` (mono link), `Title` (with the requestor beneath), `Project`, `Amount` (right-aligned, USD, whole amount first), `Status` (pill with icon and words), `Needed By` (dd/mm/yyyy in the sample, locale-formatted in the app), `Updated`. Sortable heads (`Number`, `Title`, `Amount`, `Needed By`) are buttons with `aria-sort`. Below 900px card width `Project`, `Needed By` and `Updated` drop out of the row and return in the stacked card below 660px.
- A **total row** for the filtered set and a pager (`Showing 1 to 10 of 12`, 10 per page).
- **Status words** are the lifecycle of REQ-005: Draft, Approval Pending, Sourcing in Progress, Sourced, Awarded, Open for Purchase, Completed; plus Rejected (and Withdrawn, Cancelled). Every status has an icon and its words; colour is never the only signal.
- Server-side filtering, sorting and paging (PERF-005). The sample shows 12 rows only because it is a sample.

States: loading (skeleton rows at final height), empty (`No Requisitions Yet`), no match (`No Requisitions Match`, `Clear filters`), error (`Could not load requisitions`, filters and typed input kept), partial (`Status delayed` notice; statuses show `Not available`, everything else current).

## 2. Requisition detail (`/requisitions/:id`)

Heading is the requisition title; subline `REQ-00418 · Plant 4 Line Retrofit · requested by Dev Patel`; below it the status pill. Header actions: `Edit draft` (drafts only), `Withdraw requisition` (pending) or `Discard draft` (draft). Withdrawing releases the reservation **exactly once** (REQ-013); the confirmation dialog is specified in `design-system/pages/approval-inbox.md` section 3 (canvas boards `WdDialog*`). A returned requisition shows its banner and `Edit and Resubmit`, and the diff screen is section 4 of the same file (REQ-012, E09-S08); `Withdrawn` and `Returned for Changes` are statuses with icon and words.

**Where It Is.** A full-width card with the lifecycle as an ordered list (`aria-label="Requisition lifecycle"`): seven steps, the current one `aria-current="step"`, done steps with a tick and `, done` in screen-reader text, not-yet steps `, not yet`. Under it one sentence in words saying where the request is and what happens next.

Then a grid that follows the dashboard rule (DECISIONS.md section 2, item 4): at 1100 to 1599 two columns 8/4, at 1600 and up 5/3/4 then 3/9, one column below 760; cards in a row share a height.

| Card | Content |
|---|---|
| **Request** (wide) | Key/value list: justification, project (link), business unit, department, ledger account, needed by, delivery, estimate. |
| **Reservation** (narrow) | `Reserved` as a large amount, `Released`, `Project Available`, and the sentence about when the unused part is released (REQ-013). A draft says nothing is reserved and what submitting would reserve. |
| **History** | A timeline (newest first): what happened, when, who. System steps (reservation, routing) are attributed to `System`. |
| **Linked Records** | A list of every record raised for this request: ledger entry, approval, RFQ, bids, award, purchase order. Each is a link with its kind and a one-line state. The caption says every record stays linked (REQ-009). **The graph view is a gap (E09-S09); do not draw one.** |
| **Lines** (table, no hover outline) | Per line: `Requested`, `Sourced`, `Awarded`, `Ordered`, `Cancelled`, `Remaining` (REQ-015). `Remaining` is requested less ordered and cancelled. A split award shows as awarded quantity above ordered quantity. Below 900px card width only Requested, Awarded and Remaining stay in the row; the others return as labelled values in the stacked card. |

A card that holds a table or grid takes no hover outline. Quantities and amounts here are the server's numbers; the screen computes none of them except the display of remaining.

States: loading (skeletons in every card), error (`Could not load this requisition`), partial (one card fails with `Could not load <name>` and `Try again` while the others stay current), nothing linked (`Nothing Linked Yet`). Stage boards (draft, sourcing, awarded) are on the canvas.

## 3. Open questions (put in `notes.blocked`)

1. Who may withdraw, and until which status (the sample allows draft and pending only)?
2. Does a rejected requisition show its approver's reason in History, and to whom?
3. Whether the Sourced step is a separate state in the product or a computed label (REQ-005 lists it, REQ-014 allows partial awards).
4. The Linked Records order and whether a PO appears per PO or as a group.

## 4. Tests the story must leave behind

List: tab counts, arrow keys between tabs, filters and sort, the total follows the filter, every status has an icon and words, every state, phone stacking. Detail: one `aria-current` step per status, done steps say so in words, remaining equals requested less ordered and cancelled on every line, a split award is visible, the reservation figure equals the ledger entry, history order, layout rule at 1920, 1440 and 375 with equal heights per row, a table card takes no hover outline, every state, axe in light and dark, keyboard only, phone no sideways scroll. Authorization: another tenant's requisition id returns 404, never 403 (TEN-010).
