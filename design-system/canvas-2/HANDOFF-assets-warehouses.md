# Handoff: Assets and Warehouses (registered 14 Oct 2026, nothing drawn yet)

Milestone 4 screens, registered now so the inventory has a home for each.

**Page in the canvas:** `assets-warehouses` in `design-system/canvas-2/project/canvas.json` (second canvas, https://claude.ai/artifact/62JVFUjfv8Bw3KiKXEWs5r). Method, anti-goals, self-review and tests are those of `design-system/canvas/HANDOFF-m3.md` sections 3 to 6, unchanged: Operate mode, tokens only, restraint, no disabled submit, status never by colour alone, boards at 1440 plus 1920, 1024, 768 and 375, every state, a spec file with axe in light and dark, and a page file `design-system/pages/<page>.md` for the coding agents. **Confirm the screen list and the questions below with the operator before building**, record the answers in `design-system/canvas/DECISIONS.md` (the one decision log, numbering continues), then draw.

## Screens

| Screen | Roadmap story | Requirements |
|---|---|---|
| Warehouse list, locations, custodians, stock on hand | E14-S01 | AST-001, AST-002, ORG-007 |
| Stock movements and FIFO assignment, with the audited exception override | E14-S02 | AST-003, AST-004, AST-006, ARCH-003 |
| Valuation and negative-stock prevention | E14-S04 | AST-005, AST-007 |
| Fixed-asset register and detail | E14-S05 | AST-008, ORG-006 |
| Asset created from a PO, with lineage to the originating order | E14-S06 | AST-009, AUD-007 |
| Stock count and adjustment, with reason and configurable approval | E14-S07 | AST-010, BUD-007 |

## Requirement text (verbatim from `docs/requirements.md` and `docs/claude-plan.md`)

- **AST-001** — Each BU/OU may create warehouses for unassigned fixed assets and basic stock counts.
- **AST-002** — Warehouses must record locations, custodians, active status, and supported item categories.
- **ORG-007** — Each BU/OU may contain multiple warehouses.
- **AST-003** — Stock movements must support receipt, transfer, assignment, return, adjustment, and disposal with actor, time, quantity, reason, and source.
- **AST-004** — Physical assignment of interchangeable stock must use FIFO unless an authorized user records an exception.
- **AST-006** — Stock quantity and value must be derivable from an append-only movement history.
- **ARCH-003** — The budget ledger, stock movement history, audit trail, and approval decisions are append-only records.
- **AST-005** — Perpetual inventory valuation must use weighted average purchase cost. FIFO assignment and weighted-average valuation must remain distinct rules.
- **AST-007** — Negative stock must be prevented unless an authorized policy explicitly permits it.
- **AST-008** — The fixed-asset register must support asset number, description, category, project, PO/line, purchase cost, currency, acquisition date, location, custodian, serial number, and status.
- **ORG-006** — Each BU/OU must maintain or link to a fixed-asset register.
- **AST-009** — Assignment from warehouse stock to a project, location, or custodian must preserve lineage to the originating purchase.
- **AUD-007** — Financial events must retain immutable lineage from project balance to source document and approval.
- **AST-010** — Stock-count adjustments must require reason and approval when configured.
- **BUD-007** — Manual adjustments/transfers require a reason, effective date, supporting evidence when configured, and approval when applicable.

## Questions for the operator before building (drafted, not asked yet)

1. Goods receipt arrives with M4: is the PO detail the entry point, or a warehouse screen?
2. Who may override FIFO, and is the reason free text or a list?

## Reuse

The shell, `.page`, `.card`, `.tabs`, `.toolbar`, `.row` grids, `.pill`, `.sig`, `.fold`, `.dialog`, `.sheet`, `.summary`, `.action-bar` and the required-field tag from `xlr8flo.css`; `design-system/MASTER.md` sections 2.3, 7.1 to 7.16 and 8 for components. A new component goes on the Design System sheet in `design-system/canvas/` (`ThemePanel.dc.html`) with a test in `designsystem.spec.mjs`.

## Not here

A story to build the screens. Raise one per screen group in `design-system/canvas/STORY-NOTES.md` when the page is drawn.
