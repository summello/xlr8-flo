# Handoff: Purchase Orders canvas page

**Status 13 Oct 2026: NOT BUILT.** Third of the three M3 pages; start it only when Vendors and the
Sourcing additions are approved. Read `HANDOFF-m3.md` first. This file holds what is specific to
purchase orders. The operator confirms the screen list and answers the questions in section 7 before
any board is made.

New canvas page: **`Purchase Orders`** (`{"id":"purchase-orders","name":"Purchase Orders"}`), board
prefix `PurchaseOrder*` for main boards and `Po*` for variants and states.

---

## 1. Job and audience

| Who | Arrives because | Needs to leave with |
|---|---|---|
| **Purchasing User** (`purchase_order.create/amend/approve/issue/close/view`) | an award is approved and ready to order | a correct PO issued once, to the right contact, with proof it arrived |
| **PO approver** | a PO waits in the inbox before issue (PO-003) | the PO, its budget effect and its source award on one panel |
| **Budget / Finance User** | committed spend moved on a project | to see what the PO committed, from which reservation, and reconcile it |
| **Requester** | wants to know whether their order has gone | the requisition's `Ordered` quantity and a link to the PO (already drawn) |
| **Vendor** (outside the product) | receives the email and PDF | a clean, versioned document with no internal note on it |

Mode **Operate**, with one Experience-grade moment: issue (MASTER §6.2, signature moment 5). Common
work, two actions or fewer: from an approved award, create the PO; from a PO, issue it; from a failed
transmission, retry it; from an issued PO, start a change order.

## 2. The reasoning: from requirement to screen

A purchase order is the moment **money leaves the building** (the Commit phase). Three properties
follow, and every screen exists to protect one of them.

1. **It is traceable to an award, and only to an award.** PO-001 (from approved and sourced
   requisitions or authorized project items), SRC-018 and D-16 (no PO without a completed RFQ), PO-009
   (partial POs reduce only the corresponding open quantity and value), SEQ-001 and PO-006 (numbered
   per configured scope). So creation starts from awarded lines with their open quantities, one PO per
   vendor, and partial ordering is a quantity field, not a separate feature. The second path in PO-001,
   "authorized project items", has no definition yet and conflicts with SRC-018: do not draw it
   (question 2).
2. **It commits money exactly once, at issue, never at approval.** §14 #5 and §3.9 of
   `docs/claude-plan.md`: approval does not commit; **issue** converts the reservation into a commitment
   atomically (PO-008, WF-003), so a failed transmission never strands money. PO-012 and ACC-002:
   double clicks, retries and worker retries never issue or transmit twice. So the PO detail shows a
   **budget card** with reserved and committed side by side and the sentence that says which one the
   PO holds right now, and Issue is a single committing action whose result is shown, not inferred.
   **Which project the commitment lands on (the parent that holds the reservation, or the
   requisition's generated sub-project) is undecided** (E09-S13, open point in Flow 8). Draw the card
   with the project as a variable and label it.
3. **Once issued, it is a record, not a draft.** PO-010 and ACC-005 (issued POs immutable; changes
   are numbered, approved change orders), PO-011 (preserve the prior version, identify changed fields
   and lines, recalculate budget effect, reapprove when policy requires), DATA-005 and PO-014 (the PDF
   reproduces from the stored snapshot and template version), PO-013 and ACC-006 (cancel or reduce
   releases only unused commitment, once, history kept). So an issued PO has no edit button; it has
   `Start Change Order`, which opens a versioned draft with a field-level diff and its budget effect.
4. **What the vendor sees is a separate, smaller thing than what we see.** PO-017, EML-005, COL-002:
   notes and attachments carry a visibility, internal content must never reach the vendor, and a test
   proves it. So every note and file on a PO says `Sent to Vendor` or `Internal` in words, the default
   is Internal, and the issue confirmation lists **what will be sent and what will not**.
5. **Sending is not arriving.** PO-004 and EML-006 (recipient, time, provider id, template version,
   snapshot version, result), PO-015 (failed transmissions visible, retryable, auditable, without
   duplicating the PO), EML-002 and EML-003 (bounces and complaints recorded against the contact; a
   suppressed address is never retried silently), OBS-004 (failed transmissions are a business
   monitor). So the PO carries a **transmission log**, the list has a `Needs Attention` tab, and a
   bounced send is a state with a next step (fix the contact, then resend the same version), never a
   red icon alone.
6. **Tax and freight are always visible on their own.** FIN-008 and PO-016 (per BU/OU policy,
   separately visible on line and document), FIN-009 (rounding rules central). Lines and totals show
   price, tax and freight as separate columns and totals; nothing is folded into a single amount.

**Not here:** goods receipt and fulfilment entry (M4, E14), invoices and payment (out of Phase 1),
REST or cXML transmission (later phases, PO-005), the vendor's side of anything.

## 3. Requirements (inlined)

| ID | Text |
|---|---|
| PO-001 | POs must be creatable from approved and sourced requisitions or authorized project items. |
| PO-002 | A PO must include requestor, Bill To, Ship To, vendor, items/services, descriptions, quantities, units, unit prices, currency, taxes, freight, terms, dates, project, department, and ledger accounts. |
| PO-003 | POs must follow a configurable approval workflow before issue. |
| PO-004 | Phase 1 must send approved POs by email and record recipient, time, delivery result, and the exact document version. |
| PO-006 | PO numbers must be unique within their configured organization/BU/OU sequence. |
| PO-007 | The lifecycle should support Draft, Approval Pending, Approved, Issued, Partially Fulfilled, Fulfilled, Closed, Cancelled, and Superseded. |
| PO-008 | Approval or issue must convert the applicable requisition reservation into a commitment atomically (plan §3.9: **issue**, not approval). |
| PO-009 | Partial POs must reduce only the corresponding open requisition quantity and value. |
| PO-010 | Issued POs must be immutable; changes must create a numbered, approved change order/version. |
| PO-011 | A change order must preserve the prior version, identify changed fields/lines, recalculate budget effects, and be reapproved when policy requires. |
| PO-012 | Duplicate clicks, retries, worker retries, and integration retries must not issue or transmit a PO more than once unintentionally. |
| PO-013 | Cancelling or reducing a PO must release the unused commitment exactly once and must not erase its history. |
| PO-014 | Generated PO PDFs must be reproducible from the stored PO snapshot and template version. |
| PO-015 | Failed email/API/cXML transmissions must be visible, retryable, and auditable without duplicating the PO. |
| PO-016 | Tax and freight must follow the applicable BU/OU expense policy and remain separately visible. |
| PO-017 | POs must support notes, terms, supporting attachments, and vendor-facing attachments. Internal-only content must never be included accidentally. |
| FIN-008, FIN-009 | Tax and freight configurable per organization or BU/OU; rounding, precision and line-versus-document rules central. |
| EML-001..003, 005, 006 | SPF, DKIM, DMARC verified before first send; bounces and complaints stored against the contact and shown on the transmission; hard bounce or complaint suppresses the address, never retried silently, PO shown undelivered and actionable; vendor email never includes internal content; every send records provider id, recipient, template version, snapshot id, result. |
| SEQ-001..003 | Numbers unique per scope, monotonic not gapless, format configurable and never reinterpreted. |
| COL-002 | Notes identify author and visibility, internal-only versus externally shareable. |
| ACC-002, ACC-005, ACC-006, ACC-008 | No duplicate issue or transmission on retry; a change creates a version and keeps the vendor-sent snapshot; cancel releases only unused funds, once; project-to-PO without a mouse. |

Roadmap stories: E13-S01 (PO from awarded lines, numbering), S02 (tax and freight), S03 (lifecycle),
S04 (approval before issue), S05 (issue commits atomically), S06 (immutable issued PO, change orders),
S07 (cancel or reduce releases once), S08 (PDF from snapshot), S09 (email via outbox, retry), S10
(deliverability), S11 (internal versus vendor-facing). **All engine or ledger stories; no PO UI story
exists.** The E13 money rules are blocked pending E09-S13 and the commit-target question; draw the
screens with server figures and labelled assumptions, never invented rules.

## 4. Decisions already made that apply

- §14 #5 and §3.9: requisition approval reserves, **PO issue commits**, confirmed actual expenses.
  Approval of a PO never commits.
- D-09 and D-10: every external send goes through the outbox (so the UI shows `Queued` before `Sent`);
  Resend's 100 a day cap means transactional PO sends always go immediately, notifications are digested.
- §14 #6: tax and freight are independent booleans per BU/OU, applied per document type, always
  separately visible.
- D-M2-7: a requisition's currency is the organization's base currency; only supplier prices may be in
  another currency. A PO in a vendor's currency is therefore possible, and how its commitment is valued
  is a money question (question 8).
- MASTER §2.3 already maps PO status: draft neutral, approval_pending info, approved info, issued
  success, partially_fulfilled info, fulfilled success, closed neutral, cancelled danger, superseded
  warning. Use it as written.
- MASTER §6.2 moment 5: the document lifts on `--shadow-drag`, a seal marks it, a trace runs to the
  vendor row as the transmission is queued; on failure the trace snaps back red and the retry
  affordance is already focused. Played once per session; reduced motion shows the finished picture,
  and the state is also words (§7.12).
- DECISIONS 56 (diffs in words with a struck "was"), 58 (dialogs focus the safe button), 45 (never
  disable submit), D-M2-8 (attachments are never shared externally until full scanning exists; so in
  M3 a vendor-facing *attachment* may be blocked from sending: question 5).

## 5. Proposed screens (for the operator to confirm)

Ranges: up to **400 POs per organization a year**, 1 to 40 lines, 1 to 4 change orders, 1 to 6
transmissions per PO (retries and change orders), PO totals from hundreds to tens of millions in the
base currency.

1. **PO list** (`/purchase-orders`). Question: *what is waiting on me, and did everything arrive?*
   Tabs `Drafts`, `Awaiting Approval`, `Ready to Issue`, `Issued`, `Needs Attention` (transmission
   failed, bounced, suppressed), `All`. Columns: `PO Number` (mono), `Vendor`, `Project`, `Total`
   (currency visible), `Status`, `Sent` (`Delivered 12 Oct`, `Bounced`, `Queued`, icon plus words),
   `Version` (`v2` when a change order exists). Header action `New PO` (Primary, starts from awards).
2. **Create from awarded lines** (`/purchase-orders/new`, also `Create PO` on an approved award).
   Question: *which awarded lines am I ordering now?* Awarded lines grouped by vendor with `Awarded`,
   `Already Ordered`, `Open` and an editable `Quantity to Order`; one PO per vendor; above-open
   refused at the field with the figure. Lines with no completed RFQ do not appear (SRC-018), and the
   empty state says why.
3. **PO editor** (draft, `/purchase-orders/:id/edit`). Question: *is this the document I want the
   vendor to receive?* Header per PO-002: requestor, Bill To, Ship To, vendor (snapshot, with
   eligibility), recipient contact, terms, dates, project, department, ledger accounts. Lines:
   description, quantity, unit, unit price, currency, **tax**, **freight**, line total, each money
   column on its own. A totals bar (subtotal, tax, freight, total) and a **budget preview**: `Issuing
   will commit 48,200.00 USD on Plant 4 Line Retrofit from its 50,000.00 USD reservation.` Notes and
   attachments with visibility in words on each (`Internal` by default). `Submit for Approval`
   (Primary) or `Approve` when no workflow applies.
4. **PO detail** (`/purchase-orders/:id`). Question: *where is this PO, what has it done to the budget,
   and what did the vendor get?* Lifecycle ordered list (Draft, Approval Pending, Approved, Issued,
   Partially Fulfilled, Fulfilled, Closed; Cancelled and Superseded beside it). Cards: **Order** (header
   and lines), **Budget** (reserved, committed, released, and the sentence), **Sent to Vendor** (the
   current version, the recipient, the latest transmission state, `View PDF`), **Versions and Change
   Orders**, **Notes and Attachments** (grouped `Sent to Vendor` and `Internal`), **History**, **Linked
   Records** (requisition, RFQ, award, ledger entries). Header actions by state: `Edit` (draft),
   `Issue` (approved), `Start Change Order` and `Cancel or Reduce` (issued), `Resend` (failed).
5. **Issue** (the confirmation, then signature moment 5). The confirmation lists recipient and contact,
   version and template, **what will be sent** (the PDF and the files marked `Sent to Vendor`) and
   **what will not** (`2 internal notes and 1 internal attachment stay here`), and the commitment it
   creates. Then the moment: frames before, mid-trace and after; the failure frame (trace snaps back,
   `Resend` focused); the reduced-motion end state; and the words that carry the same information
   (`Issued. Commitment posted. Email queued to orders@acme.example.`). A second click during issue
   does nothing visible except keep the same result (PO-012).
6. **Transmission log** (a card on detail, with a sheet per send). Per send: time, recipient, version,
   template version, provider message id, result (`Queued`, `Sent`, `Delivered`, `Bounced`,
   `Complained`, `Failed`), and bounce detail. A suppressed address says so and offers `Change
   Recipient`, which leads back to the vendor's contacts; `Resend` sends the same version again and
   says that it does (PO-015, EML-003).
7. **Change order** (`Start Change Order` on an issued PO). A draft `CO-2` against version 1: edit
   lines and fields, see the **field-level diff** (words and struck "was") and the **budget effect**
   (`Commitment rises by 3,100.00 USD`, signed, parenthesised when negative), whether reapproval is
   required and why, then submit. On approval, version 2 is issued and sent; version 1 stays readable
   with its PDF (ACC-005).
8. **Cancel or reduce** (dialog). States what is cancelled, how much unused commitment is released
   (once), what stays (history, PDF, transmissions), and whether the vendor is told. Destructive button
   per MASTER §7.13.
9. **PDF preview** (sheet). The rendered document from the snapshot, stamped with PO number, version,
   template version and issue date; internal content is absent by construction. Include one board of
   the PDF itself so the vendor-facing layout is designed, not left to the template author.
10. **PO decision** (an `ApprSheet` variant on the Approval Inbox page): PO summary, budget effect,
    source award and its justification, vendor eligibility, then the standard controls.
11. **Sending domain status** (small, under Administration): SPF, DKIM and DMARC checks with pass or
    fail in words and what to fix (EML-001), and the suppression list with the reason and date for
    each address. One board each; check whether an Administration page already exists for it.

## 6. States and edge cases to draw

Loading, empty (`No Purchase Orders Yet`), no match, error, partial (the transmission log fails to load
while the PO is current). Plus: **issue failed** (email bounced: status Issued, transmission
Undelivered, `Change Recipient`); **double click on Issue** (one result); **partial PO** (awarded 10,
ordered 6, open 4 visible on the requisition); a **change order that lowers** the commitment (a
negative, parenthesised, signed figure); a **cancelled PO** read later; a **foreign-currency PO**
(vendor currency on the document, base currency on the budget card, rate and date stated); a **vendor
suspended after issue** (issued PO unaffected, says so); **40 lines** at 375 (stacked lines, totals
pinned).

## 7. Open questions for the operator (ask before building)

Record answers in DECISIONS 6j. Anything still open goes to the page spec and to the M3 planner.

1. **Which project does a PO commit against:** the parent project that holds the requisition's
   reservation, or the sub-project the requisition generated? (E09-S13, Flow 8.) The budget card's
   wording depends on it.
2. **"Authorized project items"** (PO-001's second path): what are they, and how do they satisfy
   SRC-018 (no PO without a completed RFQ, D-16)? Proposal: not in M3; draw only the award path.
3. **Tax and freight entry:** per line, per document, or both, as the BU policy says? And rounding:
   per line or per document (FIN-009)? Proposal: entered per line, freight optionally per document and
   shared pro rata by line value (as DECISIONS 116 does for comparison).
4. **Bill To and Ship To:** where do the addresses come from (organization and BU/OU addresses exist?
   a new master list?), and may a buyer type a one-off Ship To?
5. **Vendor-facing attachments under D-M2-8:** attachments are never shared externally until full
   scanning exists. Does that block sending a vendor-facing attachment with a PO in M3, so only the PDF
   goes? Proposal: yes, the PDF only; the attachment shows `Not sent until scanning is available`.
6. **Which changes need reapproval** on a change order? Proposal: the requisition's material list
   reused (amount, project, ledger account, item, line count; D-M2-13).
7. **Fulfilment states:** goods receipt arrives in M4. Are `Partially Fulfilled` and `Fulfilled`
   reachable in M3 (manually), or drawn as future states only?
8. **A PO in a vendor's currency:** which rate values the commitment, and is it re-valued at change
   order? (Money; the board labels its assumption.)
9. **Who receives the PO email:** the vendor's `Service and Delivery` contact, per Vendors question 8;
   and is the requester copied?

## 8. Reuse map

| Need | Reuse |
|---|---|
| Lists, tabs, filters | `RequisitionList` |
| Lifecycle, detail cards, Linked Records | `RequisitionDetail` |
| Editor, required fields, totals bar, sticky actions | `RequisitionCreate` (`.total-bar .action-bar .summary`) |
| Budget card, reserved and committed | `ProjectDashboard` budget card and waterfall vocabulary, `ProjectLedger` |
| Diff and budget effect | `RequisitionResubmit`, `ApprSheetChanges` |
| Signature moment frames and reduced-motion end state | `TransferPosting`, `TrMoment*` (moment 3) |
| Attachments, visibility, scan states | `DetailFiles` |
| Decision panel variant | `ApprSheet*` |
| Dialogs, result panel | `WdDialog*`, `ApprSheetDone` |

## 9. Tests to leave behind (`purchase-orders.spec.mjs`)

Happy path from an approved award to an issued PO, keyboard-only (ACC-008 end to end with the
Sourcing page); above-open refusal; tax and freight in their own columns and totals on every board;
**no element marked Internal inside the PDF board or the "will be sent" list** (planted violation: an
internal note placed in the PDF must fail); issue frames and reduced-motion end state carry the same
words; failure frame focuses `Resend`; change order diff in words with the signed budget effect;
cancel dialog states the released figure once; transmission states each have icon and words; status
pills from MASTER §2.3's PO map; 375 with 40 lines and no sideways scroll; axe in light and dark on
every board.
