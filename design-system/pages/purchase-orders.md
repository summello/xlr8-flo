# Page spec: Purchase orders (list, create, editor, detail, issue, transmission log, change order, vendor copy, sending domain, decision)

**Stories:** E13-S01 (PO from awarded lines, numbering), S02 (tax and freight), S03 (lifecycle), S04 (approval before issue), S05 (issue commits atomically), S06 (immutable issued PO, change orders), S07 (cancel or reduce releases once), S08 (PDF from snapshot), S09 (email via outbox, retry), S10 (deliverability), S11 (internal versus vendor-facing). **All are engine or ledger stories; no PO UI story exists**, so the UI stories in `design-system/canvas/STORY-NOTES.md` section 9 must be raised and must cite this file. **The E13 money rules are blocked** pending E09-S13 (which project a commitment lands on) and the vendor-currency valuation; the screens are drawn with server figures and labelled assumptions. **Read first:** `design-system/MASTER.md`, then this file, then `pages/vendors.md` (eligibility signal) and `pages/sourcing.md` (the award). Layout reference: canvas page `Purchase Orders` (83 boards) and two boards on `Approval Inbox` (`ApprSheetPo`, `ApprSheetPoSuspended`). The canvas is a drawing; where it disagrees with MASTER or this file, the canvas is stale.

Requirements: PO-001 to PO-017, FIN-008, FIN-009, EML-001 to EML-006, SEQ-001 to SEQ-003, COL-002, SRC-018, ACC-002, ACC-005, ACC-006, ACC-008, OBS-004, A11Y-003, A11Y-004, UX-002, UX-004, UX-005.

**MASTER was not amended.** Needs an operator-approved amendment before code: the PO status map uses `Partially Delivered`, `Delivered` and `Accepted` where MASTER 2.3 says `Partially Fulfilled` and `Fulfilled` (decision 169); requirement PO-007 carries the old wording and is flagged for Opus (a requirement is not changed on a board).

---

## 1. Routes and purpose

| Route | Purpose | Who |
|---|---|---|
| `/purchase-orders` | What is waiting on me, and did everything arrive? | `purchase_order.view` |
| `/purchase-orders/new` (also `Create PO` on an approved award) | Which awarded lines am I ordering now? | `purchase_order.create` |
| `/purchase-orders/:id/edit` | Is this the document I want the vendor to receive? (draft) | `purchase_order.create`, `amend` |
| `/purchase-orders/:id` | Where is this PO, what has it done to the budget, and what did the vendor get? | `purchase_order.view` |
| `/purchase-orders/:id/issue` | Issue it, once | `purchase_order.issue` |
| `/purchase-orders/:id/changes/:n` | What changes in version n+1, and what does it do to the budget? | `purchase_order.amend` |
| `/purchase-orders/:id/vendor-copy` | What exactly does the supplier receive? | `purchase_order.view` |
| `/admin/sending-domain` | Can purchase orders be delivered? | organization administrators |
| decision panel (Approval Inbox) | Decide a PO before issue (PO-003) | the assigned approver |

Operate mode in the Commit phase, with one Experience-grade moment: issue (MASTER 6.2, signature moment 5).

## 2. Rules

- **A PO is traceable to an award, and only to an award** (PO-001, SRC-018). Creation starts from awarded lines with their open quantities, one PO per vendor; ordering part of a line is a quantity field, and a partial PO reduces only that line's open quantity and value (PO-009). The second path in PO-001, "authorized project items", is **not in M3** (decision 163).
- **Approval reserves, issue commits, once** (§14 #5, PO-008, PO-012). Approval never commits. `Issue` converts the reservation into a commitment atomically with the status change; a second click, a retry or a worker retry gives the same result and never issues or transmits twice. The detail page shows reserved, committed and released side by side with the sentence that says which one the PO holds now. **The project the commitment lands on is the one that holds the reservation at issue** (decision 162); drawn as an assumption, E09-S13 is open.
- **Price, tax and freight are always separate** (PO-016, FIN-008, FIN-009): per-line columns and separate totals; tax per line; freight per line or per document, shared pro rata by line value; rounding per line, with document totals the sums of rounded lines (decision 164). A line total on the PO equals the award's landed line amount.
- **Bill To** comes from the organization or unit address and **Ship To** is chosen from the delivery locations the requisition uses; no typed one-off Ship To in M3 (decision 165).
- **What the supplier receives is one email and one PDF, and nothing else** (decision 170). The email body is a high-level summary of the PO; the PDF is a simplified vendor copy **without chartfields (project, department, ledger account) and without budget information**, generated from the PO snapshot and template version (PO-014). Internal notes never reach it. **No HTML attachment.** A note marked `Sent to Vendor` appears on the PDF; **an uploaded file is never sent in M3** and shows `Not sent until scanning is available` (decision 171, D-M2-8, D-14, E19-S04).
- **An issued PO is a record** (PO-010, ACC-005). It has no Edit; it has `Start Change Order`, a versioned draft with a field-level diff in words and a struck `was`, a signed budget effect, and a reapproval rule: **a material change** (amount, project, ledger account, item, line count; D-M2-13) needs approval, a date alone does not (decision 166). Version 1 stays readable with its PDF.
- **Cancel or reduce releases only the unused commitment, once, and keeps history** (PO-013, ACC-006). The dialog states the released figure, what stays and whether the vendor is told. Destructive per MASTER 7.13: danger text on the trigger, the danger fill only on the confirm button, safe button focused.
- **Sending is not arriving** (PO-004, PO-015, EML-002, EML-003, EML-006). Every send is a row with time, recipient, version, template version, provider id and result. A bounce leaves the PO `Issued`, shows `Bounced` as a state with a next step (`Change Recipient`, then `Resend`), and a suppressed address says it is never retried silently. `Resend` sends the same version and says so; it commits nothing more.
- **The email goes to the vendor's `Service and Delivery` contact** (decision 168); the requester gets an internal notification on issue, not a copy.
- **A PO in a vendor's currency commits at the rate stored with the award,** fixed at issue and shown with its date; a change order revalues only changed lines (decision 167). Drawn as an assumption.
- **Lifecycle:** Draft, Approval Pending, Approved, Issued, **Partially Delivered, Delivered, Accepted** (future states in M3; goods receipt arrives in M4), Closed (manual); Cancelled and Superseded beside it. The linked requisition becomes **`Completed`** when its lines are accepted (decision 172).
- **People inside the organization are shown by email** (D-M2-16). Submit and Save are never disabled to signal an error (DECISIONS 45).

## 3. Status vocabulary (MASTER 2.3 with the operator's nomenclature)

`draft` neutral, `approval_pending` info, `approved` info, `issued` success, `partially_delivered` info, `delivered` success, `accepted` success, `closed` neutral, `cancelled` danger, `superseded` warning; icon plus words. Transmission result: `Not Sent`, `Queued`, `Sent`, `Delivered`, `Bounced`, `Complained`, `Failed`, `Address Suppressed`, each an icon plus words.

## 4. Screens

1. **PO list.** Tabs `Drafts`, `Awaiting Approval`, `Ready to Issue`, `Issued`, `Needs Attention`, `All`. Columns `PO Number`, `Vendor`, `Project`, `Total` (currency visible), `Status`, `Sent` (`Delivered 12 Oct`, `Bounced 11 Oct`), `Version`. `New PO` is the one Primary.
2. **Create from awarded lines.** Grouped by vendor; `Awarded`, `Already Ordered`, `Open`, editable `Quantity to Order`; one PO per vendor; above-open refused at the field with the figure; empty state says why.
3. **PO editor.** Order header (PO-002), vendor eligibility at the PO vendor field with an override for the authorized, recipient contact, lines with price, tax, freight and line total apart, totals bar, **Budget Preview** (`Issuing will commit 175,665.58 USD on Plant 4 Line Retrofit, from its 193,012.13 USD reservation`), notes and files with visibility in words (`Internal` by default). `Submit for Approval`, or `Approve` when no PO workflow is published.
4. **PO detail.** Lifecycle ordered list, Order, Budget, Sent to Vendor, Notes and Files, Lines, Transmissions, Versions and Change Orders, History, Linked Records. Header actions by state: `Edit` (draft), `Issue` (approved), `Resend` and `Change Recipient` (bounced), `View PDF`, `Start Change Order`, `Cancel or Reduce` (issued).
5. **Issue** (confirmation, then signature moment 5). The confirmation lists recipient, version and template, **what will be sent and what stays here**, and the commitment. The moment: the document lifts on `--shadow-drag`, a seal marks it, a trace runs to the vendor row as the email is queued; on failure the trace snaps back red and `Resend` is already focused. Played once per session; reduced motion shows the finished picture with a note; every frame also exists as words and a table (A11Y-009).
6. **Transmission log and sheet.** A card on the detail with a sheet per send.
7. **Change order.** Quantities and freight and needed-by are editable; the diff and the signed budget effect update as you type; the reapproval line says whether approval is needed and why.
8. **Cancel or reduce.** One dialog with a mode choice.
9. **Vendor copy.** The email as the supplier sees it and the PDF, with a list of what never goes to the vendor; the PDF is also a sheet on the detail.
10. **PO decision** (Approval Inbox variant): source award, budget effect when issued, vendor eligibility now, award justification, what will be sent. Approve stays enabled (DECISIONS 52).
11. **Sending domain** (Administration): SPF, DKIM, DMARC with pass or fail in words and what to fix, the daily allowance, the suppressed-address list.

## 5. States

Every screen has loading (skeletons at final height), empty, partial (one card fails, the rest stays current), error (what happened, what was kept, how to recover) and all five widths. Also: issue failed; double click on Issue (one result); partial PO; change order that lowers the commitment (signed, parenthesised, coloured); cancelled PO read later; vendor currency; vendor suspended after award (issued PO unaffected, says so); 40 lines at 375 (stacked lines).

## 6. Keyboard and accessibility

Tabs follow the arrow, Home and End keys. Dialogs and sheets trap focus, close on Escape and return it to the opener; the safe button is focused. Money is tabular with the currency code. Status and transmission result are never colour alone. The signature moment ends in words and a table. 44px targets below 1024. axe passes in both themes on every board (`purchase-orders.spec.mjs`). ACC-008: a complete project-to-PO workflow is operable without a mouse; this page contributes the PO leg.

## 7. Open questions (park in the E13 follow-up story)

1. **Which project a commitment lands on** (decision 162): the project that holds the reservation is the assumption; E09-S13 decides.
2. **Valuation of a vendor-currency PO** (decision 167): the award's stored rate, fixed at issue; the engine rule is open.
3. **Closing a PO:** `Closed` is manual in M3; the rule for automatic closing arrives with goods receipt (M4, E14).
4. **Requirement and MASTER wording:** PO-007 and MASTER 2.3 say Fulfilled; the product says Delivered and Accepted (decision 169).
5. **A PO whose vendor is suspended after approval but before issue:** drawn as an override by a sourcing lead; confirm separation of duty.
6. **Supplier acknowledgement:** the email asks the vendor to reply; nothing captures a reply in M3.
7. **PDF template ownership:** who edits the template and how a new version is approved is not drawn.
8. **Daily sending allowance:** 100 a day (Resend free tier); what happens when it is reached is not drawn beyond the count.

## 8. Tests the stories must leave behind

Happy path from an approved award to an issued PO, keyboard-only (ACC-008 with the Sourcing page). Unit tests: tax and freight allocation and rounding (line totals equal the award's landed lines; document totals are sums of rounded lines); the open-quantity guard; issue is idempotent. **A real-Postgres concurrency test: two simultaneous issues, exactly one commits.** A TEN-010 isolation case. An authorization test per role. **A test that no chartfield, budget figure or internal note is in the email or the PDF, with a planted violation.** A transmission test per state (queued, sent, delivered, bounced, complained, suppressed), a resend that does not duplicate the PO or the commitment, a cancel or reduce that releases once. Change order: diff in words, signed budget effect, reapproval only for a material change. Playwright with `@axe-core/playwright` on each route and the `MASTER.md` §8 checklist.

## 9. Needs an operator-approved MASTER amendment

The PO status map with `Partially Delivered`, `Delivered` and `Accepted`; and the issue moment's classes (`.po-stage`, `.po-doc`, `.po-seal`, `.po-trace`, `.po-vendor`), tokens only, with the `.pdf-page` vendor-copy style.
