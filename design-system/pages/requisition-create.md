# Page spec: Requisition create and edit (with the submission validation summary)

**Story:** E09-S11 (requisition create and edit, validation summary, list, detail). **Read first:** `design-system/MASTER.md`, then this file. Layout reference: canvas boards `RequisitionCreate`, `ReqCreate*` (states) in `design-system/canvas/project/`, on the Requisition Create page of the design canvas. Shared patterns (field, summary, notice, money format, tabs, states) are in `design-system/canvas/DECISIONS.md` and the Design System sheet; the validation summary and error behaviour follow `design-system/pages/sign-up.md` section 2.

Requirements: REQ-001 to 003, 005 to 007, 010, 011, 013, 015, FIN-006 (lock before validating funds, enforced server side), A11Y-006, UX-004, UX-005, UX-007.

**Decided by the operator (9 Oct 2026): a requisition that exceeds the funding available cannot be submitted.** See section 6.

---

## 1. Routes

| Route | Screen |
|---|---|
| `/requisitions/new` | Create |
| `/requisitions/:id/edit` | Edit a draft (same screen) |

Only a draft is editable. A submitted requisition is changed through return, amend and resubmit (E09-S08, not designed here).

## 2. Layout

Inside the app shell, breadcrumb `Requisitions / New Requisition`. A page header (`h1` is the title once saved, `New Requisition` until then; subline `Draft REQ-00431 · saved 14:06 · amounts in USD`) and a `Back to requisitions` link.

Two columns from 1100px of content width, one column below:
- **Main (8/12):** card **Requisition Details**, card **Lines**.
- **Side (4/12):** cards **Funding Check**, **Catalogue Suggestions**, **Attachments**.
- **Action bar:** sticky at the bottom of the page (`.action-bar`, `--z-sticky`, `--surface`, top border `--border-strong`), full page width: left a status line (`Draft saved 14:06`, `role=status`), right `Save draft` and `Submit for approval` (primary). It stays in view while the page scrolls.

These are form cards, not dashboard cards: no hover outline, no equal-height rule.

## 3. Requisition Details (REQ-010)

| Field | Spec |
|---|---|
| Requisition for | Radio group (`fieldset` + `legend`): `An existing project` (default) / `A standalone capital request`. |
| Standalone note | When standalone is chosen, an info note: `Approval creates a new project for this request unless the approver assigns it to an existing one.` (REQ-004) |
| Title | Text, required. |
| Project | Select, shown for an existing project, required. **A project that was itself created from a requisition is listed but disabled**, with `(created from REQ-00277)` in its label (REQ-003). |
| Business unit | Select, shown instead of Project when standalone, required. |
| Department | Select, required. |
| Ledger account | Select, required. An **inactive** account stays visible on an old draft with `(inactive)` in its label and fails validation (REQ-011 active master data). |
| Needed by | Date input, required, not in the past. |
| Delivery location | Select, required. |
| Currency | Read-only, `USD`. (Open: multi-currency, section 7.) |
| Requestor | Read-only, the signed-in user. |
| Justification | Textarea, required, 88px minimum, resizes vertically. |

Fields are in an auto-fit grid (220px minimum), Title and Justification span the row. 32px controls, 44px below 1024.

## 4. Lines (REQ-006, REQ-007)

Toolbar: `Add item line`, `Add service line`, caption `Items and services are tagged separately.` A **line count** (`4 lines`) in the card header.

One table, one row per line, each cell an input:

| Column | Spec |
|---|---|
| Type | Select `Item` / `Service`. The tag is explicit and always visible (REQ-006). |
| Description | Text, required. |
| Qty | Right-aligned mono, whole number, required, greater than zero. |
| Unit | Text (`ea`, `day`). Hidden between 660 and 899px card width, returns in the stacked row. |
| Unit Price | Right-aligned mono, decimal, required, greater than zero. USD. |
| Line Total | Computed, never typed. Whole amount first, decimals and currency lighter and smaller. |
| Remove | Icon button, label `Remove line N`. |

Every input has an `aria-label` that names its column and line (`Quantity, line 3`). Below 660px card width each row becomes a stacked card whose cells show their column name (`data-label`).

A total bar under the table: `Estimated total`, 20px mono, currency visible. **Money is held in whole cents in the UI** and as `Decimal`/`NUMERIC(18,4)` on the server; never `float`. A test must prove `3 × 0.10` is `0.30`.

Empty state: `No Lines Yet` / `Add an item or a service line, or pick one from the catalogue suggestions.`

## 5. Side cards

**Funding Check.** For an existing project: `Available Now`, `This Requisition`, `Available After` (negative in parentheses, red, with the word `Exceeds` in the pill), a meter showing the estimate against what is available (`role=img` with the figures in its label), a pill `Within the available funds` / `Exceeds the available funds` (icon + words), and `Approval Route` with the explanation (`Amara Okafor, step 1 of 1. Over 250,000.00 USD adds a second approver.`). The figure and the route come from the server on every change (debounced); the numbers on the sample board are illustrative. For a standalone request: `Funding is assigned when a standalone request is approved. Nothing is reserved until then.`

**Catalogue Suggestions (REQ-007, REQ-008).** A search field `Find in the catalogue`; under it the caption `Previously requested, sourced or bought within your scope.` and up to four rows: name (520 weight), `Item · bought 12 Mar 2026 from Voltek Drives`, `18,500.00 USD per ea`, and an `Add` button labelled `Add <name>`. Add appends a line with quantity 1 and the last price, tagged from the catalogue's kind. Suggestions respect the user's BU/OU scope; an unauthorised item never appears. No match: `Nothing in the catalogue matches. Add the line by hand.`

**Attachments.** `Upload file`, then a list: icon tile, name, `PDF · 310 KB · Dev Patel`, and a scan pill (`Clean`, `Scanning`, `Quarantined`, each with its icon). Quarantined or scanning files cannot be opened. Same states and copy as the project detail attachments tab. Empty: `No files yet. Quotes and drawings help the approver. Each file is scanned before anyone can open it.`

## 6. Submit, validation and the funds block

`Submit for approval` runs the checks of REQ-011 on the server (the client repeats them for speed). **Never disable the button to signal an error**: it stays enabled, and the failure is explained.

On failure the page shows a summary at the top (`role=alert`, `tabindex=-1`, **focus moves to it**): title `Fix N things before you submit`, line `Nothing was submitted. Everything you entered is kept.`, then one entry per problem, each `Category: link text`, where the link focuses the field or line. Categories and exact messages:

| Category | Example message |
|---|---|
| Required field | `Enter a title` · `Choose a business unit` · `Enter the date it is needed by` · `The needed-by date is in the past` · `Explain why it is needed` |
| Master data | `Ledger account 6140 is inactive. Choose an active account` |
| Project | `The chosen project cannot take a requisition` (a requisition-generated sub-project, REQ-003) |
| Lines | `Add at least one line` · `Line 3 needs a description` / `a quantity` / `a unit price` |
| Funds | `Over the available funds by 20,000.00 USD. Reduce the estimate or ask for a budget transfer` |
| Routing | `No approver is set for <unit> above <amount>. Ask an administrator` (REQ-011 approval routing) |

Each failed field also shows its own error under it (icon + text, `aria-invalid`, `aria-describedby`), and the Lines errors appear on the row.

**Funds block (decision).** A requisition whose estimate exceeds the funding available **cannot be submitted**. There is no override, no "submit anyway", no approver bypass on this screen. The Funding Check card shows the shortfall as soon as the lines change (`This is 20,000.00 USD over the funds available. A requisition over its funding cannot be submitted. Reduce the estimate, or ask for a budget transfer first.`), and submitting still produces the Funds entry in the summary. The server enforces it independently: it takes the `FOR UPDATE` lock on the funding row, validates, then reserves, in one transaction (FIN-006); the UI figure is advisory and may be stale. A race that lets two submissions pass the advisory check must still fail one on the server with the same summary entry.

**Success.** The page becomes a confirmation card: pill `Submitted`, heading `REQ-00431 Submitted for Approval`, `Reserved 196,000.00 USD against Plant 4 Line Retrofit. Routed to Amara Okafor, step 1 of 1. You can withdraw it while it is pending, and the reservation is released once.` and buttons `View requisition` (primary) and `New requisition`. While it runs: the button reads `Submitting`, is disabled and `aria-busy="true"`, status line `Submitting`. The POST takes `Idempotency-Key`; a double click or retry must not reserve twice.

## 7. States (all required, REQ UX-004)

Loading (skeletons at final height in the card bodies), error (`Could not load this requisition`, `Try again`, nothing changed), partial (catalogue fails with `Could not load suggestions` / `You can still add lines by hand.` while the form, lines and funding check keep working), new (empty lines), validation summary, short on funds, submitting, submitted. Every board exists on the canvas.

**Draft saving.** `Save draft` is explicit and shows the toast `Draft saved. Nothing is reserved until you submit.` A draft reserves nothing. (Open: autosave, below.)

## 8. Open questions for the operator (put in `notes.blocked`, do not guess)

1. **Routing rule.** The sample says over 250,000.00 USD adds a second approver. The real routes come from the approvals engine (E10); the screen only displays what the server returns.
2. **Standalone requests.** REQ-004 creates a project at approval. Which budget does a standalone request draw from, and is any funds check done at submit?
3. **Needed-by in the past** is blocked here as a reasonable reading of REQ-011. Confirm.
4. **Autosave** versus explicit save, and how long an abandoned draft lives.
5. **Quantity decimals and units.** The sample uses whole quantities. Are fractional units (kg, m) allowed, and does the unit come from the UoM master data (E05-S03) as a select?
6. **Currency.** FX exists (E05-S05). Can a requisition be in a currency other than the organization's, and how is the estimate shown against a USD budget?

## 9. Tests the story must leave behind

Lines and funding agree (three-way: line totals, estimate, available after); cents arithmetic (`3 × 0.10`); catalogue add and search; type tag and keyboard remove; empty submit lists problems, focuses the summary, links focus fields; each message in section 6; **short funds blocks submit and no control bypasses it, and a reduced estimate unblocks it**; two concurrent submissions against the last available funds, exactly one succeeds (real Postgres, FIN-006); a replayed `Idempotency-Key` reserves once; standalone mode; a requisition-generated sub-project cannot be chosen (and the server refuses it too); inactive ledger account; keyboard-only path with visible focus; the action bar stays in view; axe in light and dark on every board; phone (375) no sideways scroll and 44px targets; lines never overflow their card at 1024, 1100 and 1280.
