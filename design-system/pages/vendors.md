# Page spec: Vendors (list, profile, request, questionnaires, onboarding decision)

**Stories:** the back-end stories E11-S01 to S07 exist; **no vendor UI story exists**, so the UI stories named in `design-system/canvas/STORY-NOTES.md` section 7 must be raised and must cite this file. **Read first:** `design-system/MASTER.md`, then this file. Layout reference: canvas page `Vendors` (60 boards; in the **second canvas**, `design-system/canvas-2/project/`, moved from the first on 14 Oct 2026; `vendors.spec.mjs` is in `design-system/canvas-2/tests/`) and two boards on `Approval Inbox` (`ApprSheetVendor`, `ApprSheetVendorLow`). The canvas is a drawing; where it disagrees with MASTER or this file, the canvas is stale.

Requirements: VEN-001 to VEN-015, ORG-003, CUS-008, PRIV-001, OBS-004, COL-004, COL-005, AUD-006, A11Y-003, A11Y-004, UX-002, UX-004, UX-005.

**MASTER was amended on 13 Oct 2026** (section 9): the vendor status map is MASTER §2.3, the `signal` and `eligibility` components are §7.14, the masked value is §7.15. Implement them from MASTER; this file keeps the page rules.

---

## 1. Routes and purpose

| Route | Purpose | Roles |
|---|---|---|
| `/vendors` | Who can I use, and what needs my attention? | `vendor.view` and above |
| `/vendors/:id` | Is this vendor usable for me, and is its record sound? | `vendor.view`; edit controls need `vendor.edit` or `vendor.approve` |
| `/vendors/new` | How do I get this supplier approved? | any employee (VEN-004) |
| `/admin/vendor-questionnaires` | What do we ask new vendors, and how is it scored? | organization administrators (VEN-006) |
| decision panel (Approval Inbox) | Decide a vendor creation, activation, scope change, suspension or reactivation | the assigned approver (VEN-005) |

Vendors is an **Operate** surface in the Commit phase (hue 196): phase colour only on the nav icon, the active nav marker and the 2px rule under the page header. Never on a control, row, number or status.

## 2. Rules

- **Eligibility is derived, never typed** (VEN-012). It is worked out for one unit from status, approval, each required document and that unit's availability, and always shown with its reasons in words (`Not eligible in Plant 4: Insurance Certificate expired 2 Oct`). The server computes it; the page shows it. An expired required document makes a vendor not eligible and **does not change its status** (decision 6h-3).
- **Status and eligibility are different things and look different.** Status is a pill with an icon and words from a closed set: `Draft`, `Pending Approval`, `Active`, `Suspended`, `Inactive`, `Rejected`. Eligibility is a `signal` (icon plus words) with a reasons list. A vendor with one expired certificate stays `Active` and reads `Not Eligible`.
- **A signal is an icon plus words.** The icon takes a status tone; the words never depend on it (A11Y-004, TAG-003). Greyscale test: eligible and not eligible differ by icon and words.
- **Global vendors can be locally disabled, never locally rewritten** (VEN-008, VEN-009, ORG-003). `Disable Here` and `Enable Here` take effect at once and need no approval (decision 6h-5); they need `vendor.edit` on that unit and the organization setting `Allow Local Disable`. Scope changes (`Change Availability`), `Suspend` and `Reactivate` go for approval (VEN-005) and the vendor stays as it is until approved.
- **Tax and banking are masked by default** (VEN-014, CUS-008, PRIV-001, AUD-006). The page shows the last four characters. `Reveal Values` is offered only to vendor managers and finance users; it is **audited** (`Revealed by you, 14:02. Logged.`), re-masks on leaving the card or after 60 seconds, and the values never appear in exports, logs, the page title or the DOM before the reveal. Everyone else sees `Restricted` and who to ask. **The value must be fetched on Reveal, not shipped in the page**; the canvas keeps sample values in its own script only so it can simulate the reveal, and `vendors.spec.mjs` checks the rendered markup, not the script.
- **New vendors arrive by request** (VEN-004). The request form runs a **live duplicate check** on the tax id and configured unique ids (VEN-010). A match is reported at the field; it names the existing vendor only to someone who may see it. The second record is blocked and `Use Existing Vendor` is offered; a vendor manager may override with a reason (audited; decision 6h-7).
- **Documents expire on a calendar** (VEN-011, OBS-004). The list and the profile look ahead: `1 Expiring in 12 Days` before `1 Expired`. Required types not yet supplied are rows that say `Missing`, never absent rows. A replaced document keeps its previous versions. Attachments use the scan states of `DetailFiles` and are never shared externally (D-M2-8).
- **Performance keeps its provenance** (VEN-007, VEN-015). Every score shows its source PO, scorer email, period and rationale. A dispute is an **appended row**; an adjustment shows the struck original. Nothing is edited in place.
- **History is append-only** (VEN-013). Linked RFQs, awards and POs keep the vendor as it was when they were made, and the Linked Records card says so.
- **Contacts:** `Billing` and `Service and Delivery` are always present (VEN-002). The `Service and Delivery` contact receives RFQs and POs and `Billing` receives nothing in M3, with a per-contact `RFQs and POs` checkbox as the override (decision 6h-8).
- **People inside the organization are shown by email** (D-M2-16). Vendor contacts are not users and carry names.
- **Submit and Save are never disabled to signal an error** (DECISIONS 45). A failed submit gives a focused summary (`role=alert`) of `Category: link` entries and keeps typed input. Status-changing dialogs focus the safe button, state the consequence and whether it can be undone, and ask a reason (DECISIONS 58).
- **No vendor portal** in v1: vendors never sign in; the buyer keys everything in. No recommendation engine: the system marks facts only.

## 3. Screens

### 3.1 Vendor list `/vendors`

Tabs `Active`, `Pending Approval` (pending and drafts), `Needs Attention` (expiring, expired, missing or rejected documents), `Inactive` (suspended, inactive, rejected), `All`, each with a count. Search on trading name, legal name and vendor number; **never on a tax id** (masked data is not a search key). Filters: availability, documents. Columns: `Vendor` (trading name, then number and legal name), `Status`, `Eligible Here` (signal, first reason on the next line), `Availability`, `Documents` (signal), `Score` (with period), `Last Used`. Server paging (PERF-005). `Request Vendor` is the one Primary; `Export` excludes sensitive fields (CUS-008). Below 900px card width `Availability`, `Score` and `Last Used` stack with their column names.

### 3.2 Vendor profile `/vendors/:id`

Header: trading name, number and legal name, status pill, and the **eligibility line for the viewer's unit** with its reasons. Actions by permission: `Edit`, `Change Availability`, `Suspend` (Destructive, danger text and icon; MASTER 7.13) or `Reactivate` (Primary). Cards, in the dashboard layout rule (equal heights per row, tiers by content width): `Profile` (with a closed `Onboarding Record` fold), `Availability`, `Contacts`, `Tax and Banking`, `Documents` (table with `Open` per row), `Performance` (overall, four categories, `Open Scorecard`), `History`, `Linked Records`. A pending vendor viewed by its requester is read-only and says where it is in approval.

### 3.3 Request a vendor `/vendors/new`

Cards: Company, Identifiers (duplicate check), Where It Applies (global or selected units), Contacts, Questionnaire (typed fields rendered from the published version; weights are **never** shown to the requester), Documents. Sticky action bar: `Save Draft` and `Submit for Approval`, with `Required fields are still empty` / `All required fields complete` from the required-field mark (MASTER 7.11). Submitted state names the approver and what happens next.

### 3.4 Onboarding decision (Approval Inbox)

A variant of the decision panel, not a second approval UI. Order (DECISIONS 65): key facts, `Open the full vendor`, **Onboarding Evidence** (questionnaire score against the threshold with the per-question breakdown behind a fold, duplicate check result, documents with their verification, missing documents), Decision Trail, comment, `Why It Reached You` fold. A score below the threshold is a **fact, not a block** (decision 6h-6); Approve stays enabled.

### 3.5 Questionnaire administration `/admin/vendor-questionnaires`

A versioned list (draft, published, retired) and an editor: each question has type (text, number, yes or no, single choice, multiple choice, date, file), required, weight and points per answer. A live `Weights total N of 100` signal. **Reordering is a pair of move buttons per row** (keyboard first, A11Y-003); drag is an optional second path and is not required. Publishing is refused with a focused summary when weights do not total 100 and the draft is kept; the publish dialog says a published version never changes and requests keep the version they were answered against (CUS-007). `Preview of the Request Form` is a closed fold.

### 3.6 Document sheet, status and scope dialogs, scorecard sheet

- **Document sheet** (side sheet): file, scan state, issue and expiry, verification, reviewer, earlier versions; `Verify`, `Reject` (reason required, shown to the uploader), `Replace File`. Missing: says so and what it blocks.
- **Dialogs** (`Suspend`, `Reactivate`, `Change Availability`, `Disable Here`, `Enable Here`): intro, consequence rows (open RFQs, issued POs, approval, undo), required reason, safe button focused, confirm named for the action (`Request Suspension`, `Disable in Plant 4`). The danger fill is on the confirm button of a confirmation dialog only (MASTER 7.13).
- **Scorecard sheet** (wide): rows of PO, category, score, rationale, scorer, `Dispute`; `Add Score` opens an inline form (category, score, source order, rationale; all required).

## 4. States

Every screen has loading (skeletons at final height), empty, partial (one card or one column fails, the rest stays current), error (what happened, what was kept, how to recover) and all five widths. Additional: a required document `Missing`; an eligible vendor disabled in the viewer's unit; a suspended vendor with open RFQs; a duplicate found during a request; restricted and revealed tax cards; a long legal name at 375; a document expiring today; weights not totalling 100.

## 5. Eligibility component (Design System sheet, section `s-vendors`)

One signal, four contexts: the profile header (with reasons), the list cell (first reason below, `and N more`), the RFQ invitation picker (`Not Eligible`, the reason, and `Override` for the authorized with a required reason), and the PO vendor field. The RFQ picker is capped at five vendors (DECISIONS 106). The Sourcing and Purchase Orders pages reuse the component and the same server rule.

## 6. Keyboard and accessibility

Tabs follow the arrow, Home and End keys. Every control is reachable in logical order with visible focus; dialogs and sheets trap focus, close on Escape and return it to the opener. Reordering uses move buttons with names that include the question. Money, if shown, is tabular with the currency code. Status is never colour alone. 44px targets below 1024. axe passes in both themes on every board (`vendors.spec.mjs`).

## 7. Open questions (park in the E11 follow-up story; the canvas shows each assumption)

1. **Required document types and where they are configured.** Assumed: an organization list, each `required` or `optional`, optionally per vendor category (decision 6h-2). The per-category part is not drawn.
2. **Minimum order value.** Not captured now (decision 6h-9); it stays open with the award screen (DECISIONS 119).
3. **Reveal timeout and who may reveal.** Assumed: vendor managers and finance; 60 seconds; auditors read the access log, not the value (decision 6h-4). Confirm the timeout.
4. **Vendor category list and the per-category scoring categories** beyond quality, pricing, delivery and service (VEN-007 says configurable).
5. **Drag reordering** of questionnaire rows is not drawn; the keyboard buttons are the full path.
6. **Questionnaire versions in flight:** drafts started on version 3 finish on version 3 (assumed in the publish dialog).
7. **Decision panel footer at 375** wraps its five buttons into two rows and the shell's top bar overlaps the sheet's close button (shared component, all document kinds). Follow-up on the Approval Inbox page.
8. **Profile length:** eight cards, about 2,100px at 1440. Revisit when Performance and History gain content.
9. **375 list tab strip** wraps `Pending Approval` and `Needs Attention` onto two lines; a scrolling tab strip would fix it for every list.
10. **Earlier boards show people by name** (`ApprovalInbox`); only the vendor rows use email (D-M2-16). A later pass should align them.

## 8. Tests the stories must leave behind

Unit tests on the eligibility rule (status, approval, each required document expired, missing or rejected, unit scope, local disable, never auto-changing status); a **TEN-010 isolation case** (a foreign tenant's vendor is a 404 on the profile, its documents and its scorecard); an authorization test per role (`vendor.view`, `vendor.edit`, `vendor.approve`, finance reveal, requester read-only) including that a hidden control is also refused by the server; a **masking test that the full tax id and bank account are absent from the API response, the rendered DOM, exports and logs until an audited reveal**, with a planted violation; a duplicate-identifier test (blocked, override with reason, audited); an expiry look-ahead test; a state-machine test for the six statuses and the approval-gated transitions; an append-only test for scores and disputes; the questionnaire weight rule and version immutability; Playwright with `@axe-core/playwright` on each route, a keyboard-only path through list, profile, request and questionnaire editor, and the `MASTER.md` §8 checklist.

## 9. MASTER amendment (done 13 Oct 2026)

1. **§2.3 `vendor` status map** is in MASTER (`draft` neutral, `pending_approval` info, `active` success, `suspended` warning, `inactive` neutral, `rejected` danger). **`apps/web/src/components/status/map.ts` still carries the E04-S05 vendor map** (`pending`, `expired`, no `draft` or `rejected`), which contradicts decision 123 (an expired document never changes status). The first vendor UI story replaces it and `labels.ts`; the old `expired` key is removed, not kept.
2. **§7.14 `signal` and `eligibility`** and **§7.15 masked value** are in MASTER (classes `.sig`, `.sig-why`, `.elig`, `.mask`).
3. **§7.13** is approved and its dark-mode values are now measured in MASTER; this page uses `Destructive` and the confirm-fill exactly as written.
