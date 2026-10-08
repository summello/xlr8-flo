# Design canvas: decisions

Status at 9 Oct 2026 (second round: Project Dashboard page added). Canvas: https://claude.ai/artifact/95sB8BhSTJnNetSkmmNCNn (private). Source of truth in the repo: `design-system/canvas/project/`. Branch `worktree-design-canvas-xlr8flo`, no PR opened (only a human opens a PR to `main`; `milestone/M1-budget-spine` is not on origin).

Decided by the operator unless marked *(Claude)*. Where a decision departs from `design-system/MASTER.md`, it says so. **MASTER.md was not amended**; the canvas transcribes it and extends it. The amendments are listed at the end.

## 1. Structure

1. Canvas has two pages. **Playground** is where Home and the Executive Dashboard were refined (with width variants). **Design System** holds only the design system sheet and the theme panel.
2. The sheet shows light and dark side by side and covers: chrome, lifecycle phases, status, tags, chart series, type, components, navigation and shell, icons, overlays and feedback, interaction states, motion, loading/empty/error, layout rules and exceptions.
3. The canvas is the working copy; the repo copy is refreshed from it when a round is approved.

## 2. Layout

4. **The Executive Dashboard is the reference** for how a dashboard page lays out. Tiers by *content* width (so a collapsed sidebar moves a page up a tier): under 760 one column, KPI cards two across with the last full width; 760 to 1099 KPI 3+2 (second row wider), cards 7/5 of 12; 1100 to 1599 five KPI cards, cards 8/4; 1600 and up waterfall/business units/spend line at 5/3/4, funnel and table below at 3/9.
5. No content cap: the page fills the width it is given. Cards are containers; tables stack to cards below 660 *card* width; charts scroll inside their box below 540 with a data table alternative.
6. Cards in a row share the height of the tallest card.
7. **Exception E-1: Home** stays 8/4 from 1100 up (no 3-up, no KPI row). Registered 8 Oct 2026. Revisit when Home gains cards or KPIs. `layout.spec.mjs` pins the current state.

## 3. Shell

8. Sidebar groups are named for the lifecycle phases (Foundation, Plan, Demand, Commit, then Support); phase colour on the icon and active marker only.
9. The collapse toggle lives in the sidebar, in the logo row; collapsed (56 rail) it sits under the logo above a divider and the logo stays visible. Never on its own row while the sidebar is open.
10. Below 1024 the sidebar is a 56 rail whose toggle opens it as an overlay over a scrim; below 768 it is a drawer. The hamburger exists only below 768. There is no close X (scrim or the toggle closes it).
11. Collapsed state persists per user (`localStorage` key `xf.sidebar`), unless a board forces it.
12. **The top bar and the sidebar are pinned** at every width; page content scrolls beside and under them. On a short window the sidebar scrolls inside itself and its items keep their size. Rail tooltips are one fixed element so scrolling cannot clip them.
13. Touch targets are 44px below 1024.

## 4. Interaction

14. KPI cards lift **3px** with a spring and take a 1px outline in their own series colour *(explicit exception to MASTER 6.2, springs for drag only; "more is not always great")*.
15. (Amended 9 Oct, operator: a card holding a table or grid takes **no** hover outline; chart and summary cards keep it.) Every other card: **outline only**, no movement, in the next chart-series colour *by position on the page*, assigned in script, so a new card needs no colour decision *(Claude's recommendation, accepted)*. Starts at the series the KPI cards do not use.
16. Chart tooltips are a rounded box with a title, swatch and measured rows (amount, share, running total), on hover and keyboard focus, on every chart (waterfall, spend line, business units, funnel). Flips below a mark near the top.
17. Charts have Chart/Table tabs with a sliding indicator; the table is the accessible alternative.
18. Command menu opens with zero animation. Popover 150ms scale from its trigger. Record sheet slides 260ms, traps focus, returns focus to its row. Toast 220ms, closes itself at 4s, with Undo.
19. Motion budget by frequency, transform and opacity only; reduced motion keeps the end state. The sidebar width snaps (layout never animates).
20. Signature moments 1 (first paint, count-up) and 2 (waterfall settle) are built on the Executive Dashboard; "Replay intro" is a prototype-only button. In the product they play once per session. Moments 3 to 5 ship with the transfer, approval and purchase order screens.

## 5. Content

21. **Money:** whole amount first; decimals and currency lighter and smaller (never below 11px); currency always present; negatives in parentheses and red; works inside sentences and tooltips. KPI figures are compact (`48.25M`) with the full amount in a tooltip.
22. **Headings are Title Case:** capitalise every word of four letters or more, plus the first, last and post-colon word; keep short articles, conjunctions, prepositions and helping verbs lower case; acronyms and codes keep their form; user-typed record titles are data and are not changed. Gated by `tests/lint.mjs` and `copy.spec.mjs`.
23. Icons are placeholder line icons (24 grid, 1.6 stroke); **Phosphor regular is still to be swapped in** (MASTER 7.10).

## 6. Quality gates

24. Playwright runs the real boards locally (runtime served as `support.js`). 202 tests: static gates each proven against a planted violation (no animated layout property, no colour literal, every token defined, reduced-motion block, title case, board overlap), responsive, shell, layout, motion budgets, reduced motion, interactions, copy, design-system completeness, and axe in light and dark across the overlay states and the sheet. See `README.md` for how to run.

## 6a. Project Dashboard page (screen list confirmed with the operator before building)

Screens (all in the inventory, none a gap): project list, project dashboard, ledger entries with source-document lineage, project detail. The budget overview (a gap, E07-S06 is API only) was **not** built; propose it as a story first.

25. Page `Project Dashboard` holds four main boards at 1440 plus 1920, 1024, 768 and 375 variants and a row of state boards each (loading, empty, error, partial; the ledger adds an open sheet, the detail adds one board per tab). 40 boards, all driven by `view`, `tab` and `open` props on the main board, so a variant is a tiny wrapper.
26. **Hierarchy is an indented tree grid** (organization, business unit, project, phases), not a treemap: `role=treegrid`, `aria-level`, `aria-expanded`, a toggle button per parent that also answers ArrowLeft and ArrowRight, and a used-share meter under each name with the percentage as text. A phase links on to its ledger entries. The same markup serves the dashboard card and the detail tab.
27. Dashboard cards follow the Executive Dashboard rule exactly (hierarchy as the wide card, budget status as the narrow one, spend line, requisitions and orders, recent ledger entries). The spend chart has Chart/Table tabs; every chart has its tooltip.
28. **Tables stay tables down to 660 card width.** Between 660 and 899 the low-priority columns (status, allocated, phase, available after) drop out of the row via `.hide-md` and return in the stacked card below 660. A row of six money columns does not fit 660 to 900, and sideways scrolling is not an option.
29. **Ledger rows are bucket movements.** Each entry moves one bucket (allocated, reserved, committed, actual) and the screen shows "Available After" computed from them, so the figures cannot drift from the KPIs (a test checks they agree). Entries are never edited: a correction is a reversal row, shown with the entry it reverses. The sheet says so and offers no edit or delete.
30. List: saved views (all, mine, needs attention), search, business unit and status filters, group by business unit, sort on every money column, 10 per page, a total row. Filters and typed input survive an error. In the partial state spend figures show "Not available" while allocated stays current.
31. Detail: tabs Overview, Hierarchy, Milestones, Attachments (inventory gaps for phases, risks and notes left out). Attachments show a scan state as icon plus text (clean, scanning, quarantined) and a blocked file says why it cannot be opened. `New project`, `Add milestone` and `Upload file` only show a toast: their forms belong to other screens.
32. Design system sheet gains one section, **Tables, Trees and Lineage**, and one icon, `chevron` (rotates for open, up and down; transform only). New classes in `xlr8flo.css`: `.field .inp .toolbar .pager .notice .row.group .row.total button.sort .chev .tree-btn .tree-name .meter .lineage .file-ico .hide-md` and the `cols-*` grids. Tokens only.

## 6b. Requisition Create (built 9 Oct 2026)

Page `Requisition Create` holds three screens, each with 1440, 1920, 1024, 768 and 375 and its states (34 boards): **create and edit** (with the validation summary), **list**, **detail**. Page specs for the coding agents: `design-system/pages/requisition-create.md` and `requisitions.md`. Return, amend and resubmit with a diff (E09-S08) waits for the Approval Inbox page; the linked-record graph (E09-S09) is a gap and is shown as a list.

33. **A requisition that exceeds the funding available blocks submit** (operator, 9 Oct 2026). The Funding Check card shows the shortfall as the lines change; submitting still returns a `Funds` entry in the summary; there is no override, no "submit anyway" and no bypass on this screen. The server enforces it independently under the `FOR UPDATE` lock (FIN-006), so the UI figure is advisory.
45. **The submit button is never disabled to signal an error.** It stays enabled, and a failure produces a summary at the top (`role=alert`, focus moves to it) listing each problem as `Category: link`, each link focusing its field or line. Categories: Required field, Master data, Project, Lines, Funds, Routing (REQ-011). Typed input is always kept.
46. **Money in editable lines is held in whole cents** (a test proves `3 x 0.10 = 0.30`); the line total and the estimate are computed, never typed; on the server `Decimal`/`NUMERIC(18,4)` as always.
47. **Lines are one table with an explicit Item/Service tag per line** (REQ-006), editable in place; below 660px a line becomes a stacked card whose cells keep their column names. Catalogue suggestions add a tagged line with quantity 1 and the last price (REQ-007, REQ-008).
48. **A requisition-generated sub-project is listed but disabled in the Project select** (REQ-003); the server refuses it too. A standalone request shows no funds figure, only that funding is assigned at approval (REQ-004).
49. **Status is always an icon plus words** across the lifecycle of REQ-005 (Draft, Approval Pending, Sourcing in Progress, Sourced, Awarded, Open for Purchase, Completed, Rejected). The detail page shows the lifecycle as an ordered list with `aria-current="step"` and "done" in words, not colour.
50. **Per-line quantities show requested, sourced, awarded, ordered, cancelled and remaining** (REQ-015); remaining is requested less ordered and cancelled; a split award is visible as awarded above ordered. The detail grid follows the dashboard layout rule; the Lines table card takes no hover outline.
51. The Design System sheet gains a section **Forms, Steppers and Action Bars** (radio group, textarea, lifecycle stepper, suggestion list, total bar, sticky action bar). New classes: `.req-grid .req-stack .form-grid .inp.ta fieldset.choice .cols-line .cols-req .cols-qty .total-bar .action-bar .stepper .suggest .sr-only`.

Open questions are in the two page files (routing rule, standalone funding source, needed-by in the past, autosave, fractional units, currency, who may withdraw).

## 6c. Common Screens (in progress; sign in first because the coding agents depend on it)

Page `Common Screens`. **Built:** sign in with its states (wrong password, server unreachable, signing in, session ended, field errors), two-step verification (challenge, invalid code, enrolment, recovery codes), sign up (form, errors), verify-your-email, and **invitation acceptance** (form, field errors, server unreachable, checking, expired, unavailable, accepted, phone). One board family (`SignIn*`), driven by a `view` prop. Page specs for the coding agents: `design-system/pages/sign-in.md`, `sign-up.md`, `invitation.md`. Story requests and the paste-ready prompt for the invitation story: `STORY-NOTES.md`. **Planned, in this order:**

1. Password reset: request (the same message whether or not the address exists), set a new password, expired link.
2. Recovery-code entry (the "use a recovery code" link), email verified and verification link expired.
3. Logout and session expired (as a screen, not only the sign-in notice).
4. 403, 404 and 500 with a correlation id and a copy button; tenant-foreign records always show 404, never 403.
5. Suspended tenant notice (read-only grace period) and demo-tenant banner.
6. Empty and error patterns as a reference board, then the role-based onboarding tour.
7. ~~Invitation acceptance~~ designed 9 Oct 2026 (see 38 to 44); still needs a roadmap story, and a prerequisite multi-organization story (see the conflicts above).
8. Organization switcher in the app shell menu (decision 41), after the chooser.

Decisions:
34. Sign in and sign up share one card: **two panels side by side at the centre** (product promise left, form right), stacked below 768 with the promise shrunk to a brand strip. The card is opaque and above the scene, so the backdrop never sits behind text.
35. **Backdrop: a quiet outline scene of a capital project** (tower crane, building going up floor by floor, excavator, skyline) in tokens at low opacity, masked to fade upward. The crane stands at the left edge and the building at the right so the card does not hide them. Motion follows the motion skill's rules for this surface: CSS only (the canvas has no library), transform and opacity only, loops of 7 to 22 seconds, no spring or overshoot (this is a ledger product), and the un-animated state is the finished picture, so reduced motion stops everything and loses nothing. Tests read the real animations and fail on a layout property or a loop under 6 seconds (planted violations included).
36. Failure behaviour from E05-S10 and E05-S11 kept exactly: a wrong password, an unknown email, a locked account **and a throttled attempt** all show one uniform message (D-M1-27, D-M1-29: the throttle must reveal nothing about the account); the email is kept, the password cleared, focus moves to the error summary. No distinct "too many attempts" screen exists, by design. A network error says nothing was submitted and the email is kept. (An earlier draft of this page had a distinct throttled state; it was removed on 9 Oct 2026 after re-reading E05-S11.)
38. **Passwords follow the requirements, not convention** (AUTH-003, AUTH-004): minimum 15 characters, paste allowed, no composition rules, no scolding strength meter, common and breached values refused. Sign-up and invitation both say so in the hint.
39. **No account enumeration anywhere** (AUTH-007): the verify-your-email copy is conditional ("If this address can create an account...") and identical for a new and an existing address; every unusable invitation link renders one message, `Invitation Unavailable`, with no organization shown. Expiry is the only distinct case.
40. **Invitation: the organization, role and scope come from the invitation record, not the URL** (tenancy rule). The left panel shows who invited you, the tenant, the role, the access and the expiry; the email is read-only. Lifetime 7 days, shown in the copy.
41. **Decided 9 Oct 2026 (operator): one person may belong to more than one organization, and the same organization may exist as separate tenants** (for example two portfolios on different continents under different data laws). Each tenant is fully separate; nothing is shared. A tenant is identified by organization name plus a **tenant label** chosen by its administrator (`Northwind Capital, EMEA`), so two of the same name can be told apart in the chooser, the invitation and the shell menu. The page set gains a **Choose an Organization** screen after sign-in (shown only with two or more memberships) and the invitation gains an **existing account** path (sign in, then `Accept and join`; other organizations are untouched).
42. **Decided: only users with an administrator role can invite**, for now.
43. **Decided: resend is required, and an invitation expires 7 days after it was sent without being accepted.** A resend issues a new link and invalidates the previous one. *Assumption, confirm:* a resend restarts the 7 days (otherwise a resent invitation could be born nearly expired).
44. **Decided: privileged roles must enrol in two-step verification before first use** (AUTH-006), so the invitation's accepted state hands straight to enrolment for those roles.

### Conflicts this created with existing decisions (not resolved here; Opus and the operator decide)

- **E05-S09 D-M1-23** says one identity belongs to exactly one organization in Phase 1 and lists multi-organization membership as out of scope. Decision 41 reverses that. It needs a `docs/claude-plan.md` section 1 entry and a story that changes `identity_membership` (primary key becomes `(identity_id, org_id)`), makes the session carry the **chosen** organization, and adds the chooser and a switcher. The invitation story depends on it for the existing-account path.
- **Data residency is not a requirement today.** Nothing in `docs/requirements.md` mentions data location, and the architecture is one pooled database in one region (D-06, D-01 to D-04). Separate tenants make legal separation possible but do **not** by themselves put data in a chosen country. The canvas therefore shows a tenant *label* and makes no residency claim. If clients need data kept in a region, that is a new requirement and an architecture decision (a database per region), to be raised before any sales conversation promises it.
37. Inputs on access screens are 44px; password has a Show/Hide toggle (`aria-pressed`); the code field is one input with `autocomplete="one-time-code"`; the QR code has the key as text for people who cannot scan it.

### Registered exceptions from the access screens (also in `design-system/pages/sign-in.md`)

| Id | Where | Exception |
|---|---|---|
| E-2 | Access backdrop | The 300ms transition cap does not apply to the ambient loops (7 to 22s). They are not transitions; transform and opacity only, loops of at least 6s, reduced motion shows the finished picture, enforced by `auth.spec.mjs`. |
| E-3 | Access screens | Inputs and buttons are 44px at every width. |
| E-4 | Access screens | No app shell (sidebar, top bar). |

## 7. Findings to feed back into MASTER.md (not yet amended)

- `--fg-muted` clears 4.5:1 on canvas and surface but measures 4.31:1 on `--sunken`; the canvas steps muted text up to `--fg-secondary` on sunken grounds (hover and selected rows).
- The three E04 debts in `agents/project-memory.md` (border rule, dark `--shadow-drag`, `--fg-muted` comment value) were left alone, as instructed.
- Defined by the canvas but not in MASTER: `--z-sticky`, `--scrim` (MASTER lists it under chrome; the canvas uses it for overlays), rail and drawer dimensions, the `.amt` money classes, the card hue rule, the spring exception.
- The operator's original direction, **not yet built**: white and very dark grey chrome, a wider colour range on cards and charts, fixed semantic colours for buttons (for example red for delete, blue for undo), each with an icon or text. Needs the MASTER amendment first.

## 8. Open

- Remaining pages: Approval Inbox, Budget Transfer, Comparison Dashboard, and the rest of Common Screens (6c).
- Project Dashboard page: only signature moment 1 (KPI count-up) is used. The budget overview screen waits for a story.
- Home needs more cards and KPIs as screens are added (then retire E-1).
- Sample data only; amounts are internally consistent but invented.
