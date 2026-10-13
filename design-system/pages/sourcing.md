# Page spec: Sourcing (RFQ list, new RFQ, editor and amend, detail, record a bid, evaluation, award draft, award decision)

**Stories:** E12-S01 (RFQ from lines, open-quantity guard), S02 (content, invitations, eligibility), S03 (lifecycle, versions), S04 (bid capture), S05 (confidentiality, late bids), S07 (scoring, conflict of interest), S08 (award, **blocked on money**), S09 (cancel or supersede, **blocked on money**). S06 (engine) and S10 (comparison screen) are built: `design-system/pages/rfq-comparison.md`, which this page does not redraw. **Only S10 is a UI story today;** the UI stories this page needs are in `design-system/canvas/STORY-NOTES.md` section 8 and must cite this file. **Read first:** `design-system/MASTER.md`, then this file, then `pages/vendors.md` for the eligibility signal. Layout reference: canvas page `Sourcing` (the comparison boards plus 85 new boards) and two boards on `Approval Inbox` (`ApprSheetAward`, `ApprSheetAwardWhy`). The canvas is a drawing; where it disagrees with MASTER or this file, the canvas is stale.

Requirements: SRC-001 to 008, 010 to 018, VEN-012, VEN-013, REQ-014, REQ-015, APR-003, WF-005, ACC-008, A11Y-003, A11Y-004, UX-002, UX-004, UX-005.

**MASTER was not amended.** Needs an operator-approved amendment before code: the RFQ and award status maps (§2.3 has none), see section 10.

---

## 1. Routes and purpose

| Route | Purpose | Who |
|---|---|---|
| `/sourcing/rfqs` | What is open, what is due, what needs me? | `rfq.view` |
| `/sourcing/rfqs/new` (also `Start RFQ` on a requisition in `Sourcing in Progress`) | Which open lines am I sourcing, and how much of each? | `rfq.create` |
| `/sourcing/rfqs/:id/edit` | What exactly am I asking for, and from whom? (draft) | `rfq.edit` |
| `/sourcing/rfqs/:id` | Where is this RFQ, and who has what? | `rfq.view` |
| `/sourcing/rfqs/:id/amend` | What changed, why, and who will be told? (published) | `rfq.edit` |
| bid sheet on the detail | Did I capture exactly what the vendor sent? | `rfq.edit` |
| `/sourcing/rfqs/:id/evaluation` | Declare any conflict, then score | evaluators |
| `/sourcing/rfqs/:id/award` | Is this award complete, justified and within scope? | `rfq.approve` |
| decision panel (Approval Inbox) | Decide the award | the assigned approver (APR-003) |

Sourcing is **Operate** mode in the Commit phase: phase colour only on the nav icon, the active nav marker and the 2px rule under the page header.

## 2. Rules

- **Approved lines are the only way in, and the open quantity is a hard edge** (SRC-001, SRC-003, SRC-018, REQ-014, REQ-015). The line picker shows `Requested`, `Already Sourced`, `Open` and an editable `Quantity to Source` that starts at the open quantity. Above open is refused **at the field** with the figure and the way out (`40 are open on REQ-00418. Amend the requisition to source more`). The server enforces it under a lock; the figure on screen is advisory.
- **One RFQ may carry lines from several requisitions** when they share business unit or operating unit and currency (decision 144). A line that does not is shown disabled with the reason (`Another business unit`, `Different currency`).
- **An invitation is a promise to the vendor, so eligibility is checked when it is made** (VEN-012). The picker uses the vendor eligibility signal from `pages/vendors.md` unchanged. A not-eligible vendor shows its reasons; **`Override` is offered only to a user with `rfq.approve` on the RFQ's unit** (the sourcing lead, decision 146), asks for a reason and is audited. **At most 5 vendors per RFQ** (DECISIONS 106): the sixth is refused in words (`Up to 5 vendors per RFQ. Remove one to invite <vendor>.`), **even with an override**; the cap is enforced on invitation, not only shown.
- **After publication, changes are versions and everyone invited is told** (SRC-004, SRC-005). A published RFQ is never edited in place. `Amend` makes version N+1 with a diff (words and a struck `was`, DECISIONS 56), a **required reason** and the list of who will be told, by contact. The detail page shows per invitee which version they were sent and `Not yet told of version 3`. Extending the deadline is the most common amendment and has its own two-action path from the header; **a deadline can be extended, never brought forward**.
- **Bids are keyed by the buyer** (no vendor portal in v1). Two times are shown on every keyed bid: **when the vendor sent it** (entered by the buyer from the email, which is attached as evidence; this time decides lateness, decision 145) and **when it was keyed** (system, never changes). A bid sent after the deadline is **blocked** with the reason; it can be accepted only by a user with `rfq.approve` on the unit, with a reason, audited (SRC-008), and is shown as `Late, Accepted By Override` everywhere.
- **The original bid is never altered** (SRC-010). The bid sheet keeps the vendor's currency and shows totals in it only; conversion is the comparison's job, with its rate, source and date.
- **Sealed means sealed** (SRC-007, DECISIONS 108, 118): before the deadline no price, term, score or count of requirements is on any page for anyone, only who has submitted (`3 Received, sealed`). A test fails if a bid figure appears.
- **Scoring is separate from price, and a conflict is declared first** (SRC-011). The evaluation shows nothing to score until the evaluator declares. A declared conflict recuses them from that RFQ and is shown to the RFQ owner. Scores are sealed from everyone but their author until the deadline.
- **The award is a draft that passes three checks before it can go for approval** (SRC-012 to 014, SRC-016): per line the awarded vendor and quantity (a line may split by quantity); a **justification for every line that is not the lowest** (an organization setting, on by default, decision 150); and the scope and reserved-funds check with its figures. **Over scope is refused with the figure and the two ways out** (change the selection, amend the requisition); the submit button is never disabled.
- **An award goes through approval only when an award workflow is published** (decision 147). Otherwise the page says `No award workflow is published. This award is approved when you submit.` and the button reads `Approve and Award`. This is a financial-control rule the operator owns; the notice makes it visible.
- **Reserved funds an award does not use are released once, when the award is approved** (decision 148). Drawn as an assumption only (`Assumption, not yet a rule`); the engine rule is E12-S08 and is blocked on E09-S13.
- **Cancelling or superseding keeps everything** (SRC-017, WF-005). Each dialog states the consequence for invitees, bids, the requisition's open quantities and the budget; history is kept and the cancelled RFQ stays readable. The budget effect is an assumption while E12-S09 is blocked.
- **People inside the organization are shown by email** (D-M2-16). Vendor contacts are records with names.
- **Submit and Save are never disabled to signal an error** (DECISIONS 45): a failed submit gives a focused summary (`Category: link`) and keeps typed input. Destructive dialogs focus the safe button and state the consequence and whether it can be undone (DECISIONS 58, MASTER 7.13).
- **Colour has three jobs on the comparison** (DECISIONS 120); on this page status and price position are icon plus words (`Lowest`, `Not the Lowest`).

## 3. Status vocabulary (proposal, not in MASTER)

RFQ: `draft` (neutral), `published` (info), `closed` (info), `under_evaluation` (info), `awarded` (success), `cancelled` (danger). Award: `draft` (neutral), `approval_pending` (info), `approved` (success), `superseded` (warning), `cancelled` (danger). Icon plus words everywhere; a closed set.

## 4. Screens

1. **RFQ list.** Tabs `Drafts`, `Open`, `Evaluating`, `Awarded`, `All`, each with a count. Columns `Number`, `Title` (requisition beneath), `Status`, `Invited` (`4 of 5`), `Bids` (a count, `3 Received, sealed`; never a figure), `Deadline` (date and `in 2 days` or `closed 3 days ago`), `Owner`. `New RFQ` is the one Primary.
2. **New RFQ from approved lines.** A table grouped by requisition. `Create RFQ Draft` is the one Primary and never disabled.
3. **RFQ editor (draft) and amend.** Cards: Items and Specifications, Delivery Deadline and Terms, Compliance Requirements (copied from a template, editable per RFQ, DECISIONS 119), Attachments (visibility on every file, `Internal` by default), Invited Vendors (eligibility picker). `Publish RFQ` opens a confirmation listing what each invitee receives and which files stay internal. Amend adds `What Changed` and `Who Will Be Told`.
4. **RFQ detail.** Lifecycle as an ordered list (`aria-current="step"`, done in words; Cancelled is terminal and shown as a banner). Cards: Request, Bids (sealed state or a link to the comparison), Versions, Evaluators, Invitees (version sent, invitation email state, bid state, `Record Bid`), History, Linked Records. Header actions by state: `Edit` (draft), `Extend Deadline`, `Amend`, `Cancel RFQ` (published), `Open Comparison` (after the deadline), `View Award`, `Supersede Award` (awarded).
5. **Record a bid** (wide side sheet from the Invitees card). Vendor snapshot, version answered, `Vendor Sent It On` with evidence, `Keyed By Us`, lines in the vendor's currency with `Not Quoted`, validity, freight, exceptions, compliance `Met` or `Not Met` per requirement. Late: blocked panel with `Accept Late Bid` for the authorized.
6. **Evaluation.** Conflict declaration first; then a fold per vendor with a score and a comment per criterion (weights read-only); `Save for Later` and `Submit Scores`. Sealed state: `Scoring Opens at the Deadline`.
7. **Award draft.** Awarded lines, `Scope and Funds Check`, `Exceptions`. The comparison's `Start Award` hands off to it with its basis.
8. **Award decision** (Approval Inbox variant). `Award Evidence` in decision-panel order (DECISIONS 65): basis, by vendor, scope and funds, not the lowest with reasons, exceptions and evaluators, then the standard decision controls.
9. **Dialogs:** cancel, extend deadline, supersede award, publish, each with consequence rows and a required reason where it changes history.
10. **Requisition detail:** `Start RFQ` as the header action for a requisition in `Sourcing in Progress` (the existing `Linked Records` already lists RFQ rows).

## 5. States

Every screen has loading (skeletons at final height), empty, partial (one card or column fails, the rest stays current), error (what happened, what was kept, how to recover) and all five widths. Also: no approved lines; no eligible vendors; sixth invitee refused; override with a reason; a published RFQ amended (invitees on different versions); invitation email bounced (`Change Contact`); late bid before and after override; a line not quoted; evaluator recused; award over scope; award with three non-lowest lines; cancelled RFQ read after the fact.

## 6. Keyboard and accessibility

Tabs follow the arrow, Home and End keys. Every control is reachable in logical order with visible focus; dialogs and sheets trap focus, close on Escape and return it to the opener. The invitee picker and the line picker are checkbox rows, not drag. Money is tabular with the currency code. Status is never colour alone. 44px targets below 1024. axe passes in both themes on every board (`sourcing.spec.mjs`). ACC-008: a complete project-to-PO workflow is operable without a mouse; this page contributes the whole RFQ-to-award leg.

## 7. Open questions (park in the E12 follow-up story; the canvas shows each assumption)

1. **Reserved funds an award does not use** (decision 148): released at approval, once, is the assumption. The money rule is E12-S08, blocked on E09-S13.
2. **Budget effect of cancelling or superseding** (SRC-017): the reservation stays with the requisition is the assumption. E12-S09 is blocked.
3. **Split award versus vendor minimum order value** stays open (DECISIONS 119, decision 149): the vendor carries no minimum (6h-9).
4. **Is `rfq.approve` enough for the sourcing lead on every unit**, including a lead who is also the RFQ's author? Separation of duty on an override is not drawn.
5. **Evaluators and weights:** who sets the criteria and weights, and whether weights are per RFQ or per template, is not drawn (the page shows them read-only).
6. **Deadline time zone** is a select with one zone; a per-unit default zone is not drawn.
7. **Attachment scan failure** on an RFQ file is not drawn beyond the scan label.
8. **Shared decision-panel footer at 375** wraps its buttons (see `pages/vendors.md` section 7).

## 8. Tests the stories must leave behind

Happy path from a requisition to a published RFQ, keyed bids, evaluation and the award draft, keyboard-only. Unit tests on the open-quantity guard (under, equal, above; several requisitions; different currency refused). A TEN-010 isolation case (a foreign tenant's RFQ, bid and award are 404). An authorization test per role (`rfq.create`, `rfq.edit`, `rfq.approve`, evaluator, requester), including that a hidden `Override` or `Accept Late Bid` is also refused by the server. Eligibility: ineligible vendor refused without an override, sixth invitee refused even with one, override audited. Versioning: amend produces a version, a diff and names every invitee. **A sealed-bid test that no figure leaves the server before the deadline for any role**, with a planted violation. Late bid blocked, then accepted only with a reason and audited; lateness uses the vendor's sent time. Conflict declaration precedes scoring and recuses. Award: over scope refused with the figure; non-lowest lines demand a justification; the unused reservation released once; idempotent submit. Playwright with `@axe-core/playwright` on each route and the `MASTER.md` §8 checklist.

## 9. Not here

The comparison (built: `pages/rfq-comparison.md`), a vendor portal (none in v1), PO creation (the Purchase Orders page), approval workflow configuration (the Approval Workflow Builder page).

## 10. Needs an operator-approved MASTER amendment

The RFQ and award status maps (section 3). The eligibility signal and `.sig` are already proposed in `pages/vendors.md` section 9; this page adds no new component.
