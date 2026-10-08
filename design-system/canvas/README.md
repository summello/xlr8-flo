# Claude Design canvas source

Source of the Claude Design canvas `xlr8flo-design-system` (https://claude.ai/artifact/95sB8BhSTJnNetSkmmNCNn, private).

- Start with `DECISIONS.md` (everything decided) and `HANDOFF.md` (the plan for the remaining screens).
- Seven canvas pages (Approval Inbox holds `Appr*`, `ApprSheet*`, `WdDialog*`, `ReqDetailReturned`, `ReqDetailWithdrawn`, `Resub*`, `Deleg*`; Approval Workflow Builder holds `WfList*`, `Build*`; Requisition Create holds `Requisition*`, `ReqCreate*`, `ReqList*`, `ReqDetail*`; Common Screens holds the access screens, `SignIn*`): Playground (Home, Executive Dashboard, width variants), Project Dashboard (project list, dashboard, ledger entries, detail, with widths and states) and Design System (the sheet and the theme panel).
- Files under `project/` mirror the published canvas paths.
- `project/xlr8flo.css` transcribes the tokens in `../MASTER.md` and adds the motion, state and overlay classes. It does not amend MASTER.md; MASTER.md stays the source of truth.
- Boards: `ProjectList`, `ProjectDashboard`, `ProjectLedger`, `ProjectDetail` (variants `List*`, `Dash*`, `Ledger*`, `Detail*`), `Main` (Home), `ExecutiveDashboard`, `DesignSystem` (+ `ThemePanel`), and width variants `HomeW*`, `ExecW*`, `HomeCollapsed`.
- Sample data only. Layout and interaction review, not a build spec.
- The canvas on claude.ai is the working copy; refresh these files from it when a round is approved.

## Rules and Exceptions

The Executive Dashboard is the reference for how a dashboard page lays out (tiers by content width: under 760 one column; 760 to 1099 cards 7/5; 1100 to 1599 cards 8/4 with five KPI cards; 1600 and up waterfall, business units and spend line at 5/3/4 with funnel and table below at 3/9). Cards in a row share the tallest card's height. The same table is on the design system sheet.

| Id | Page | Exception | Revisit |
|---|---|---|---|
| E-1 | Home | Keeps the 8/4 two-row layout from 1100 up, no 3-up row at 1600, no KPI row. Four cards today. Registered 8 Oct 2026. | When Home gains cards or KPIs; then follow the dashboard rule. `layout.spec.mjs` pins the current state. |
| E-2 | Access backdrop | Ambient scene loops (7 to 22s) are exempt from the 300ms transition cap: transform and opacity only, loops of at least 6s, reduced motion shows the finished picture. | Never; enforced by `auth.spec.mjs`. |
| E-3 | Access screens | 44px inputs and buttons at every width. | If a dense access variant is ever needed. |
| E-4 | Access screens | No app shell. | Never (pre-authentication). |

Conventions: headings are Title Case (`tests/lint.mjs`, `copy.spec.mjs`); amounts are whole figure first with lighter, smaller decimals and currency; KPI cards lift 3px with a spring and outline in their series colour; every other card takes a 1px outline only, in the next series colour by position (assigned in script, so new cards need no colour decision).

## Tests

Playwright runs the real boards locally, with the canvas runtime served as `support.js`.

```
cd design-system/canvas/tests
ln -s "$(npm root -g)" node_modules        # or npm i -D playwright
DC_RUNTIME=<dc-runtime.js> AXE_CORE=<axe.min.js> npx playwright test
# if another checkout already serves 4173, set PORT=4180 (any free port) for both the run and any server you start
```

- `DC_RUNTIME`: the canvas's `artifact-type/dc-runtime.js` (read it from the artifact; it is not committed).
- `AXE_CORE`: defaults to `apps/web/node_modules/axe-core/axe.min.js` in a full checkout.
- `lint.spec.mjs`: no layout property animated, no colour literal in an artboard, every `var(--token)` resolves, a reduced-motion block exists. Each gate is also run against a planted violation.
- `shell.spec.mjs`: responsive overflow, sidebar modes, toggle placement, touch targets, stacked cards, popover, command menu, record sheet, toast.
- `layout.spec.mjs`: no content cap at 1920 and 2560, layout tiers per width, equal card heights in every row, money format (whole amount first, lighter smaller decimals and currency).
- `designsystem.spec.mjs`: canvas pages, no overlapping boards, every sheet section present in both themes, shell samples, the full icon set, overlay samples; every icon used on the screens must be in the set.
- `copy.spec.mjs`: title case on every rendered heading and column head; the rules and exception E-1 are on the sheet.
- `motion.spec.mjs`: duration budget per element, reduced motion, intro count-up, tabs, tooltips.
- `projects.spec.mjs`: the Project Dashboard page: happy and keyboard paths, every state, figures that must agree across screens, sheet focus trap, phone targets, axe in light and dark on 26 boards, planted violations.
- `requisitions.spec.mjs`: create (lines, cents, catalogue, validation summary, funds block, submit, standalone, keyboard, sticky bar), list (tabs, filters, status icons), detail (lifecycle, quantities, ledger agreement, layout rule), axe in light and dark on 25 boards, planted violations.
- `auth.spec.mjs`: the access screens: sign in, two-step verification, sign up, the backdrop's motion rules (with planted violations), reduced motion, axe in light and dark on 18 boards.
- `approvals.spec.mjs`: the two approval pages: inbox, decision panel, withdraw, resubmit diff, delegation, workflow list and builder (validation, cycle prevention, outline, keyboard, pointer drag, route preview), axe in light and dark on 31 boards, planted violations.
- `a11y.spec.mjs`: axe in light and dark across the default, collapsed, popover, command menu, sheet, drawer and tab states. Includes a planted violation to prove the harness fails.
