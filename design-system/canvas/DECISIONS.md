# Design canvas: decisions

Status at 10 Oct 2026 (Project Dashboard, Requisition Create, Common Screens, Approval Inbox, Approval Workflow Builder, Budget Transfer and RFQ Comparison pages added). Canvas: https://claude.ai/artifact/95sB8BhSTJnNetSkmmNCNn (private). Source of truth in the repo: `design-system/canvas/project/`. Branch `worktree-design-canvas-xlr8flo`, no PR opened (only a human opens a PR to `main`; `milestone/M1-budget-spine` is not on origin).

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

## 6d. Approval Inbox and Approval Workflow Builder (built 9 Oct 2026; screen list confirmed with the operator first)

Two canvas pages. **Approval Inbox** (about 55 boards) holds: the inbox (pending, overdue, completed, delegated), the decision panel with its routing explanation, the withdraw confirmation, the returned requisition and the resubmit diff, and delegation with out-of-office. **Approval Workflow Builder** (about 25 boards) holds the workflow list with version history, and the builder with canvas, outline, inspector, problems, route preview and publish. Page specs for the coding agents: `design-system/pages/approval-inbox.md` and `approval-workflow-builder.md`. Story requests: `STORY-NOTES.md` section 3. Each screen has 1440, 1920, 1024, 768, 375 and its states; variants are wrappers over the main boards, driven by `view`, `tab`, `open`, `err`, `case`, `dialog`, `mode`, `sel` props.

52. **A decision is never undone and its panel never offers a disabled button.** Approve, Reject, Return and Post Comment stay enabled; a refused attempt gives a `role=alert` with focus (reason missing, stale version). Where an action is not allowed at all (own request, already decided, read-only), the buttons are absent and a notice says why (WF-006).
53. **The decision panel explains routing in a fixed order:** the numbered route with your step marked, the rule that matched, why you, the group rule, when the group was fixed (APR-018), separation of duty. Route Preview in the builder uses the same words, so an administrator sees what an approver will see (APR-021).
54. **A decision ends in a result, not a toast.** The panel body becomes a `role=status` with a tick that draws in 260 ms (end state under reduced motion), the outcome, what happens next and an audit line; `Next: <number>` keeps a run of approvals to two clicks each and moves focus. This is signature moment 4, as quiet as moment 2. *(Claude; operator confirm.)*
55. **A stale version is refused, not warned about** (APR-016): the notice appears on open, the decision is refused with the reason, `Load Version 3` shows what changed, and a comment typed so far is kept.
56. **A diff is words plus a struck-through was.** Columns Field, Was, Now, Change; the change is a pill with an icon (Changed, Added, Removed); money shows a computed delta. One `.diff` component serves the decision panel, the resubmit screen and the workflow version history. The `Removed` icon is the close cross because a flat minus fails the icon-set height gate.
57. **Material or not is shown, never decided on screen.** Resubmit states whether routing restarts, and what happens to the reservation (released once, or checked under the lock for an increase). A short-of-funds resubmit is blocked like a requisition (decision 33). The material-change rule is an open question; the sample uses amount, project, ledger account, catalogue item or line count.
58. **Withdraw asks first, with the safe button focused,** says what stops, what is released and that it happens once, and offers no way back; a failure keeps everything and the reason; an already-approved requisition releases nothing. Semantic button colours (red for destructive) wait for the MASTER amendment in section 7, so the confirm button is the primary style.
59. **Delegation never rewrites history** (APR-017). Decisions made as a delegate are shown as the delegate acting for the owner, forever; a revoke returns only undecided tasks (assumption); ended rows say `Kept in the record`. The delegate picker lists eligible people only and the server re-checks.
60. **The builder has a drag canvas and an outline with the same operations in the same story** (APR-001, APR-002, A11Y-003): add, connect, move, delete; keyboard on a canvas node (arrows, Delete) and `Goes To` selects in both the outline and the inspector. The canvas scrolls inside its own box; below 768 the outline is the default.
61. **Cycles are prevented when connecting** (the connect is refused with the graph unchanged) and still detected when validating (APR-007). Publish never opens its dialog while a problem exists; the Problems tab and a focused summary take over.
62. **A published workflow version is immutable** and says how many documents in flight keep it (ACC-007); the publish dialog and version history both say so.
63. **No graph library.** The graph is HTML node buttons over per-edge SVG paths; the canvas runtime has no library and the rule is the simplest thing that works. Revisit when a workflow routinely has more than about 30 nodes. `ponytail:` ceiling: all nodes re-render on every drag step.

64. **A hyperlink is coloured, underlined and moves** (operator, 9 Oct 2026). `a.link`: the focus colour, 1px underline that thickens to 2px and lifts from 3px to 5px on hover, and a trailing arrow that moves 3px (120ms, transform only; reduced motion keeps the shifted end state). Pressed state returns to body colour. First use: `Open the full requisition` in the decision panel. Other in-text links (`a.u`) are unchanged until the operator asks.
65. **The decision panel puts the trail first** (operator). Order: key facts, the link, **Decision Trail** (a `t-heading` with an event count, a first entry `Waiting for you, step 1 of 1, due <date>` marked with a filled dot, then history), the comment, and **Why It Reached You** as a collapsed native `<details>` labelled `for audit`. The fold opens by itself when its content failed to load so an error is never hidden. Approvers rarely ask why a task reached them; auditors can still open it in one click.
66. **Required fields are marked by the control's own state** (operator: "be creative"). Every required control carries `required`; its label carries a `Required` tag that becomes a green `Done` with a drawn tick (120ms) as soon as the control has a value, and an empty required control gets a 3px rail on its start edge (hidden while focused, so the focus ring wins). A conditional rule uses `data-req` and its own wording (`Needed to reject or return`). The action bar shows `Required fields are still empty` or `All required fields complete`, from `:has()`, so no script counts anything and a new field needs only the attribute and a tag (a numeric count was tried: `.card` is a style-containment boundary, so a CSS counter cannot cross cards). Required line cells in tables get the rail but no tag (they are named by `aria-label`). The tag is `aria-hidden`; assistive technology gets `required`. **Submit and Save stay enabled** (decision 45): an incomplete form is refused with the error summary, and the server enforces every required field independently. Rolled out to sign in, two-step verification, enrolment, sign up, invitation, requisition create, delegation, the builder inspector and Route Preview, and the decision panel's reason. Gate: `requiredMarkViolations` in `tests/lint.mjs` fails any tag whose control is not required, and any required control without a tag (planted violations in `lint.spec.mjs`). The design system sheet gains **Links, Folds and Required Fields** and one icon, `arrow`.

New classes in `xlr8flo.css` (tokens only): `.sheet.wide .dialog-scrim .dialog .dialog-h .dialog-b .dialog-f .tick .diff .diff-row .cols-appr .cols-deleg .cols-wfl .cols-out .row.due-late .who-list .route .sticky-note .vers .wf-shell .wf-bar .wf-scroll .wf-board .wf-edges .wf-elabel .wf-node .wf-badge .wf-port .wf-hint .problems .chk`. The sheet gains one section, **Diffs, Dialogs and Workflow Graphs**, and six icons (clock, return, user, swap, plus, branch; Removed reuses the close cross). Two test findings fixed in the stylesheet, both the known `--fg-muted` on `--sunken` problem (section 7): `.sticky-note` and `.total-bar` captions step up to `--fg-secondary`. The latter also existed on the requisition detail page.

Tests: `approvals.spec.mjs` (76 tests, including planted violations: a removed approver, an unconnected step, a loop, a non-title-case heading) plus the sheet and icon tests. `playwright.config.mjs` now takes `PORT` so a run never talks to another checkout's server on 4173.

Open questions are in the two page files (withdraw rights, material change, revoke behaviour, re-delegation, duplicate approvals, conditions beyond the estimate).

## 6e. Budget Transfer and approval open questions (answered by the operator, 9 Oct 2026; nothing built yet)

Asked one at a time from the handoff note `NEXT-SESSION.md` (removed once the page was built). Every answer below was chosen by the operator; the ones marked *(Claude's recommendation, accepted)* matched the recommended option.

**Scope**

67. **Manual allocation and adjustment share the transfer form** through a `Type` switch (Transfer, Allocation, Adjustment) (BUD-001). The operator chose this over the recommended separate page. Allocation and adjustment involve one project, so the second project, the eligibility check and the ancestry preview are hidden for them. The roadmap has no UI story for them yet, so the stories need a request in `STORY-NOTES.md`.

**Budget Transfer money rules**

68. **Ancestors are re-summed, not posted to** *(accepted)*. Only the source and target projects get ledger entries (out, in). Each ancestor's total is a derived roll-up; the preview shows recounted totals (before, change, after), not new rows. **Conflict to raise with Opus:** BUD-003 and requirements 7.3 say "generate balanced, linked ledger entries for every affected level". Decision 68 reads that as the roll-up lines of the preview, not as persisted rows. A requirement is not changed here (AGENTS.md rule 4); E07-S08 must confirm the reading before it is built. Until then the canvas shows the preview rows as "recounted" and the entry list as two rows.
69. **Eligible = same organization, same hierarchy depth, both projects open** *(accepted)*. Anything else is `Cross Hierarchy` and needs an organization policy flag plus approval (BUD-002). A closed or frozen project is ineligible with an `Eligibility` entry.
70. **Approval is the workflow by amount** *(accepted)*. Cross Hierarchy is always routed, and the source and target project owners are added. Ancestor owners are notified, not required to approve.
71. **A transfer that takes the source below zero is always blocked** *(accepted)*. No negative-budget policy exists; no override on the screen (same as decision 33). The shortfall shows as a parenthesised, red, signed figure and a `Funds` entry in the summary.
72. **Same currency only** *(accepted)*. Different currencies make the pair ineligible with an `Eligibility` entry. No rates, no conversion.
73. **The effective date is today or later, in an open period** *(accepted)*. A past or closed-period date fails with a `Period` entry. Backdating is a later decision.
74. **Evidence is an organization setting with three values:** off, Cross Hierarchy only, or always *(accepted)*. When it demands evidence the field carries the `Required` tag (decision 66); otherwise it is optional. Attachments show scan states as on project detail.
75. **Notification:** approvers on submit; both project owners on posting, plus ancestor owners for Cross Hierarchy; the requester on rejection or return *(accepted)*. All through the outbox.
76. **A posted transfer can be reversed** *(accepted)*. `Reverse Transfer` on the detail page creates a new linked draft that goes through the same approval route; the original is untouched (append-only). Blocked with a `Funds` entry if the target no longer has the funds available. Who sees the button: the requester's role or finance administrator (exact role names for the page spec).
77. **The loser of a race is refused, not queued** *(accepted)*. A `role=alert` with focus says what changed ("Available is now X; your transfer needs Y"). Nothing posts, the typed input and draft are kept, and one button, `Edit Amount`, returns to the form. No automatic retry. The server decides under the `FOR UPDATE` lock; the figure on screen is advisory.

**Approval pages (closes the open questions of 6d)**

78. **Withdraw:** the requester, until sourcing starts *(accepted)*. An approved requisition releases its reservation exactly once.
79. **Material change on resubmit is the fixed list** *(accepted)*: amount, project, ledger account, catalogue item, line count. Not configurable per workflow; the builder gets no setting.
80. **Revoking a delegation returns undecided tasks to the owner** *(accepted)*. Decisions already made stay attributed to the delegate acting for the owner.
81. **A delegate cannot re-delegate** *(accepted)*. The picker is absent on delegated tasks and the server refuses it too.
82. **Duplicate approvals are allowed per step with an explicit tick** *(accepted)* (`Same Approver May Repeat`). The default is one decision per person per document.
83. **Workflow conditions use the estimate only** *(accepted)*. Department and risk wait for Phase 2.

**Housekeeping**

84. **No MASTER.md amendment yet** *(accepted)*. Required-field indicator, hyperlink style, border rule and semantic button colours stay on the canvas and in the page specs until **after Budget Transfer is built**, then one amendment pass for everything in section 7. Only the operator can authorize it.

**What changes in the proposed screen list** (see `HANDOFF-budget-transfer.md`): screen 2 gains the `Type` switch; screen 3 (ancestry preview) shows recounted totals, not new rows; screen 4 gains `Reverse Transfer`; screen 6 follows decisions 70 and 77. No new screen.

## 6f. Budget Transfer (built 9 Oct 2026; screen list confirmed with the operator first)

Page `Budget Transfer` holds 47 boards: the **list**, **create** (transfer, allocation, adjustment), **detail** (with reversal) and the **posting moment**, each with 1440, 1920, 1024, 768 and 375 where it is a page and its states (loading, empty, partial, error). Two boards on the Approval Inbox page show the transfer variant of the decision panel. Page spec for the coding agents: `design-system/pages/budget-transfer.md`. Story requests: `STORY-NOTES.md` section 5. The money rules are 6e (items 67 to 77); these are the design decisions made while building.

85. **The ancestry preview is two stacked tables, not two columns** *(Claude)*. Four money columns do not fit half of an 8/12 card with the currency code visible; stacked, every cell keeps its code. Source, Going Up above Target, Going Down; a shared level appears in both and reads `No net change`.
86. **Every level carries its state as a word as well as a dot.** `Recounted`, `Recounting`, `Waiting` (screen readers get it as hidden text, the Change cell says `Recounting` or `Waiting` while it runs). Colour never carries it (A11Y-004). The lit row is tinted with a rail; the dot pulses once at --dur-base (180ms), transform only. A planted-violation test blanks a word and proves the check fails.
87. **The Chart and Table tabs are the accessible data table for the moment** (A11Y-009). Same levels, one list, recount order, State column. Arrow keys switch, the panel is labelled by its tab.
88. **Reduced motion shows the finished picture at once** with a note, and `Replay Posting` jumps to it. The posting moment ends in a `Posted` result with the entry ids; its static frames (before, trace in progress, after, reduced motion, table) are boards. `Replay Posting` is prototype-only, like `Replay intro`.
89. **Totals follow the rows.** Moved Out and Moved In are derived from how many levels on each side are recounted, so a static frame is deterministic and the strip never disagrees with the table. A test checks the three figures on the trace frame.
90. **Posted-state ledger entries are the transfer's two project entries, linked to the ledger sheet.** A pending transfer says `Will post`. Net zero is shown. The page never offers to edit an entry (append-only).
91. **A reversal is a confirmation, then a draft.** The dialog focuses the safe button and says what is created, that it needs approval and that the funds are checked. A refusal for missing funds is a `role=alert` on the page with focus and `Nothing was created`. A reversed transfer shows `Reversed By BT-00030` and no second reversal.
92. **The inbox transfer panel puts "What Posts If You Approve" with the key facts, before the link and the trail** (decision 65 order kept: facts, link, trail). Funds gone at decision time **refuse approval** and keep the comment; Approve stays enabled (decision 52).
93. **One id family, `BT-`,** for transfers, allocations and adjustments, and one list. Open: separate prefixes. Sample owners follow the existing data (Dev Patel owns Plant 4 Line Retrofit).
94. **New classes in `xlr8flo.css` (tokens only):** `.cols-tr .cols-ent .cols-dent .cols-anc .cols-anct .anc-dot .anc-row` (states `is-done`, `is-lit`, `is-wait`) and the keyframe `ancPulse`. The Design System sheet gains **Ancestry Traces and Entry Grids** (`s-transfers`). Everything else is reused (`.tabs`, `.row.stack`, `.meter`, `.total-bar`, `.lineage`, `.dialog`, `.summary`, the required-field mark). The Type switch is the existing `fieldset.choice`.
95. **Entry points:** `Transfer Funds` in the project dashboard header and `N transfers this year` with `N awaiting approval` in the project detail Budget Summary card. Two existing width boards (`DetailW375`, `DetailW768`) grew 58px and were re-measured.

96. **BUD-003 reading settled: ancestors are recounted, the two projects are posted** (operator delegated the choice, 9 Oct 2026, "the cleaner, least bug-prone path"). Why: persisting a row per ancestor would put a second copy of every roll-up in the ledger that can drift from its children (invariant 9), would need a lock on every ancestor on both paths (ordering and deadlock risk, and the whole organization row becomes a hot lock for every transfer), and would double the rows reversal has to mirror. Recounting needs the lock on **two** project rows only, taken in ascending id order so two opposite transfers cannot deadlock. The level-by-level view the requirement asks for is the preview, the posted ancestry and the Table, all derived. **The requirement text still says "entries for every affected level"; clarifying it is an Opus decision-log entry in `docs/claude-plan.md` section 1, not made here.** Proposed wording is in `STORY-NOTES.md` section 5.
97. **MASTER.md amended** (operator, 9 Oct 2026): sections 7.11 (links, folds, required fields) and 7.12 (ancestry trace) added, §6.3 and §8 extended, §6.2 item 3 marked built. Section 7.13 (semantic buttons) is a **proposal** pending the operator's sign-off, with dark-mode values still to be contrast-measured. The border rule (§2.7) and dark `--shadow-drag` (§3.1) were already in MASTER, so those two debts are closed. `tokens.css` is unchanged and follows MASTER.

Tests: `transfers.spec.mjs` (140 tests, including planted violations: an altered ancestry level, a trace row that loses its word, an unlabelled input) with axe in light and dark on 38 boards. `designsystem.spec.mjs` now expects eight pages and the new section. Heights in `canvas.json` come from a measured run.

## 6g. Transfer answers (9 Oct 2026) and the Bid Comparison page (built 10 Oct 2026)

Operator answers to the open Budget Transfer questions, then the screen list confirmed for the RFQ comparison dashboard. Page specs: `pages/budget-transfer.md` (amended) and `pages/rfq-comparison.md`.

98. **Only a Finance Administrator may reverse a transfer, for now** (operator). Supersedes the "requester's role or finance administrator" wording of decision 76. Anyone else sees no `Reverse Transfer` button and the line `Only a Finance Administrator can reverse a transfer.` The server enforces it (hiding a button is not authorization).
99. **An allocation draws on the organization's available budget; there is no separate unallocated pool** (operator). The form says `Organization Available Budget`, the entry `Allocation From Available Budget`, and BUD-008 is checked against the organization's available.
100. **Approval thresholds are configurable by tenant and by entity, with a provision for user-based thresholds** (operator). The create form states `Set by your approval thresholds` before the route. *Assumption, confirm:* the most specific scope wins (user over entity over tenant), and the routing explanation names the scope that matched. The sample amounts (over 100,000.00 USD adds the Finance Director) are only a sample. This needs a settings story (STORY-NOTES section 6).
101. **The policy flag is `Allow Cross-Level Transfers`, changed by an organization administrator only** (operator). Off, a Cross Hierarchy pair fails with an `Eligibility` entry that names the flag.
102. **One `BT-` id prefix** for transfers, allocations and adjustments (operator). **Allocation and adjustment reuse the transfer workflow** (operator).
103. **Notification copy is drafted** (operator asked) in `pages/budget-transfer.md` section 10.
104. **Bid comparison, award basis** (operator): the default is **Lowest Cost Supplier**, which picks the lowest supplier **per item** so an RFQ with several items and suppliers may be awarded to **several vendors**; the second option is **Best Average Cost**, which keeps the **whole RFQ with one vendor**. Both are shown as radio cards with their own totals. *Assumption, confirm:* "average cost" is the lowest total landed cost among vendors that quoted every line, so a partial bid cannot win it.
105. **The award is a hand-off, not part of this page** (operator: "okay"). `Start Award` carries the selection to the award draft (engine E12-S08, no UI story yet). It is never disabled; over the approved scope it is refused with the figure (SRC-016).
106. **At most 5 vendors per RFQ** (operator, for uniformity with the UI). **Compliance is pass or fail per requirement, counted `n of N Met`**, with scoring separate. **The base currency is the organization's currency** (USD in the sample).
107. **Decide in layers** *(Claude)*: a verdict strip, then a side-by-side matrix, then a sheet per vendor. `Show Only Differences` is on by default and counts what it hides. The best value in a row carries one marker, an icon plus words, never colour alone; `Lowest` is awarded only among full bids; a partial bid is labelled and not compared, but can win a line. Nothing recommends a vendor.
108. **Sealed means sealed** *(Claude)*: before the deadline no price, term, score or requirement count is on the page, only who has submitted; a test fails if a figure leaks. A late bid is shown only with its audited override (who, when, why).
109. **Narrow widths pick two vendors** *(Claude)* instead of scrolling a five-column table sideways: under 1000px of content width two vendors show with a picker (never the same one twice); under 560px each measure sits above its values and the verdict strip swipes.
110. **The cost chart is one stacked bar per vendor** *(Claude)* in a fixed order (price, tax and duty, freight) with the total beside it and a dashed approved-scope line; the Chart/Table tabs make the table the accessible equivalent. No radar chart and no weighted-score gauge.
111. **New classes in `xlr8flo.css` (tokens only):** `.cmp-strip .cmp-facts .cmp-opts .cmp-opt .cmp-toggle .cmp-pick .cmp-matrix .cmp-row .cmp-group .cmp-vend .cmp-legend .cmp-bars .cmp-bar-row .cmp-bar .cmp-scope .cols-cmp .cols-bl`. The Design System sheet gains **Comparison Matrix, Award Basis and Cost Bars** (`s-compare`). A `--fg-muted` on `--sunken` fix is repeated for the selected option card, a hovered row, the lit ancestry row and the totals bar (DECISIONS section 7).

112. **MASTER 7.13 (semantic buttons) approved as drafted** (operator, 10 Oct 2026). Primary is the one filled action; Secondary is the default; Destructive is danger text plus icon plus verb, with a danger fill only on a confirmation dialog's confirm button; Undo uses the info colour; there is no green "positive" button. **Still owed:** the dark-mode values must be contrast-measured in a tokens story before any code; `tokens.css` follows MASTER, never the reverse.
113. **Approval threshold resolution: the most specific scope wins** (operator): user over entity over tenant. A user-based threshold is a personal approval limit, and the routing explanation names the scope that matched. Settles the assumption in decision 100.
114. **Best Average Cost is the lowest total landed cost among vendors that quoted every line** (operator). A partial bid cannot win it. Settles the assumption in decision 104.
115. **Only an Organization Administrator changes `Allow Cross-Level Transfers` and the evidence-required setting** (operator). Every change is audited.
116. **Freight is shared across lines pro rata by line value** (operator), for per-line comparison.
117. **A rate older than 7 days shows a warning, and any sourcing role may refresh it** (operator). The rate, its source and its date are stored with the comparison; the vendor's bid never changes. **Amended 27 Oct 2026 (operator): the server wins. `Refresh Rate` requires `rfq.edit` on the RFQ's unit (`api/sourcing.py`, E12-S06), not any sourcing role; the page shows it only to `rfq.edit` holders (E12-S10).**
118. **A sealed round is hidden from everyone until the deadline** (operator), including evaluators and the RFQ owner; it opens on its own.
119. **Ties on a line read `Tied` and the earlier submission takes the line; the buyer may override with a reason** (operator). **Compliance requirements belong to the RFQ** (copied from a template); **waiving one needs a reason and the sourcing lead** (operator), and is audited. **Still open:** how a split award interacts with vendor minimum order values.

120. **Bid comparison, colour and density revised** (operator feedback, 10 Oct 2026: the `Lowest` label was overlapped by the selection highlight and the screen overwhelmed with data). Colour now has **three jobs only, each with an icon and words**: a **win** (green tint, with `Lowest`, `Fastest` or `Highest Score` inside the cell), **worth a look** (amber: partial, not comparable, not quoted) and a **failure** (red: misses the needed-by date, three or more unmet requirements). One or two unmet requirements are an icon and words without a tint, so amber is scarce. A **colour key** sits on the matrix. A mark lives inside the cell it describes, so nothing can overlap it (a test measures every mark against its cell). The selection outline on `By Line` is 1px with a text twin and a caption. **Density:** the matrix opens with four short groups (Cost, Delivery, Compliance and Score); `Price, Tax and Freight` and `Terms` are closed groups that say how many rows they hold and open on demand (progressive disclosure, a native-button toggle with `aria-expanded`); the verdict card keeps one total, how it compares, three quiet facts and only its wins; a vendor that misses the needed-by date (award 12 Oct plus lead time) is flagged in red in the strip and the matrix.
Tests: `comparison.spec.mjs` (81 tests, including planted violations: an altered landed total, `Lowest` on a partial bid, a figure on a sealed page, an unlabelled input), with figures checked against values computed outside the page, and axe in light and dark on 19 boards. `transfers.spec.mjs` now has 141 tests.

## 6h. Vendors (built 13 Oct 2026; screen list and questions confirmed with the operator first)

Page `Vendors` holds 60 boards: the **vendor list**, **profile**, **request**, **questionnaire administration**, each with 1440, 1920, 1024, 768 and 375 and its states; the profile also carries the status, scope and document dialogs, the document sheet and the scorecard sheet as boards. Two boards on the Approval Inbox page show the **onboarding decision panel** (`ApprSheetVendor`, `ApprSheetVendorLow`). Page spec for the coding agents: `design-system/pages/vendors.md`. Story requests: `STORY-NOTES.md` section 7. Tests: `vendors.spec.mjs`.

**Operator answers, 13 Oct 2026** ("accept all recommendations", to the ten questions in `HANDOFF-vendors.md` section 7; each is the handoff's proposal):

121. **(6h-1) Vendor status vocabulary:** `draft`, `pending_approval` (info), `active` (success), `suspended` (warning), `inactive` (neutral), `rejected` (danger), icon plus words. **MASTER was not amended**; the map is a proposal in `pages/vendors.md` section 9 for the operator to fold into MASTER 2.3.
122. **(6h-2) Required documents:** an organization list of document types, each `required` or `optional`, optionally per vendor category. The per-category part is not drawn; the canvas assumes five required types.
123. **(6h-3) An expired required document makes the vendor not eligible**, automatically. Eligibility is derived and the status does not change (VEN-012).
124. **(6h-4) Who may reveal tax and banking:** vendor managers and finance users only, every reveal audited, never auditors by default (PRIV-001). Everyone else sees `Restricted`.
125. **(6h-5) Local disable of a global vendor:** an organization setting `Allow Local Disable`; a user with `vendor.edit` on that unit may do it; **no approval**.
126. **(6h-6) Onboarding score below the threshold informs, it does not block.** The approver sees the fact; approval decides.
127. **(6h-7) A duplicate identifier blocks a second record** and offers `Use Existing Vendor`; a vendor manager may override with a reason (audited).
128. **(6h-8) The `Service and Delivery` contact receives RFQs and POs; `Billing` receives nothing in M3**, with a per-contact override.
129. **(6h-9) No minimum order value is captured now.** A money field with no rule would settle a rule on a board; it stays open with the award screen (decision 119).
130. **(6h-10) Performance in M3:** purchasing users score manually per PO and period; disputes by vendor managers. Goods receipt (M4, E14) will automate it.

**Design decisions made while building** *(Claude)*:

131. **Eligibility and status are different things.** Status is a pill; eligibility is a derived `signal` (icon plus words) with its reasons, shown in the profile header and in the list. A vendor with an expired certificate stays `Active` and reads `Not Eligible`.
132. **A signal is an icon plus words, with the icon taking a tone.** One class, `.sig`, serves eligibility, documents and the questionnaire weight total, so the list does not grow a second pill vocabulary. Pills are for status only.
133. **The list answers "why not" without a click:** the `Eligible Here` cell shows the first reason on the next line (`and 1 more`), and the Documents cell looks ahead (`Expiring in 12 Days` before `Expired`).
134. **The scorecard is a wide side sheet, not a route**, so `Open Scorecard` keeps the profile behind it and stays at two actions.
135. **Onboarding evidence is a fold, not a card:** the profile's `Onboarding Record` is a closed `<details>` (decision 65); the approver's per-question breakdown is a closed fold too.
136. **Masked values are fetched on Reveal.** The board keeps sample values in its own script only to simulate the reveal; the test inspects rendered markup and fails a planted leak.
137. **Questionnaire reorder is move-up and move-down buttons**, named with the question; drag is optional and not drawn.
138. **Destructive buttons follow MASTER 7.13:** `.btn.danger` (danger text, icon, verb) and `.btn.danger.fill` only on a confirmation dialog's confirm button.
139. **A pair of cards that never goes three-up** (`.v-w`, `.v-n`: 7/5, then 8/4) is used for Performance and History so no row is ragged at 1600 and up; the rest follow the dashboard rule.
140. **New classes in `xlr8flo.css`** (tokens only): `.sig .sig-why .elig .mask .btn.danger .btn.danger.fill .v-w .v-n .cols-ven .cols-vdoc .cols-vcon .cols-vsc .cols-vq`. The Design System sheet gains **Eligibility, Signals and Masked Values** (`s-vendors`). Everything else is reused (`.tabs .toolbar .row.stack .pager .summary .dialog .sheet .kv .lineage .suggest .total-bar .action-bar .fold .req-tag .meter`).
141. **The inbox gains two pending vendor-onboarding rows**, so `approvals.spec.mjs` counts moved (9 to 11 pending). Earlier boards still show people by name; the vendor rows use email (D-M2-16). A later pass should align them.

**Self-review with impeccable (13 Oct 2026, two isolated assessments: design review A, detector with browser evidence B).**

- **Specificity:** authored for capital procurement, not interchangeable. Eligibility derived for a unit and explained in words, the vendor snapshot beside the live record, suspension consequences for open RFQs and issued POs, duplicate evidence and score on the approver's panel, document expiry feeding eligibility. The generic parts (tabs, table, form) are right for Operate.
- **Heuristics (A), 0 to 4:** visibility of status 3, match to the real world 3, control and freedom 3, consistency 3, error prevention 3, recognition rather than recall 2, flexibility 3, aesthetic and minimalist 2, error recovery 3, help 3: **28/40**. The two 2s were a duplicate named only in prose and an eight-card profile with a dead `Amount` row.
- **Cognitive load:** no memory bridge on a money figure (the vendor pages carry none). More than four options at the list header (5 tabs, 2 filters, 2 actions) and in the decision footer (5 buttons); both follow the existing patterns and are left.
- **Detector (B):** one advisory, `repeating-stripes-gradient`, from the shared `.hatch` class in `xlr8flo.css` that no vendor board uses: **false positive**. No overflow at 1440 or 375, no contrast failure in light or dark, no missing accessible names.
- **Fixed in one batch (P1):** (1) a placeholder, `the vendor`, leaked into the Trading Name input and the `Done` mark contradicted an invalid email: the value is kept apart from the success text and email fields carry the same dotted-domain pattern the summary checks; (2) the scorecard sheet squeezed Rationale and spilled the scorer's email: the scorer is now a line under the rationale and Rationale takes the flexible column; (3) the duplicate on the decision panel was prose: it is a link with the matching registration number and address beside the new request's; (4) the header rule showed the Plan hue on a Commit page: `--ph-page` on the page root now sets it; (5) 18px checkbox rows were under 44px below 1024: `.v-chk`; (6) the dead `Amount: Not applicable` row is gone for a vendor.
- **Left, with reasons (P2, P3, recorded in `pages/vendors.md` section 7):** the eight-card profile (a closed fold for Contacts and Linked Records was weighed and refused: contacts carry live checkboxes and Linked Records carries the VEN-013 caption); the five-button decision footer wrapping at 375 and the shell's top bar overlapping the sheet's close button are the shared decision-panel component and affect every document kind, so they are a follow-up on the Approval Inbox page, not a vendor fix; the 375 list tab strip wraps two long labels; the unconfirmed focus ring on one request input at one tab stop; 11px text is the system's own floor (`Required`, `Done`, sidebar group labels).

## 6i. Sourcing (screen list and questions confirmed with the operator first, 13 Oct 2026)

Screens added around the built comparison: RFQ list, start an RFQ from approved lines, RFQ editor (draft and amend), RFQ detail, key a bid, evaluation, award draft, award decision (inbox variant), cancel and supersede dialogs, and `Start RFQ` on the requisition detail. The comparison, by-line, cost breakdown, scoring read side, vendor bid sheets and their states (6g) are **not redrawn**. Page spec: `design-system/pages/sourcing.md`. Tests: `sourcing.spec.mjs`.

**Operator answers, 13 Oct 2026** ("Yes to all", to the screen list and to the nine questions in `HANDOFF-sourcing.md` section 7 plus the page rename; each is the handoff's proposal or my recommendation):

142. **(6i-0) The page `RFQ Comparison` is renamed `Sourcing`.** Its id `rfq-comparison` stays, so no link breaks.
143. **(6i-1) Status vocabulary.** RFQ: `draft` (neutral), `published` (info), `closed` (info), `under_evaluation` (info), `awarded` (success), `cancelled` (danger). Award: `draft`, `approval_pending`, `approved`, `superseded` (warning), `cancelled`. Icon plus words. **MASTER was not amended**; the map is a proposal in the page spec.
144. **(6i-2) One RFQ may carry lines from several requisitions** when they share business unit or operating unit and currency; a line that does not is shown disabled with the reason.
145. **(6i-3) The vendor's sent time decides lateness.** The buyer enters it with the vendor's email attached as evidence; the keyed time is always recorded beside it.
146. **(6i-4) The sourcing lead is `rfq.approve` on the RFQ's unit.** No new role. It waives compliance requirements and overrides late bids and ineligible vendors, with a reason, audited.
147. **(6i-5) An award goes through approval only when an award workflow is published.** Otherwise the board says `No award workflow is published. This award is approved when you submit.` A financial-control rule the operator owns; the notice makes it visible.
148. **(6i-6) The reserved amount an award does not use is released once, when the award is approved.** Matches the requisition detail copy. Drawn as an assumption only: E12-S08 stays blocked on E09-S13 for the engine rule.
149. **(6i-7) No split-award-versus-minimum-order warning is drawn.** The vendor carries no minimum order value (6h-9); it stays open (119).
150. **(6i-8) The non-lowest justification is an organization setting, on by default** (SRC-014 "when configured").

**Design decisions made while building** *(Claude)*:

151. **The comparison is not redrawn; the new screens wrap it.** The list, detail and award draft link into the built comparison, and `Start Award` lands on the award draft with its basis carried across.
152. **The line picker and the invitee picker are checkbox rows, not drag.** A quantity field is the whole split mechanism (REQ-014), so no drag path is needed.
153. **The eligibility signal is the Vendors one.** `Override` is offered only to `rfq.approve` on the unit, asks for a reason, and is recorded beside the vendor; the sixth invitee is refused with the limit in words even with an override.
154. **A published RFQ is amended, never edited.** The amend screen shows `What Changed` as words and a struck `was`, a required reason, and `Who Will Be Told` by contact; each invitee row says which version it holds.
155. **A keyed bid carries two times:** `Vendor Sent It On` (date and a 24-hour time, evidenced) decides lateness; `Keyed By Us` never changes. A late bid is kept out unless a lead accepts it with a reason, in one footer action (`Accept Late Bid and Save`).
156. **The award verdict sits beside the total and in the pinned bar,** not a screen below the lines, so the figure that decides the submit is never carried from one place to another. `Set All to Lowest` is the keyboard shortcut for the common case.
157. **Non-lowest justification is one line per line,** a text input marked Required, so three open textareas do not take the page.
158. **The release of unused reserved funds is labelled an assumption on the board,** and no ticket id appears in user copy.
159. **Captions on a sunken ground step up to the secondary colour** (`.row.group`, `.notice`), the same fix as DECISIONS section 7.
160. **New classes in `xlr8flo.css` (tokens only):** `.cols-rfq .cols-rln .cols-rit .cols-rinv .cols-rdi .cols-rbl .cols-rev .cols-raw`. Everything else is reused (`.sig`, `.elig`, `.diff`, `.stepper`, `.sheet.wide`, `.dialog`, `.total-bar`, `.action-bar`, `.fold`, `.v-chk`, `.v-w`, `.v-n`). No new Design System section: no new component.
161. **The inbox gains one pending award row set:** `AWD-00095` and `AWD-00096`, so `approvals.spec.mjs` counts moved again (see below). The `Start RFQ` action is on `RequisitionDetail` for a requisition in sourcing.

**Operator answers, 15 Oct 2026** (M3 planner session, the four blocking questions of `design/E12-S19.md`):

204. **(6i-9) An award with no published award workflow is refused** (409 `workflow_not_set_up`, D-M2-21). **Supersedes 147:** the award draft's notice becomes "No award workflow is published. Ask an administrator to set one up. Your draft is kept.", and the button stays `Submit for Approval`. The board is stale until the design session redraws it.
205. **(6i-10) The unused reservation is released once at award approval and returned to the main project in the same transaction.** Confirms 148 and settles where the money goes (E09-S16 left it on the sub-project).
206. **(6i-11) Requisition status triggers:** `sourced` when every open line is on a published RFQ; `awarded` and `open_for_purchase` together when every line's quantity is awarded on approved awards; `completed` when every line is ordered (received, from M4).
207. **(6i-12) Publishing an RFQ needs no approval.** It commits no money; it is the buyer's act, audited.

**Operator answers, 15 Oct 2026** (M3 planner session, purchase orders; `pages/purchase-orders.md` §7):

208. **(6j-13) A PO with no published PO workflow is refused** (409 `workflow_not_set_up`, D-M2-21, PO-003). The editor's `Approve when no PO workflow is published` is dropped; the board is stale.
209. **(6j-14) Cancelling or reducing an issued PO** releases the unused commitment once and **re-reserves it on the sub-project for the reopened quantity**, so another PO can order it without re-approval. If the requisition line is cancelled as well, the amount is released and returned to the main project (as 205).
210. **(6j-15) Budget Summary definitions confirmed** as written in `pages/purchase-orders.md` §7.9 (`Committed`, `This PO`, `Available After Issue`, `Project Actual`).
211. **(6j-16) Tax and freight treatment (claude-plan §14 #6, FIN-008):** two settings per BU/OU, `tax.consumes_budget` and `freight.consumes_budget`, default on. When on, that component counts against the budget (award scope check, PO commitment); when off it is shown separately on the PO and not committed. Always separately visible (PO-016).

**Operator answers, 19 Oct 2026** (M3 planner session, `design/E12-S19.md` item 3, SRC-017):

212. **(6i-13) Cancelling an RFQ leaves the reservation with the requisition.** No ledger entry; the RFQ's quantity returns to the lines as open and the requisition can be sourced again. Releasing the money stays the job of cancelling the requisition.
213. **(6i-14) Superseding an approved award is the exact reversal of its approval's money (205):** the awarded quantity returns as open, and the amount released and returned to the main project at approval is transferred back main → sub-project and re-reserved, in one transaction. Refused with `InsufficientBudget` when the main project no longer has it available. Allowed only before any PO exists.

**Self-review with impeccable (13 Oct 2026, two isolated assessments: design review A, detector with browser evidence B).**

- **Specificity:** the generic parts (list, tabs, draft editor, scoring grid) are right for Operate. What only capital procurement carries: the justification per non-lowest line, the scope and funds check with the released amount, requisition lines with a quantity to source against an open quantity, sealed bids with `Keyed By Us` and no portal, the conflict declaration before scoring, the award evidence block on the approver's panel.
- **Heuristics (A), 0 to 4:** visibility 3, real-world match 3, control 3, consistency 2, error prevention 3, recognition rather than recall 2, flexibility 2, minimalism 3, error recovery 3, help 3: **26/40**. The three below 3 are fixed below.
- **Cognitive load:** one P0 memory bridge on a money figure: the over-scope verdict was a screen below the lines and the total, and not in the pinned bar. Fixed.
- **Detector (B):** `repeating-stripes-gradient` from the shared, unused `.hatch` class on every board: **false positive**. No overflow at 1440 or 375, no duplicate ids, no control without an accessible name, no dark-mode contrast failure.
- **Fixed in one batch (P0 and P1):** (1) award verdict beside `Total Awarded` and in the action bar; (2) New RFQ header cells did not sit over their columns (a seventh, hidden header shifted them); (3) the late-bid callout contradicted itself (`Blocked` with an `Accept` button inside): one callout, one footer action; (4) mixed date formats and a 12-hour time: a date field and a 24-hour time field; (5) four open justification textareas on an over-scope award: one line each; (6) 4.32:1 captions on a sunken ground in light mode; (7) ticket ids in user copy; (8) no shortcut for the common case: `Set All to Lowest`; (9) no score range beside the score field.
- **Left, with reasons (recorded in `pages/sourcing.md` section 7):** the detail page has seven cards (each is a different question; a fold for History and Linked Records was weighed and refused, they are the audit trail); the footer wrap of the shared decision panel at 375 (follow-up on the Approval Inbox page); 18px checkboxes sit inside 44px labels (measured on the input, not the label); the unconfirmed focus ring on one text input in light mode at one tab stop (same as Vendors); money fractions and `USD` at 11px are the system's money style; sort eligible vendors first in the invitee picker (P3).

## 6j. Purchase Orders (screen list and questions; answers logged 13 Oct 2026, page not built yet)

Screens proposed and accepted: PO list, create from awarded lines, PO editor, PO detail, issue (confirmation and signature moment 5), transmission log, change order, cancel or reduce, PDF preview, PO decision (inbox variant), sending domain status. Page spec when built: `design-system/pages/purchase-orders.md`.

**Operator answers, 13 Oct 2026** ("Yes to all except 7 and 5"; each accepted answer is the recommendation made):

162. **(6j-1) A PO commits against the project that holds the requisition's reservation at issue,** whichever it is. The budget card names it. Drawn as a labelled assumption: E09-S13 is open.
163. **(6j-2) "Authorized project items" (PO-001's second path) are not in M3.** Only the award path is drawn, which keeps SRC-018 intact.
164. **(6j-3) Tax and freight are entered per line;** freight may also be entered per document and is shared pro rata by line value (as DECISIONS 116). Rounding is per line; document totals are sums of rounded lines.
165. **(6j-4) Bill To comes from the organization or unit address; Ship To is chosen from the delivery locations the requisition uses.** No one-off typed Ship To in M3.
166. **(6j-6) A change order needs reapproval for the requisition's material changes** (amount, project, ledger account, item, line count; D-M2-13).
167. **(6j-8) A PO in a vendor's currency commits at the rate stored with the award,** fixed at issue and shown with its date; a change order revalues only changed lines. Drawn as an assumption.
168. **(6j-9) The PO email goes to the vendor's `Service and Delivery` contact** (6h-8). The requester gets an internal notification on issue, not a copy of the vendor email.
169. **(6j-7) Fulfilment nomenclature is `Delivered` and `Accepted`, not `Fulfilled`** (operator). `Partially Delivered`, `Delivered` and `Accepted` are future states in M3 (goods receipt arrives in M4). The linked requisition's status when its lines are accepted is `Completed` (172). **Note for Opus:** PO-007 in `docs/requirements.md` still says `Partially Fulfilled` and `Fulfilled`, and MASTER 2.3 maps `partially_fulfilled` and `fulfilled`; a requirement is not changed on a board (AGENTS.md rule 4), so the wording is flagged here.
170. **(6j-5) The supplier receives an email and a PDF only** (operator, 13 Oct 2026). The email body is a high-level summary of the PO; the PDF is a simplified version of the buyer's PO with **no chartfields (project, department, ledger account) and no budget information**, and no internal notes. **PDF only, no HTML attachment** (PO-014: reproducible from the snapshot; HTML attachments are often blocked). The PDF is generated by the system from the PO snapshot, so it is not an uploaded file and D-M2-8 does not hold it back.
171. **(6j-5b) Other uploaded files stay internal in M3** (operator): an attachment marked for the vendor shows `Not sent until scanning is available` (D-14, E19-S04), and only the generated PDF goes out.
172. **(6j-7b) After acceptance the linked requisition's status is `Completed`** (operator), the status REQ-005 already has; no new `Fulfilled` status is added.

**Page built 13 Oct 2026** (the screen list and questions were confirmed first; this section was started when the answers were logged). Page `Purchase Orders` holds 83 boards; two decision panels sit on the Approval Inbox page (`ApprSheetPo`, `ApprSheetPoSuspended`). Page spec: `design-system/pages/purchase-orders.md`. Story requests: `STORY-NOTES.md` section 9. Tests: `purchase-orders.spec.mjs`.

**Design decisions made while building** *(Claude)*:

173. **The email result never uses the bare word `Delivered`.** It reads `Email Delivered`, `Email Bounced`, `Email Queued`, so it cannot be read as goods received, which is the future PO state `Delivered` (decision 169).
174. **A hard bounce makes `Change Recipient` the primary action and the focused one; `Resend` is second.** Resending to a suppressed address would fail; the recovery is a new address, then the same version again. MASTER 6.2 says the retry affordance is focused; for a hard bounce the next step is. A soft failure would focus `Resend`.
175. **The confirmation dialog on the PO is the commit; the issue page is the moment that follows.** The page has no second `Issue` button: a replay is prototype-only. Closes the double-confirmation question.
176. **Price, tax and freight line amounts equal the award's landed line amounts to the cent.** The sample is built so the PO and the award agree, and `purchase-orders.spec.mjs` checks it; a one-cent difference would read as a bug.
177. **The `Amount to Order` column follows `Quantity to Order`,** so the value of a partial order is never inferred.
178. **The vendor copy is a page of its own** (the email and the PDF side by side, and a list of what never goes to the vendor), and the PDF is also a sheet on the PO. `.pdf-page` is the one place a paper-like page is drawn; it uses surface tokens, not a literal white.
179. **The signature moment ends in words and a table** (A11Y-009, MASTER 7.12): the steps list, the result card and a Frames table whose selected tab follows the frame on show.
180. **No requirement, decision or ticket id appears in user copy;** a test fails if one does. The assumptions are one plain sentence.
181. **New classes in `xlr8flo.css` (tokens only):** `.cols-pol .cols-pcr .cols-pln .cols-ptx .cols-pch .cols-pst .cols-pdm .cols-psp .pdf-page .pdf-h .pdf-grid .pdf-row .pdf-tot .po-stage .po-doc .po-seal .po-trace .po-vendor`, and a 44px minimum on `.v-chk` and the sheet close button below 1024. Everything else is reused.

**Self-review with impeccable (13 Oct 2026, two isolated assessments: design review A, detector with browser evidence B).**

- **Specificity:** the shell (header, pill, stepper, two-column cards, history) is generic and right for Operate. What only capital procurement carries: reserved, committed and released side by side with the sentence that says which one the PO holds; the budget effect of a change order; the vendor copy with no chartfields; per-line partial ordering against an award; vendor-currency commitment at the award's stored rate; issue as one atomic act.
- **Heuristics (A), 0 to 4:** visibility 3, real-world match 3, control 3, consistency 3, error prevention 3, recognition 3, flexibility 3, minimalism 3, error recovery 3, help 3: **30/40**. No heuristic below 3; the P1s were specific defects.
- **Cognitive load:** no P0 memory bridge. One near miss: an amount that did not follow the quantity being ordered (fixed).
- **Detector (B):** `repeating-stripes-gradient` from the shared, unused `.hatch` class on every board: **false positive**. No overflow, no duplicate ids, no control without an accessible name, no contrast failure in either theme, and **no chartfield, budget or internal word on the PDF** (also a test).
- **Fixed in one batch (P1 and P2):** (1) the email result `Delivered` collided with the future PO state; (2) `Resend` was primary and focused for a suppressed address; (3) requirement and ticket ids and `Assumption, not yet a rule` in user copy; (4) a double confirmation, dialog then page; (5) the Frames table said `Before` while the picture showed another frame; (6) `Landed Amount` did not follow the quantity; (7) a bounce dated before the issue; (8) 18px checkboxes and a 34px close button below 1024; (9) the tab strip wrapped `Awaiting Approval`.
- **Left, with reasons (in `pages/purchase-orders.md` section 7):** eight cards on the detail (each answers a different question); four future stepper pills that share one note (a single caption was weighed: the pills are the lifecycle and are kept); the shell's top bar over a sheet's close button and the five-button decision footer at 375 are the shared decision-panel component; the unconfirmed focus ring on one date input in light mode; the 11px `Required` tag and money fractions are the system floor.


## 6k. MASTER amendment for M3 round 1 (13 Oct 2026; operator asked for it, Opus drafted)

Items 121, 143 and 169 said "MASTER was not amended". They are now folded in; the canvas follows MASTER from here, as everywhere.

182. **MASTER 2.3** gains the `vendor`, `rfq` and `award` maps (121, 143) and the purchase-order map with `partially_delivered`, `delivered`, `accepted` (169, 172). Email transmission results are signals, not statuses, and never use the bare word `Delivered` (173).
183. **MASTER 7.14 to 7.16** are the signal and eligibility component, the masked value, and the purchase order issue moment (131, 132, 136, 174, 175, 179, 181).
184. **MASTER 2.2** registers one exception to "phase colour in four places": the trace line of a signature moment (7.12, 7.16), because it is a drawing and its words and table always sit beside it.
185. **MASTER 7.13 dark values are measured** (danger text and fill 7.30:1 on `--surface`, 6.60:1 on `--raised`; confirm label 7.30:1; hover 6.18:1; undo 9.11:1). No new token. The canvas's `filter: brightness(0.92)` hover on the confirm fill is a shortcut; code uses a background shift.
186. **`docs/requirements.md`:** PO-007 and the requisition lifecycle diagram take Delivered and Accepted wording, and the PO report metric reads `delivery status`. The operator decided the nomenclature (169); no requirement's meaning changed.
187. **Code is behind MASTER in three places**, each for the first story that touches the screen: `status/map.ts` has `vendor` with `pending` and `expired` (decision 123 forbids an expired status), `rfq.closed` as neutral (MASTER: info), no `award`, and `purchase_order` with the old fulfilled keys; `labels.ts` has the matching words.
188. **A shared defect is recorded once** in `pages/approval-inbox.md` section 6 (decision-panel footer and top bar at 375), not on each of the three pages.

## 6l. Canvas comments, 14 Oct 2026 (operator; questions asked first, answers below)

Three open comments on the published canvas. Where a comment left a choice open, it was asked, not assumed.

189. **PO Detail is simplified** (comment on `PoDetail`): PO number is the page title; one header bar holds the connected lifecycle stepper over two equal swimlanes, `Order` and `Vendor`; the body is 70 / 30. Left: `Lines`, `Notes and Files`, `Transmissions`. Right: `Budget Summary`, `Linked Records`, `History`. The operator chose the stepper on a row above the two swimlanes, and `Sent to Vendor` inside Transmissions.
190. **Versions are a control, not a card.** The page shows the current version; a disclosure beside the PO number lists versions and change orders (Escape closes, focus returns to it). Class contract: `.po-ver` on a native `<details>`, reusing `.pop`, `.pop-h`, `.pop-i`.
191. **The stepper is connected** (`.stepper.is-linked`): a 2px line joins the steps and takes the success tone after a done step. The words and `aria-current` carry the meaning; the line is decoration and is dropped below 760px.
192. **PO Editor cap raised** from 1120 to 1600px, centred, full width below that (the operator chose a wider centred cap). Auto margins in a flex column shrink an item to its content, so the width is stated: `width:100%` with the cap.
193. **Bid Comparison comment of 9 Oct** (overlapping lowest highlight; colour for wins and failures): the operator says it has been addressed and asked for it to be resolved. No board change.
194. **New classes in `xlr8flo.css` (tokens only):** `.po-hdr .po-lanes .po-lane .po-body .po-col .stepper.is-linked .po-ver`.
195. **Budget Summary is per project** (operator, 14 Oct 2026): the immediate project of the PO's lines, not the parent. One block per project invoked on the PO, four figures: `Committed`, `This PO`, `Available After Issue`, `Project Actual`. A line's project is shown beneath its description; board `PoDetailProjects` shows a PO that touches two. The definitions of the four figures are assumptions to confirm (`pages/purchase-orders.md` section 7 item 9).

196. **The shared decision panel at 375** (found on the three M3 pages) is decided once, in `pages/approval-inbox.md` section 6 item 7, and built by story E11-S10. The variant boards at 375 are stale until redrawn.
197. **Stories raised:** E11-S09 (status vocabulary) and E11-S10 (decision panel at phone width); see `STORY-NOTES.md` section 10.

## 6m. Second canvas (14 Oct 2026; operator)

198. **The canvas is split.** The first canvas reached its limit of 512 files. A second canvas, **XLR8 FLO Design System 2** (https://claude.ai/artifact/62JVFUjfv8Bw3KiKXEWs5r, source `design-system/canvas-2/`), holds the Common Screens page (27 boards) and the Vendors page (60 boards), moved whole with their tests. The first canvas keeps nine pages and frees 87 files for new boards on them.
199. **Every GAP in the screen inventory is registered as a page in the second canvas,** grouped by module like the first: Home and Reporting, Organization and Administration, Projects and Budget, Requisitions and Approvals, Assets and Warehouses, Cross-Cutting Surfaces; the remaining Common Screens items are listed on that page. **Nothing is drawn yet** (operator): each page has a brief, `design-system/canvas-2/HANDOFF-<page>.md`, to be confirmed with the operator before building.
200. **Cross links are absolute.** A board cannot be deep-linked, so a link between canvases opens the other canvas in a new tab. The five links from first-canvas boards to `VendorProfile` now open the second canvas; no second-canvas board links to a first-canvas board.
201. **`xlr8flo.css` exists in both canvases** and a test fails if they differ. The Design System sheet, this decision log and MASTER stay single.
202. **The two onboarding decision panels** (`ApprSheetVendor`, `ApprSheetVendorLow`) stay on the Approval Inbox page; their tests moved to `canvas/tests/vendor-decision.spec.mjs`.
203. **Specs and rules that named `design-system/canvas/`** now name both (AGENTS.md authority 3, `agents/claude.md`, `author.sh`, `docs/author-traps.md`, CodeQL ignore).

## 6n. M3 open questions closed (21 Oct 2026; operator, in batches, planner session)

The three parking stories (E11-S08 vendors, E12-S19 sourcing, E13-S22 purchase orders) and the M2 follow-ups were put to the operator in five batches. Each answer is below; "assumption confirmed" means the packet's stated behaviour ships as written and nothing changes.

**Money and authorization**

214. **Vendor minimum order value, and split awards below it, are deferred to M4** (E11-S08 #2, E12-S19 #6). MOV is not captured in M3 (6h-9 stands); splitting below a vendor's minimum is the buyer's judgement.
215. **An override by an RFQ author who also holds `rfq.approve` is allowed and audited** (E12-S19 #7, E13-S22 #5: a non-lowest award, or a PO issued to a vendor suspended after approval). The reason field and the audit row are the control. Assumption confirmed.
216. **Assigning a standalone request to an existing project:** the final approver, who must hold `requisition.approve` on the target project (M2 follow-up, E09-S06). Assumption confirmed.
217. **Baseline roles from Requestor up gain `project.read` and `master.read` in their scope** (M2 follow-up, `docs/m2-decisions.md`). Requestors can see the projects they spend against. Change story **E22-S06**.

**Vendor data**

218. **Required document types stay org-wide in M3; per-category requirements are M4** (E11-S08 #1). The category is already stored. Assumption confirmed.
219. **One scoring set for every vendor** (four fixed dimensions plus org-wide extras; E11-S08 #4). Scores stay comparable across vendors. Assumption confirmed.
220. **A questionnaire draft finishes on the version it started on** (E11-S08 #6). Assumption confirmed.
221. **A reveal of masked tax or banking data lasts 60 s and re-masks on leaving the card** (E11-S08 #3). Assumption confirmed.

**Sourcing and purchase-order operations**

222. **Evaluation criteria and weights are set per RFQ by its owner while draft, prefilled from an organisation default** an administrator maintains (E12-S19 #8). Folded into **E12-S07**.
223. **The RFQ deadline is in the organisation's time zone, editable per RFQ** (E12-S19 #9). Assumption confirmed.
224. **An attachment that fails its scan blocks publish until removed or replaced;** no override (E12-S19 #10). Assumption confirmed.
225. **`Closed` is manual in M3;** from M4 a fully received purchase order closes automatically, and manual close stays for short-closes (E13-S22 #3).
226. **Supplier acknowledgement is deferred to the M4 vendor portal** (E13-S22 #6). Not captured in M3.
227. **The PO PDF is one code-versioned system layout carrying the organisation's address block and terms text,** set by an administrator and copied into each version's snapshot so issued PDFs never change (PO-014). No template editor, no template approval (E13-S22 #7). **The organisation logo follows when uploads are scanned** (decision 171, D-14): it is an uploaded image embedded in a document sent outside the company. Folded into **E13-S08**.
228. **Email allowance (100/day, the platform's Resend account):** transactional PO sends still go and digests wait, as shipped; **the operator is alerted once a day when 80 sends are used.** The cap is the platform's, not an organisation's, so the alert goes to the operator's monitoring, not to an org admin (E13-S22 #8). Folded into **E13-S09**.

**UI**

229. **Questionnaire rows reorder with Move Up and Move Down only;** no drag (E11-S08 #5). Assumption confirmed.
230. **The vendor profile ships with its eight cards as drawn, and the 375 tab strip wraps;** a horizontally scrolling tab strip becomes one cross-list follow-up (E11-S08 #8, #9).
231. **Requisition tones:** `rejected` danger, `withdrawn` neutral (the requester's own act), **`expired` warning** (recoverable by resubmitting, not an error). MASTER §2.3 gains the three rows. Change story **E22-S08** (E09-S11 shipped `expired` as danger).
232. **People are shown by display name with the email as secondary text and as the fallback** (E11-S08 #10). **Amends D-M2-16:** the identity gains a display name, copied from the invitation's `full_name` at acceptance. Change story **E22-S07**; boards that show email only are stale for the design session.

233. **Approval delegation gets its screen in M3** (STORY-NOTES §3; the E10-S08 engine had none). Built from `pages/approval-inbox.md` §5 and boards `ApprovalDelegation`, `Deleg*`, under the rules already accepted (59, 66, 80, 81). Story **E22-S10**.

234. **A reader scoped to a business unit sees its own scope plus organisation-wide master data** (operator, 22 Oct 2026; E22-S06's block). `GET /projects` and `GET /master/{kind}` accept `project.read` / `master.read` held at any scope and narrow rows to the caller's scopes: projects in the caller's BU/OU (and any project the caller holds a project-scoped grant on); master rows of the caller's BU/OU **plus** organisation-level rows (`unit_id` NULL), which apply to every unit. A foreign-tenant or out-of-scope id stays 404. Story **E22-S06**.

235. **Approval evidence is read with the instance, under the document's read permission; sensitive vendor fields stay masked** (operator, 22 Oct 2026; E11-S15's block). `GET /api/v1/approvals/instances/{id}` returns the adapter snapshot's `material.evidence` as an `evidence` field, guarded by the check it already makes (`adapter.read_permission` on the document, `vendor.view` for vendor changes). Any sensitive vendor field in the evidence payload is masked exactly as `GET /vendors/{id}` masks it; revealing stays the separate audited `POST /vendors/{id}/sensitive/reveal`. No extra endpoint. Story **E11-S15**.

236. **The vendor detail names whether the caller may reveal, and whom to ask** (planner, 22 Oct 2026, applying decision 124 and MASTER 7.15; E11-S13's block). `GET /vendors/{id}` returns `sensitive_access: {can_reveal: bool, ask: str}`: `can_reveal` is the server's own check of `vendor.sensitive.reveal` (the permission `POST /vendors/{id}/sensitive/reveal` already enforces), and `ask` lists the organization's role names that hold it. A caller who cannot reveal sees the masked last four, `Restricted` and whom to ask, and no button (decision 124). The reveal asks for one of the closed reason codes (`verify_payment`, `vendor_request`, `audit_support`, `other`) with an optional note. Story **E11-S13**.

237. **A requester completes their own draft vendor request** (operator, 22 Oct 2026; E11-S14's block). A caller holding `vendor.request` may write contacts, identifiers, documents and tax/bank details on a vendor they created (`vendor.created_by` = caller) while it is `draft`, through the existing routes. Sensitive values are write-only for them: saved, always returned masked, never revealed (reveal stays `vendor.sensitive.reveal`, decision 124). Once submitted it locks for them; `vendor.edit` holders keep their current rights. Someone else's draft or a foreign tenant's is 404. Story **E11-S14**.

238. **The vendor detail lists the actions the caller may take** (planner, 22 Oct 2026; E11-S16's block; same pattern as 236, no new rule). `GET /vendors/{id}` returns `actions: list[str]`, each computed in the service by the same check its route makes: `edit` and `request_change` (`vendor.edit`), `inactivate` and `verify_documents` (`vendor.approve`); and `local_disable_units: list[uuid]`, the units where the caller holds `vendor.edit` (the disable and enable routes' check), empty when the vendor does not allow local disable. The UI shows a control only when its action is listed; the server still refuses a forced call. Story **E11-S16**.

239. **A requester reads their own draft vendor request** (operator, 23 Oct 2026; E11-S14 review round 1). `GET /vendors/{id}`, `GET /vendors/{id}/contacts` and `GET /vendors/{id}/documents` admit a caller without `vendor.view` when they hold `vendor.request` and `vendor.created_by` is the caller, **in any status** (the read side of decision 237; amended 23 Oct by the operator so a submitted request reopens read-only instead of 404, as `GET /vendors/{id}/onboarding` already does). Sensitive values stay masked. Writes still lock at submit (237). Anyone else's vendor or a foreign tenant's is 404. `vendor.view` holders are unchanged. Story **E11-S14**.

240. **The duplicate check says whether the caller may override** (operator, 23 Oct 2026; same pattern as 236 and 238, no new rule). `GET /vendors/duplicate-check` returns `can_override: bool`, the server's own `vendor.approve` check that the override path makes. The UI shows Override with a Reason only when it is true and never probes another route to find out; the server still refuses a forced override. Story **E11-S14**.

241. **A vendor requester lists the units a vendor can be restricted to** (operator, 23 Oct 2026; E11-S14 planner pre-flight). `GET /org/units` admits a caller who holds `org.unit.read` (unchanged) **or** `vendor.request`. A caller with `vendor.request` and without `org.unit.read` gets only active units of their own organization (the `active` filter is forced to true), in the same `OrgUnitRead` shape; `GET /org/units/{id}` and every write are unchanged. A member holding neither permission gets 403. The rule lives in the org service, not the router. Unit names are reference data, not sensitive. Story **E11-S14** (the Where It Applies unit picker).

## 7. Findings to feed back into MASTER.md (amended 9 Oct 2026, see 97)

- **The MASTER amendment is deferred until after Budget Transfer (item 84), which is now built: the next session can draft it for the operator.** It would fold in the items below plus the ancestry state rows (6f item 86).
- **Required fields (6d item 66) and hyperlinks (item 64) are new conventions with no MASTER section.** Candidates for 7.x: the tag, rail and summary line, and the link style and hover.
- `--fg-muted` clears 4.5:1 on canvas and surface but measures 4.31:1 on `--sunken`; the canvas steps muted text up to `--fg-secondary` on sunken grounds (hover and selected rows).
- The three E04 debts in `agents/project-memory.md` (border rule, dark `--shadow-drag`, `--fg-muted` comment value) were left alone, as instructed.
- Defined by the canvas but not in MASTER: `--z-sticky`, `--scrim` (MASTER lists it under chrome; the canvas uses it for overlays), rail and drawer dimensions, the `.amt` money classes, the card hue rule, the spring exception.
- The operator's original direction, **not yet built**: white and very dark grey chrome, a wider colour range on cards and charts, fixed semantic colours for buttons (for example red for delete, blue for undo), each with an icon or text. Needs the MASTER amendment first.

## 8. Open

- MASTER 7.13 is approved (112); only its dark-mode contrast measurement is owed.
- Remaining pages: the rest of Common Screens (6c). Budget Transfer (6f) and Bid Comparison (6g) are built.
- Project Dashboard page: only signature moment 1 (KPI count-up) is used. The budget overview screen waits for a story.
- Home needs more cards and KPIs as screens are added (then retire E-1).
- Sample data only; amounts are internally consistent but invented.
