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
117. **A rate older than 7 days shows a warning, and any sourcing role may refresh it** (operator). The rate, its source and its date are stored with the comparison; the vendor's bid never changes.
118. **A sealed round is hidden from everyone until the deadline** (operator), including evaluators and the RFQ owner; it opens on its own.
119. **Ties on a line read `Tied` and the earlier submission takes the line; the buyer may override with a reason** (operator). **Compliance requirements belong to the RFQ** (copied from a template); **waiving one needs a reason and the sourcing lead** (operator), and is audited. **Still open:** how a split award interacts with vendor minimum order values.

120. **Bid comparison, colour and density revised** (operator feedback, 10 Oct 2026: the `Lowest` label was overlapped by the selection highlight and the screen overwhelmed with data). Colour now has **three jobs only, each with an icon and words**: a **win** (green tint, with `Lowest`, `Fastest` or `Highest Score` inside the cell), **worth a look** (amber: partial, not comparable, not quoted) and a **failure** (red: misses the needed-by date, three or more unmet requirements). One or two unmet requirements are an icon and words without a tint, so amber is scarce. A **colour key** sits on the matrix. A mark lives inside the cell it describes, so nothing can overlap it (a test measures every mark against its cell). The selection outline on `By Line` is 1px with a text twin and a caption. **Density:** the matrix opens with four short groups (Cost, Delivery, Compliance and Score); `Price, Tax and Freight` and `Terms` are closed groups that say how many rows they hold and open on demand (progressive disclosure, a native-button toggle with `aria-expanded`); the verdict card keeps one total, how it compares, three quiet facts and only its wins; a vendor that misses the needed-by date (award 12 Oct plus lead time) is flagged in red in the strip and the matrix.
Tests: `comparison.spec.mjs` (81 tests, including planted violations: an altered landed total, `Lowest` on a partial bid, a figure on a sealed page, an unlabelled input), with figures checked against values computed outside the page, and axe in light and dark on 19 boards. `transfers.spec.mjs` now has 141 tests.

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
