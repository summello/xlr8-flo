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
15. Every other card: **outline only**, no movement, in the next chart-series colour *by position on the page*, assigned in script, so a new card needs no colour decision *(Claude's recommendation, accepted)*. Starts at the series the KPI cards do not use.
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

## 7. Findings to feed back into MASTER.md (not yet amended)

- `--fg-muted` clears 4.5:1 on canvas and surface but measures 4.31:1 on `--sunken`; the canvas steps muted text up to `--fg-secondary` on sunken grounds (hover and selected rows).
- The three E04 debts in `agents/project-memory.md` (border rule, dark `--shadow-drag`, `--fg-muted` comment value) were left alone, as instructed.
- Defined by the canvas but not in MASTER: `--z-sticky`, `--scrim` (MASTER lists it under chrome; the canvas uses it for overlays), rail and drawer dimensions, the `.amt` money classes, the card hue rule, the spring exception.
- The operator's original direction, **not yet built**: white and very dark grey chrome, a wider colour range on cards and charts, fixed semantic colours for buttons (for example red for delete, blue for undo), each with an icon or text. Needs the MASTER amendment first.

## 8. Open

- Remaining pages: Requisition Create, Approval Inbox, Budget Transfer, Comparison Dashboard, Common Screens (see `HANDOFF.md`).
- Project Dashboard page: only signature moment 1 (KPI count-up) is used. The budget overview screen waits for a story.
- Home needs more cards and KPIs as screens are added (then retire E-1).
- Sample data only; amounts are internally consistent but invented.
