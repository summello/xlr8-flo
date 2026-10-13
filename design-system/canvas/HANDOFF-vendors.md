# Handoff: Vendors canvas page

**Status 13 Oct 2026: NOT BUILT.** First of the three M3 pages. Read `HANDOFF-m3.md` first: it holds
the shared direction, the rules, the build loop and the impeccable self-review. This file holds what
is specific to vendors. The operator confirms the screen list and answers the questions in section 7
before any board is made.

New canvas page: **`Vendors`** (`{"id":"vendors","name":"Vendors"}` in `project/canvas.json`), board
prefix `Vendor*` for main boards and `Ven*` for variants and states.

---

## 1. Job and audience

| Who | Arrives because | Needs to leave with |
|---|---|---|
| **Vendor Manager** (role `vendor-manager`: `vendor.view/create/edit/approve`) | maintains the vendor master: onboarding, documents, scope, suspension | a vendor list they trust: who is usable where, what is about to expire |
| **Sourcing Specialist** (`vendor.view` only) | needs vendors to invite to an RFQ | to know at a glance whether a vendor may be invited, and why not |
| **Requester / any employee** | wants a new supplier used | to submit an onboarding request without knowing the master-data rules |
| **Approver** | a vendor creation, activation, scope change, suspension or reactivation waits in their inbox (VEN-005) | enough evidence on one panel to decide: score, duplicates, documents |
| **Finance / Auditor** | tax and banking verification, history | the protected data when authorized, and an audit trail of who saw it |

Mode **Operate**. Common work, which must take two actions or fewer from the vendor list: open a
vendor; see why a vendor is not invitable; upload or verify a document; start an onboarding request.

## 2. The reasoning: from requirement to screen

The vendor module has one job in the money loop: **decide which companies money may go to, and keep
that decision explainable.** Everything below follows from that.

1. **A vendor's usability is derived, not typed.** VEN-012 says an inactive, suspended, expired or
   unapproved vendor must not be invited or selected without an authorized override. "Expired" is a
   property of the vendor's *documents* (VEN-011), "unapproved" of its *approval* (VEN-005),
   "inactive/suspended" of its *status*, and availability depends on the *BU/OU* asking (VEN-008,
   VEN-009). So the screen must show one derived answer, **Eligible / Not eligible here**, and always
   name the reasons beside it. This **eligibility signal** is the most reused thing on this page: the
   RFQ invitation picker (Sourcing page) and the PO vendor field consume it. Design it as a component.
2. **Status and eligibility are different things and must look different.** Status is the vendor's
   lifecycle (a §2.3-style closed vocabulary: pill, icon, words). Eligibility is a computed answer for
   a context (a line of text with an icon and its reasons). Merging them would make a vendor with one
   expired insurance certificate look "suspended", which is false and would push people to edit the
   status to fix a document problem.
3. **Global vendors are shared; local units can only say no.** ORG-003, VEN-008, VEN-009 and §14 #12:
   a global vendor may be **locally disabled** by a BU/OU where policy allows, **never locally
   rewritten**. So the availability card shows the global record once and a per-unit list of local
   disables (who, when, why), and the edit controls on a global vendor are not offered to a BU-only
   user at all.
4. **Sensitive data is masked by default and revealed on purpose.** VEN-014 (field-level access and
   masking for tax and banking), CUS-008 (excluded from exports and logs), PRIV-001 (collect only what
   is needed), AUD-006 (banking data never in logs). The tax and banking card shows a masked value to
   everyone who may see that the field exists, a deliberate `Reveal` (audited, re-masked on leaving)
   to those authorized, and `Restricted` with who to ask to everyone else. This is the page's quiet
   signature: it should feel careful, not alarming.
5. **New vendors arrive by request, not by form-filling in the master.** VEN-004 (anyone can request),
   VEN-006 (configured questionnaire with typed fields, required answers, weights and scoring),
   VEN-005 (approval), VEN-010 (duplicate tax and other identifiers detected). So onboarding is a
   request form with a live duplicate check and a score the approver can read, not a vendor-create
   screen.
6. **Documents expire on a calendar, so the page must look ahead.** VEN-011 (issue date, expiry,
   verification status, reviewer, expiry notifications) and OBS-004 (expired vendor documents are a
   business monitor). The list needs a documents column that says `Expiring in 12 days` before it says
   `Expired`, and a filter to find them.
7. **Performance needs provenance.** VEN-007 (quality, pricing, delivery, service, configurable
   categories) and VEN-015 (every score keeps source transaction, scorer, period, rationale, dispute
   and adjustment history). A score without its source is an opinion; the scorecard therefore lists
   the scored transactions, and a dispute is an appended row, never an edit.
8. **History is append-only.** VEN-013: changing a vendor never rewrites what an RFQ, award or PO
   recorded. The profile's Linked Records card says so in its caption, as the requisition detail does
   (REQ-009).

**What is deliberately not here:** a vendor portal (none in v1); vendor-side document upload; payment
or AP screens (out of scope for Phase 1); a vendor "recommendation"; bulk vendor import (an
`imports` template may come later, not drawn now).

## 3. Requirements (inlined)

| ID | Text |
|---|---|
| VEN-001 | The system must maintain a simplified, searchable vendor list for sourcing and PO generation. |
| VEN-002 | Each vendor must support billing and service/delivery contacts; additional typed contacts may be added. |
| VEN-003 | Vendor profiles must store required tax, incorporation, insurance, certification, banking-verification, and other configured documents as protected attachments. |
| VEN-004 | Users must be able to submit a new-vendor onboarding request. |
| VEN-005 | Vendor creation, activation, scope changes, suspension, and reactivation must support approval. |
| VEN-006 | Administrators must be able to configure onboarding questionnaires with typed fields, required answers, weights, and scoring. |
| VEN-007 | Vendor performance must be measurable across quality, pricing, delivery/time adherence, service, and configurable categories. |
| VEN-008 | Vendor availability must be global or restricted to selected BU/OUs. |
| VEN-009 | Global vendors may be locally disabled where policy allows, without deleting global history. |
| VEN-010 | Vendor tax identifiers and other configured unique identifiers must be checked for duplicates. |
| VEN-011 | Required documents must support issue date, expiry date, verification status, reviewer, and expiry notifications. |
| VEN-012 | A vendor that is inactive, suspended, expired, or not approved must not be invited or selected unless policy explicitly permits an authorized override. |
| VEN-013 | Vendor changes must not rewrite the vendor snapshot stored on historical RFQs, awards, or POs. |
| VEN-014 | Sensitive vendor data, including tax and banking information, must receive field-level access control and masking where appropriate. |
| VEN-015 | Performance scores must retain source transaction, scorer, period, rationale, and any dispute/adjustment history. |
| ORG-003 | Vendor availability must support global and BU/OU-local scope. |
| CUS-008 | Sensitive custom fields must support field-level authorization and exclusion from exports/logs. |
| PRIV-001 | Collect only personal and sensitive data required for stated business purposes. |
| OBS-004 | Business monitoring must expose stuck approvals, unreconciled balances, expired vendor documents, and failed transmissions. |
| COL-004/005 | Attachments keep filename, type, size, checksum, uploader, time, scan status; uploads are allow-listed, validated, size-limited, scanned. |
| UX-002, UX-004, UX-005 | Destructive actions name consequence and target; distinct loading, empty and failure states; errors say what happened, what was kept, how to recover. |

Roadmap stories these screens serve: E11-S01 (profile, list, scope, local disable), S02 (contacts),
S03 (documents, expiry), S04 (field-level masking), S05 (onboarding questionnaire and scoring), S06
(duplicates, approval of create/activate/suspend/reactivate), S07 (performance with disputes). **All
seven are back-end stories; no vendor UI story exists.** Name the UI stories this page needs in
`STORY-NOTES.md` when you finish.

## 4. Decisions already made that apply

- Vendor scope (§14 #12, `docs/claude-plan.md` §1.1): **a global vendor may be locally disabled, never
  locally rewritten.**
- No vendor portal in v1 (operator, 8 Oct 2026).
- At most 5 vendors per RFQ (DECISIONS 106): the eligibility component is used inside a picker that
  caps at five.
- Approvals reuse the inbox: a vendor decision is a new **variant of the decision panel**
  (`ApprSheet*` boards; DECISIONS 6d), with the routing explanation, separation of duty and stale
  version handling already designed. Do not design a second approval UI.
- Attachments reuse the scan states drawn on project detail (`DetailFiles`) and D-M2-8: files that
  pass basic checks are downloadable by authorized users and **never shared externally**.
- People are shown by email (D-M2-16); vendor contacts are records with names.
- Never disable submit; summary with focus (45). Destructive and status-changing dialogs focus the
  safe button and say what happens and whether it can be undone (58). Diffs use words and a struck
  "was" (56).

## 5. Proposed screens (for the operator to confirm)

Each screen: 1440 plus 1920, 1024, 768, 375, and loading, empty, partial and error. Realistic ranges:
a tenant holds **50 to 2,000 vendors** (reference load: 3,000 RFQs and 4,000 POs across 10 tenants),
2 to 8 contacts each, 3 to 12 documents each, names up to 80 characters, legal names longer than
trading names.

1. **Vendor list** (`/vendors`). Question it answers: *who can I use, and what needs my attention?*
   Tabs `Active`, `Pending Approval`, `Needs Attention` (expiring or expired documents, failed
   verification), `Inactive` (suspended, inactive, rejected), `All`, each with a count. Search on name,
   trading name and vendor number (never on a full tax id: masked data is not a search key for people
   who cannot see it). Columns: `Vendor` (trading name, legal name beneath), `Status` (pill),
   `Availability` (`Global`, or `3 Units`, with locally disabled units counted in words), `Documents`
   (`All Current`, `1 Expiring in 12 Days`, `1 Expired`, icon plus words), `Score` (overall, with the
   period), `Last Used`. Server paging (PERF-005). Header action `Request Vendor` (Primary) for anyone;
   `Export` for vendor managers (sensitive fields excluded, CUS-008).
2. **Vendor profile** (`/vendors/:id`). Question: *is this vendor usable for me, and is its record
   sound?* Header: trading name, vendor number, status pill, and the **eligibility line for the
   viewer's own unit** (`Eligible in Plant 4` or `Not eligible in Plant 4: insurance certificate
   expired 2 Oct`). Header actions by permission: `Edit`, `Suspend` / `Reactivate`, `Change
   Availability`. Then the dashboard layout rule with cards:
   - **Profile** (wide): legal and trading name, registered and remit-to addresses, default currency,
     payment term, minimum order value if captured (see question 9).
   - **Availability** (narrow): `Global` or the unit list; each local disable with who, when, why;
     `Disable Here` for an authorized BU user on a global vendor.
   - **Contacts**: typed list, `Billing` and `Service and Delivery` always present (VEN-002), others
     added by type; each with name, role, email, phone; which one receives RFQs and POs is shown, not
     assumed (see question 8).
   - **Documents**: table of type, issue date, expiry, verification status (`Unverified`,
     `Verified by <email>`, `Rejected`), scan state; required types not yet supplied appear as rows
     that say `Missing`, not as absent rows.
   - **Tax and Banking**: masked values (`•••• 4821`), `Reveal` for the authorized (audited, re-masks
     on leaving the card or after a short time), `Restricted. Ask a Vendor Manager.` for others. Never
     in exports or the page title.
   - **Performance**: overall score and per-category scores for the period, the last scored
     transactions, `Open Scorecard`.
   - **Onboarding**: questionnaire version, score against threshold, approval outcome (for vendors
     created through onboarding).
   - **History** and **Linked Records** (RFQs, awards, POs), with the caption that historical
     documents keep the vendor as it was when they were made (VEN-013).
3. **Request a vendor** (`/vendors/new`, the onboarding request). Question: *how do I get this
   supplier approved?* Sections: company identity, identifiers (tax id and configured unique ids),
   availability requested (global or units), contacts, the **configured questionnaire** (rendered from
   its typed fields; required answers marked per MASTER §7.11), documents. **Live duplicate check** on
   each identifier (VEN-010): a match says so at the field (`Matches an existing vendor`), names it only
   if the requester may see it, and offers `Use Existing Vendor` instead of a second record. Submit
   starts approval (VEN-005); never disabled; summary on failure. A saved draft is allowed.
4. **Onboarding decision** (an `ApprSheet` variant on the Approval Inbox page, linked from here). What
   the approver needs and nothing else: requester, company, **questionnaire score against threshold
   with the per-question breakdown behind a fold**, duplicate check result, documents supplied and
   their verification, requested availability, then the standard decision controls. A score below
   threshold is shown as a fact, not as a block (see question 6).
5. **Questionnaire administration** (`/admin/vendor-questionnaires`, organization administrators).
   Question: *what do we ask new vendors, and how is it scored?* A versioned list (draft, published,
   retired) and an editor: questions with type (text, number, yes/no, single choice, multiple choice,
   date, file), required, weight, and scoring per answer; a live total of weights; a preview of the
   request form. Reordering by keyboard (move up and down) with drag as the second path (A11Y-003).
   Publishing makes a new version; requests keep the version they were answered against (CUS-007).
6. **Document sheet** (side sheet from the Documents card). Upload or replace, issue and expiry dates,
   verification (`Verify`, `Reject` with reason, by a vendor manager), reviewer and time, scan state,
   previous versions. Replacing a document never deletes the old one.
7. **Status and scope changes** (dialogs). `Suspend`, `Reactivate`, `Change Availability`, `Disable
   Here`: each states the **consequence** (`2 open RFQs and 1 issued PO name this vendor. Open RFQs
   will show it as not eligible; issued POs are not changed.`), asks for a reason, and says whether the
   change takes effect now or goes for approval (VEN-005). The result is a status with icon and words,
   plus a history row.
8. **Scorecard** (`/vendors/:id/performance`, or a sheet; your call). Categories (quality, pricing,
   delivery, service, configured ones), each score with source transaction (a PO link), scorer email,
   period, rationale; `Add Score` for authorized scorers; `Dispute` appends a dispute row with the
   vendor's or buyer's position and an adjustment if granted; nothing is edited in place (VEN-015).
9. **Eligibility component** (Design System sheet). The line and its reasons in four contexts: the
   profile header, the list row tooltip, the RFQ invitation picker (eligible, not eligible with
   reasons, `Override` for the authorized with a reason field), and the PO vendor field. Greyscale
   test: eligible and not eligible must differ by icon and words, not by colour.

## 6. States and edge cases to draw

Loading, empty (`No Vendors Yet`, with `Request Vendor`), no match, error, partial (one card fails,
others current). Plus: a vendor with **no documents** but required types configured (rows say
`Missing`); a vendor **eligible globally but disabled in the viewer's unit**; a vendor **pending
approval** viewed by its requester (read-only, with where it is in approval); a **suspended** vendor
with open RFQs; a **duplicate** found during request; the **restricted** tax card; a **revealed** tax
card (after reveal, the audit note `Revealed by you, 14:02. Logged.`); a **long legal name** (80
characters) at 375; a **document expiring today**.

## 7. Open questions for the operator (ask before building)

Record each answer in DECISIONS 6h. Anything still open after the build goes to the page spec and to
the M3 planner's parking story under E11.

1. **Vendor status vocabulary.** MASTER §2.3's map has no `vendor` entry. Proposal: `draft`,
   `pending_approval` (info), `active` (success), `suspended` (warning), `inactive` (neutral),
   `rejected` (danger). Adding the map is a MASTER amendment, which is yours to approve.
2. **Which documents are required, and where is that configured?** Proposal: an organization list of
   document types, each `required` or `optional`, optionally per vendor category.
3. **Does an expired required document make the vendor not eligible automatically?** VEN-012 says
   "expired"; proposal: yes, eligibility is derived, and the status does not change.
4. **Who may reveal tax and banking data?** Proposal: vendor managers and finance users only, each
   reveal audited, never auditors by default (PRIV-001).
5. **Who may disable a global vendor locally, and is approval needed?** VEN-009 says "where policy
   allows". Proposal: an organization setting `Allow Local Disable`; a user with `vendor.edit` on that
   unit may do it; no approval.
6. **Onboarding score below threshold: block or inform?** Proposal: inform the approver; approval
   decides.
7. **Duplicate identifier: block or allow with override?** Proposal: block a second record, offer
   `Use Existing Vendor`; a vendor manager may override with a reason (audited).
8. **Which contact receives RFQ invitations and POs?** Proposal: the `Service and Delivery` contact
   gets RFQs and POs, the `Billing` contact gets nothing in M3; per-vendor override.
9. **Minimum order value.** The comparison page left "split award versus vendor minimum order value"
   open (DECISIONS 119). Should the vendor carry an optional minimum order value now, so the award
   screen can warn later?
10. **Who scores performance, and when?** Goods receipt arrives in M4 (E14). Proposal for M3:
    purchasing users score manually per PO and period; disputes by vendor managers.

## 8. Reuse map

| Need | Reuse |
|---|---|
| List, tabs, filters, counts, paging | `RequisitionList`, `ProjectList` (`.tabs .cols-*`) |
| Detail layout and cards | `RequisitionDetail`, `ProjectDetail` (dashboard layout rule) |
| Request form, required marks, summary, sticky bar | `RequisitionCreate` (`.form-grid .summary .action-bar`), MASTER §7.11 |
| Attachments and scan states | `DetailFiles` on the Project Dashboard page |
| Decision panel variant | `ApprSheet*` on the Approval Inbox page |
| Versioned admin list and editor, keyboard reorder | `WfList*`, `WorkflowBuilder` outline mode |
| Dialogs, history timeline, result panel | `WdDialog*`, `RequisitionDetail` History, `ApprSheetDone` |
| Side sheet with focus trap | `LedgerSheet`, `CmpSheet*` |

## 9. Tests to leave behind (`vendors.spec.mjs`)

Happy path (request, approve via the inbox variant, profile), keyboard-only path through list,
profile, request and questionnaire editor (including reorder without a mouse), every state in
section 6, eligibility reasons always in words, masked values never present in the DOM text before
`Reveal` (a planted violation: a board that renders the full tax id must fail), status pills from one
map with icons, `Missing` rows for required documents, the 375 layout with no sideways scroll, axe in
light and dark on every board.
