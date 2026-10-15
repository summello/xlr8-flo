# Handoff: Projects and Budget (registered 14 Oct 2026, nothing drawn yet)

The project and budget screens the Project Dashboard and Budget Transfer pages do not cover.

**Page in the canvas:** `projects-budget` in `design-system/canvas-2/project/canvas.json` (second canvas, https://claude.ai/artifact/62JVFUjfv8Bw3KiKXEWs5r). Method, anti-goals, self-review and tests are those of `design-system/canvas/HANDOFF-m3.md` sections 3 to 6, unchanged: Operate mode, tokens only, restraint, no disabled submit, status never by colour alone, boards at 1440 plus 1920, 1024, 768 and 375, every state, a spec file with axe in light and dark, and a page file `design-system/pages/<page>.md` for the coding agents. **Confirm the screen list and the questions below with the operator before building**, record the answers in `design-system/canvas/DECISIONS.md` (the one decision log, numbering continues), then draw.

## Screens

| Screen | Roadmap story | Requirements |
|---|---|---|
| Project detail: phases, milestones, risks and notes | E06-S07 | PROJ-020, XFN-002 |
| Budget overview: allocated, reserved, committed, actual, available | E07-S06 | BUD-009, BUD-011, ACC-003 |
| Allocation form with reason and effective date | E07-S03 | BUD-001, BUD-006, BUD-007 |
| Reconciliation drift report | E07-S09 | BUD-013, OBS-004 |

## Requirement text (verbatim from `docs/requirements.md` and `docs/claude-plan.md`)

- **PROJ-020** — Users must be able to filter, sort, group, search, and save project views.
- **XFN-002** — Module grids must support sorting, filtering, resizing, reordering, visibility, pinning where useful, pagination/virtualization, and saved views.
- **BUD-009** — Balances must support MTD, QTD, YTD, fiscal-year, life-to-date, and selected ranges.
- **BUD-011** — A user must be able to reconcile a displayed balance to its component entries.
- **ACC-003** — Every dashboard balance drills down to a ledger that recalculates to the same value.
- **BUD-001** — Project dashboards must provide authorized allocation, adjustment, and transfer utilities.
- **BUD-006** — Every ledger entry must link to its source document or authorized manual action.
- **BUD-007** — Manual adjustments/transfers require a reason, effective date, supporting evidence when configured, and approval when applicable.
- **BUD-013** — Recalculation jobs must be idempotent and report discrepancies rather than overwrite posted history.
- **OBS-004** — Business monitoring must expose stuck approvals, unreconciled balances, expired vendor documents, and failed transmissions.

## Questions for the operator before building (drafted, not asked yet)

1. Does the budget overview replace the dashboard drill-down or sit beside it?
2. Is an allocation reversible from the form, or only by a counter-entry?
3. Who sees the drift report, and what can they do from it, given that the nightly job reports and never corrects?

## Reuse

The shell, `.page`, `.card`, `.tabs`, `.toolbar`, `.row` grids, `.pill`, `.sig`, `.fold`, `.dialog`, `.sheet`, `.summary`, `.action-bar` and the required-field tag from `xlr8flo.css`; `design-system/MASTER.md` sections 2.3, 7.1 to 7.16 and 8 for components. A new component goes on the Design System sheet in `design-system/canvas/` (`ThemePanel.dc.html`) with a test in `designsystem.spec.mjs`.

## Not here

A story to build the screens. Raise one per screen group in `design-system/canvas/STORY-NOTES.md` when the page is drawn.
