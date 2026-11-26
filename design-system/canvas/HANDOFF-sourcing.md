# Handoff: Sourcing (the RFQ screens not yet drawn)

**Status 13 Oct 2026: PARTLY BUILT.** Second of the three M3 pages. Read `HANDOFF-m3.md` first. **The
comparison dashboard is done and must not be redrawn:** `RfqComparison` and its 20 `Cmp*` boards,
page spec `design-system/pages/rfq-comparison.md`, DECISIONS 6g (104 to 120), `comparison.spec.mjs`.
This handoff covers only what surrounds it: getting lines into an RFQ, inviting, publishing and
amending, keying bids, scoring, and the award draft that `Start Award` hands off to.

**Before proposing anything, list the boards on the `RFQ Comparison` page** and check each screen in
section 5 against them. If a screen already exists on the canvas, drop it from the proposal and say so.

Canvas page: the existing `RFQ Comparison` page. **Question for the operator:** rename it `Sourcing`
now that it holds the whole RFQ flow (its id `rfq-comparison` stays, so nothing links break). New
boards use the prefix `Rfq*` for main boards and `Src*` for variants.

---

## 1. Job and audience

| Who | Arrives because | Needs to leave with |
|---|---|---|
| **Sourcing Specialist** (`rfq.create/edit/view/approve`, `vendor.view`) | an approved requisition is in `Sourcing in Progress` | a published RFQ to eligible vendors, bids keyed, a defensible award |
| **Evaluator** | asked to score an RFQ | scores with comments, a conflict-of-interest declaration on record |
| **Sourcing lead** (role to confirm, question 7) | a compliance waiver, a late-bid override or an ineligible-vendor override needs authority | a decision with a reason, audited |
| **Award approver** | an award waits in the inbox (APR-003: awards are approval documents) | the award, its justification and its scope check on one panel |
| **Requester** | wants to know where their request is | the requisition detail showing sourced, awarded and remaining per line (already drawn) |

Mode **Operate**. Common work, two actions or fewer: from a requisition, start an RFQ; from the RFQ
list, open an RFQ; from an RFQ, key a bid; from the comparison, start the award (built).

## 2. The reasoning: from requirement to screen

Sourcing turns an approved request into a **defensible choice of vendor at a known price**. Every
screen exists to make one of those three words true.

1. **Approved lines are the only way in, and the open quantity is a hard edge.** SRC-001 (RFQ from
   approved requisition lines), SRC-003 (never source more than the approved open quantity or amount
   unless the requisition is amended), SRC-018 (no award, PO or financial effect without a completed
   RFQ), REQ-014 and REQ-015 (split sourcing and partial awards; per-line requested, sourced, awarded,
   ordered, cancelled, remaining, already drawn on `RequisitionDetail`). So RFQ creation is a **line
   picker over open quantities**, not a blank form, and a quantity above the open amount is answered
   at the field with the figure and the way out (`Amend the requisition to source more`).
2. **An invitation is a promise to the vendor, so eligibility is checked when it is made.** VEN-012
   and DECISIONS 106 (at most 5 vendors). The invitation picker consumes the eligibility component
   from the Vendors page; a not-eligible vendor shows its reasons and, for an authorized user only,
   an `Override` with a reason.
3. **After publication, changes are versions, and everyone invited is told.** SRC-004 (Draft,
   Published, Closed, Under Evaluation, Awarded, Cancelled) and SRC-005 (versions after publication
   retained and communicated to all affected invitees). So a published RFQ cannot be edited in place;
   `Amend` creates version N+1 with a diff, a reason and a list of who will be told, and the detail
   page shows per invitee which version they were sent.
4. **Bids are keyed by the buyer, and their timing matters.** No vendor portal in v1. SRC-006 (line
   prices, currency, tax, freight, lead time, validity, exceptions, attachments, submission
   timestamp), SRC-008 (late bids blocked or accepted only through an audited override), SRC-007 and
   DECISIONS 108 and 118 (sealed: no figure for anyone before the deadline). A keyed bid has two
   times: when the vendor sent it and when it was keyed. Which one decides lateness is an open
   question (question 3), and the screen must show both.
5. **The original bid is never altered.** SRC-010 and DECISIONS 117: conversion shows rate, source and
   date and never changes the vendor's figure. The bid entry form keeps the vendor's currency; it does
   not offer a converted field.
6. **Scoring is separate from price, and conflicts are declared first.** SRC-011 (qualitative and
   quantitative criteria, comments, conflict-of-interest declarations where configured). An evaluator
   declares before seeing anything to score; a declared conflict recuses them from that RFQ. Scores
   stay sealed until the deadline like everything else (DECISIONS 118).
7. **The award is a draft that has to pass three checks before it can be approved.** SRC-012 (split by
   line or quantity), SRC-013 (record vendor, lines, amount, rationale, evaluator results, exceptions,
   approval history), SRC-014 (non-lowest justification when configured), SRC-016 (never above approved
   scope or available reserved funds without an approved amendment). The comparison's `Start Award`
   carries a selection (Lowest Cost Supplier or Best Average Cost, DECISIONS 104, 114); the award draft
   is where each line's vendor and quantity is confirmed, every non-lowest line is justified, and the
   **scope and reserved-funds check** is shown with its figures. The money rules behind that check are
   **blocked** in the roadmap (E12-S08, pending E09-S13): draw the guard and its refusal, with server
   figures, and label the release behaviour as an open question rather than inventing it.
8. **Cancelling keeps everything.** SRC-017 (cancel or supersede preserves history and reconciles the
   budget effect), SRC-015 (everything stays linked to the requisition), WF-005 (cancellation
   propagates only through defined relationships). Cancel and supersede are dialogs that state the
   consequence for invitees, bids and the requisition's open quantities.

**Not here:** the comparison (built); vendor-facing bid submission (no portal); PO creation (Purchase
Orders page); approval-workflow configuration (Approval Workflow Builder page, already built).

## 3. Requirements (inlined)

| ID | Text |
|---|---|
| SRC-001 | Sourcing users must be able to create an RFQ from approved requisition lines. |
| SRC-002 | An RFQ must contain items/services, quantities, specifications, delivery requirements, response deadline, terms, attachments, and invited vendors. |
| SRC-003 | The system must prevent sourcing more than the approved open quantity or amount unless the requisition is amended. |
| SRC-004 | RFQs must support Draft, Published, Closed, Under Evaluation, Awarded, and Cancelled states. |
| SRC-005 | RFQ versions and changes after publication must be retained and communicated to all affected invitees. |
| SRC-006 | Vendor responses must capture line prices, currency, tax, freight, lead time, validity, exceptions, attachments, and submission timestamp. |
| SRC-007 | Bids must remain confidential from other vendors and from unauthorized internal users. |
| SRC-008 | Late bids must be blocked or explicitly marked and accepted only through an audited override. |
| SRC-010 | Currency conversion used for comparison must show rate, source, and effective date and must not alter the vendor's original bid. |
| SRC-011 | Evaluators must be able to score qualitative and quantitative criteria with comments and conflicts-of-interest declarations where configured. |
| SRC-012 | Awards may be split by line or quantity across vendors. |
| SRC-013 | An award must record selected vendor, awarded lines, amount, rationale, evaluator results, exceptions, and approval history. |
| SRC-014 | A non-lowest-price award must require an explicit justification when configured. |
| SRC-015 | All RFQs, responses, comparisons, and awards must remain linked to the requisition. |
| SRC-016 | Award totals must not exceed approved requisition scope or available reserved funds without an approved amendment. |
| SRC-017 | Cancelling or superseding an RFQ/award must preserve the complete history and reconcile its downstream budget effect. |
| SRC-018 | Every expenditure line must complete RFQ sourcing before final award, PO submission, or financial effect unless a future, explicitly approved sourcing-exemption policy is introduced. |
| VEN-012 | Inactive, suspended, expired or unapproved vendors are not invited or selected without an authorized override. |
| VEN-013 | Vendor changes do not rewrite the vendor snapshot on RFQs, awards or POs. |
| REQ-014 | Split sourcing and partial awards must be supported without exceeding approved quantity or amount. |
| APR-003 | Approval workflows cover RFQs and Awards. |
| WF-005 | Cancellation propagates only through defined relationships and never erases history. |
| ACC-008 | A complete project-to-PO workflow is operable without a mouse. |

Roadmap stories: E12-S01 (RFQ from lines, open-quantity guard), S02 (content, invitations, eligibility),
S03 (lifecycle, versions), S04 (bid capture), S05 (confidentiality, late bids), S07 (scoring, conflict
of interest), S08 (award, blocked on money), S09 (cancel or supersede, blocked on money). S06 and S10
are the comparison (engine, and the built screen). **Only S10 is a UI story;** STORY-NOTES §6 item 4
already asks for an award UI story. Name the rest when you finish.

## 4. Decisions already made that apply

DECISIONS 6g in full, especially: 104 and 114 (the two award bases), 105 (`Start Award` is a hand-off,
never disabled, refused over scope with the figure), 106 (5 vendors; compliance pass or fail per
requirement; base currency is the organization's), 108 and 118 (sealed for everyone until the
deadline, opens by itself), 116 (freight pro rata by line value), 117 (rate warning after 7 days,
refresh by an `rfq.edit` holder, amended 27 Oct 2026), 119 (ties go to the earlier submission, buyer override with reason;
compliance requirements belong to the RFQ, copied from a template; waiving one needs a reason and the
sourcing lead, audited), 120 (colour has three jobs: win, worth a look, failure; density by closed
folds). Also: no vendor portal; approvals reuse the inbox decision panel; D-M2-7 (only supplier prices
may be in another currency); never disable submit (45); dialogs focus the safe button (58).

## 5. Proposed screens (for the operator to confirm)

Ranges: an organization runs up to **300 RFQs a year**, 1 to 40 lines each, 1 to 5 invitees, 0 to 5
bids, 1 to 3 versions, 1 to 6 evaluators.

1. **RFQ list** (`/sourcing/rfqs`). Question: *what is open, what is due, what needs me?* Tabs
   `Drafts`, `Open` (published, deadline ahead), `Evaluating` (closed or under evaluation), `Awarded`,
   `All`. Columns: `Number` (mono), `Title` with the requisition(s) beneath, `Status`, `Invited`
   (`4 of 5`), `Bids` (`3 Received`; before the deadline only the count, never figures), `Deadline`
   (absolute date and `in 2 days` / `closed 3 days ago`), `Owner`. Header action `New RFQ` (Primary).
2. **Start an RFQ from approved lines** (`/sourcing/rfqs/new`, also opened from `Start RFQ` on a
   requisition in `Sourcing in Progress`). Question: *which open lines am I sourcing, and how much of
   each?* A table of approved lines with `Requested`, `Already Sourced`, `Open` and an editable
   `Quantity to Source`, prefilled with the open quantity; above-open answered at the field with the
   figure and the amend path (SRC-003). Selecting lines from several requisitions is shown only if
   the operator allows it (question 2).
3. **RFQ editor** (draft, `/sourcing/rfqs/:id/edit`). Question: *what exactly am I asking for, and from
   whom?* Sections in order of the vendor's reading: items and quantities with specifications;
   delivery requirements and needed-by; response deadline (date, time and time zone stated); terms;
   **compliance requirements** (copied from a template, editable per RFQ, DECISIONS 119);
   attachments with **visibility shown on every file** (`Sent to Vendors` or `Internal`, default
   Internal); **invited vendors** through the eligibility picker (cap of 5, reasons in words, override
   for the authorized). `Publish` (Primary) opens a confirmation that lists what each invitee receives
   and which files stay internal.
4. **RFQ detail** (`/sourcing/rfqs/:id`). Question: *where is this RFQ, and who has what?* Lifecycle as
   an ordered list (Draft, Published, Closed, Under Evaluation, Awarded; Cancelled as a terminal shown
   beside it), the same component as the requisition lifecycle. Cards: **Request** (lines, linked
   requisitions, deadline), **Invitees** (vendor, contact emailed, version sent, delivery state of the
   invitation email, bid received yes or no and when), **Versions** (v1, v2 with the reason and
   `Sent to 4 of 4`), **Bids** (sealed: who has submitted, never a figure, until the deadline; then a
   link to the comparison), **Evaluators** (declared, scored, recused), **History**, **Linked
   Records**. Header actions by state: `Edit` (draft), `Amend` and `Extend Deadline` (published),
   `Open Comparison` (after the deadline), `Cancel RFQ`.
5. **Amend a published RFQ.** The editor in amend mode: what changed as a diff (words and a struck
   "was", DECISIONS 56), a required reason, and **who will be told** (every invitee, by contact).
   Extending the deadline is the most common amendment; give it its own two-action path from the
   detail header.
6. **Key a bid** (side sheet or page from the Invitees card, `Record Bid`). Question: *did I capture
   exactly what the vendor sent?* Header: vendor (snapshot), RFQ version answered, **received at**
   (when the vendor sent it) and **keyed at** (system). Lines in the vendor's currency: unit price,
   quantity quoted (or `Not Quoted`), tax, freight, lead time; then validity, exceptions (free text),
   attachments (the vendor's original document is expected), compliance answers (`Met` / `Not Met`
   per requirement). If received after the deadline: the bid is **blocked** with the reason and an
   `Accept Late Bid` override for the authorized (reason required, audited, DECISIONS 108), never a
   silent acceptance. Totals shown in the vendor's currency only; conversion belongs to the
   comparison (SRC-010).
7. **Evaluation** (`/sourcing/rfqs/:id/evaluation`, per evaluator). First a **conflict-of-interest
   declaration** (`I have no conflict` or `I have a conflict`, with a description; a conflict recuses
   the evaluator, and the RFQ owner sees it). Then the criteria with weights (read-only to evaluators),
   a score per vendor per criterion, and a comment per score. Sealed until the deadline: before it the
   page says when scoring opens and shows nothing to score. The comparison's `Scoring` tab (built)
   is the read side of this screen.
8. **Award draft** (`/sourcing/rfqs/:id/award`, the hand-off from `Start Award`). Question: *is this
   award complete, justified and within scope?* Per line: awarded vendor, awarded quantity (a line may
   split across vendors by quantity, SRC-012), landed amount, `Lowest` marker or `Not the Lowest` with a
   **justification field** for each such line (SRC-014). A summary with the basis used, total against
   **approved scope** and against **reserved funds** (SRC-016), exceptions, and evaluator results.
   Over scope: refused with the figure and the two ways out (change the selection, or amend the
   requisition), never a disabled button. `Submit for Approval` (Primary). Label on the board, as an
   assumption: what happens to the reserved amount the award does not use (question 6).
9. **Award decision** (an `ApprSheet` variant on the Approval Inbox page). The approver sees: basis,
   per-vendor totals, non-lowest lines with their justifications, scope check, compliance gaps and
   waivers, evaluator summary, then the standard decision controls.
10. **Cancel or supersede** (dialogs from the RFQ detail and the award). Each states the consequence
    in words: invitees who will be told, bids that stay on record, what happens to the requisition's
    sourced quantities (they return to open), and the budget effect as an open question while E12-S09
    is blocked. History is kept; the cancelled RFQ remains readable.
11. **Requisition detail, sourcing entry point** (a one-line change on `ReqDetailSourcing`): `Start
    RFQ` as the header action for sourcing roles, and RFQ and award rows in Linked Records. Check
    whether the existing boards need it or only the spec.

## 6. States and edge cases to draw

Loading, empty (`No RFQs Yet`), no match, error, partial. Plus: an RFQ with **no eligible vendors**
in the picker; the **sixth invitee** refused with the limit in words; a **published RFQ amended
twice** (invitees on different versions until told); an invitation **email bounced** (the invitee row
says so and offers to change the contact); a **late bid** before and after override; a bid with
**one line not quoted**; an evaluator who **declared a conflict**; an award **over scope**; an award
with **three non-lowest lines** (justifications required, the summary counts them); a **cancelled**
RFQ read after the fact.

## 7. Open questions for the operator (ask before building)

Record answers in DECISIONS 6i. Anything still open goes to the page spec and to the M3 planner.

1. **RFQ status vocabulary.** MASTER §2.3 has none for RFQs or awards. Proposal for RFQs: `draft`
   (neutral), `published` (info), `closed` (info), `under_evaluation` (info), `awarded` (success),
   `cancelled` (danger); for awards: `draft`, `approval_pending`, `approved`, `superseded` (warning),
   `cancelled`. A MASTER amendment is yours to approve.
2. **May one RFQ carry lines from several requisitions?** SRC-001 says "requisition lines". Proposal:
   yes, when they share a BU/OU and currency.
3. **Which time decides lateness for a keyed bid:** when the vendor sent it (taken from the email,
   entered by the buyer, with the email attached as evidence) or when it was keyed? Proposal: the
   vendor's sent time, entered and evidenced, and the keyed time always recorded beside it.
4. **Who is the sourcing lead** who waives compliance requirements and overrides late bids and
   ineligible vendors (DECISIONS 119)? The baseline roles have no such role. Proposal: `rfq.approve` on
   the RFQ's unit (held by sourcing specialists today); or a new role.
5. **Does every award go through approval,** or only when an award workflow is published (as for budget
   changes, D-M2-11)? Proposal: only when one is published; otherwise the award is approved on submit.
6. **Reserved funds the award does not use.** If an award comes in under the requisition's reservation,
   is the difference released at award, at PO issue, or when the requisition completes? This is the
   money rule E12-S08 is blocked on (with E09-S13). The board shows it as an assumption only.
7. **Split award versus vendor minimum order value** (DECISIONS 119, still open). Proposal: warn on the
   award draft when a vendor's awarded total is under its minimum (needs Vendors question 9).
8. **Is "Non-lowest justification" always required, or configured?** SRC-014 says "when configured".
   Proposal: an organization setting, on by default.

## 8. Reuse map

| Need | Reuse |
|---|---|
| Lists, tabs, counts | `RequisitionList` |
| Lifecycle ordered list, detail cards | `RequisitionDetail` (`aria-current="step"`, done in words) |
| Line table with requested, sourced, awarded, remaining | `RequisitionDetail` Lines (decision 50) |
| Editor, required fields, summary, sticky bar | `RequisitionCreate` |
| Diff with struck "was", resubmit pattern | `RequisitionResubmit`, `Resub*` |
| Vendor eligibility in the picker | the component from the Vendors page |
| Bid sheet layout, late and partial pills, conversion note | `CmpSheet*`, `CmpSheetLate`, `CmpSheetForeign` |
| Award basis cards, scope pill, over-scope refusal | `RfqComparison`, `CmpOver`, `CmpAverage` |
| Decision panel variant | `ApprSheet*` |
| Dialogs and result panel | `WdDialog*`, `ApprSheetDone` |

## 9. Tests to leave behind (extend `comparison.spec.mjs` or add `sourcing.spec.mjs`)

Happy path from a requisition to a published RFQ to keyed bids to the award draft, keyboard-only;
open-quantity refusal at the field; sixth invitee refused; ineligible vendor shows reasons and only an
authorized override; amend produces a version and a diff and names every invitee; **no figure in the
DOM before the deadline on any new board** (planted violation: a sealed board that renders a price
must fail); late bid blocked, then accepted only with a reason; conflict declaration precedes scoring;
award over scope refused with the figure; non-lowest lines demand a justification; axe in light and
dark on every new board.
