# Handoff: Requisitions and Approvals (registered 14 Oct 2026, nothing drawn yet)

What remains of the requisition and approval screens after Requisition Create, Approval Inbox and the Workflow Builder.

**Page in the canvas:** `requisitions-approvals` in `design-system/canvas-2/project/canvas.json` (second canvas, https://claude.ai/artifact/62JVFUjfv8Bw3KiKXEWs5r). Method, anti-goals, self-review and tests are those of `design-system/canvas/HANDOFF-m3.md` sections 3 to 6, unchanged: Operate mode, tokens only, restraint, no disabled submit, status never by colour alone, boards at 1440 plus 1920, 1024, 768 and 375, every state, a spec file with axe in light and dark, and a page file `design-system/pages/<page>.md` for the coding agents. **Confirm the screen list and the questions below with the operator before building**, record the answers in `design-system/canvas/DECISIONS.md` (the one decision log, numbering continues), then draw.

## Screens

| Screen | Roadmap story | Requirements |
|---|---|---|
| Linked-record graph on the requisition detail: RFQs, bids, awards, POs, approvals, notes, ledger | E09-S09 | REQ-009, WF-001 |

## Requirement text (verbatim from `docs/requirements.md` and `docs/claude-plan.md`)

- **REQ-009** — RFQs, bids, comparisons, awards, POs, approvals, notes, attachments, and budget entries must remain linked.
- **WF-001** — A downstream record must never be created from an unauthorized or stale upstream version.

## Questions for the operator before building (drafted, not asked yet)

1. Is the graph a diagram, a tree, or the existing Linked Records list with a depth control?

## Reuse

The shell, `.page`, `.card`, `.tabs`, `.toolbar`, `.row` grids, `.pill`, `.sig`, `.fold`, `.dialog`, `.sheet`, `.summary`, `.action-bar` and the required-field tag from `xlr8flo.css`; `design-system/MASTER.md` sections 2.3, 7.1 to 7.16 and 8 for components. A new component goes on the Design System sheet in `design-system/canvas/` (`ThemePanel.dc.html`) with a test in `designsystem.spec.mjs`.

## Not here

A story to build the screens. Raise one per screen group in `design-system/canvas/STORY-NOTES.md` when the page is drawn.
