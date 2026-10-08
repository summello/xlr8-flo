# Handoff: remaining screens

Read `DECISIONS.md` first (everything decided so far), then `README.md` (how to run the tests). Do not amend `design-system/MASTER.md` unless the operator says so.

## State

**Common Screens page started: sign in, two-step verification and sign up are built (DECISIONS 6c); the remaining common screens are planned there. Requisition Create is decided and waits (6b).**

**Done: Project Dashboard page** (see DECISIONS 6a). Next: Requisition Create, then Approval Inbox, Budget Transfer, Comparison Dashboard, Common Screens. Variants are wrappers (`ListW1024`, `DashLoading`...) over the four main boards; edit the `.dc.html` files directly.

- Canvas https://claude.ai/artifact/95sB8BhSTJnNetSkmmNCNn, three pages: **Playground**, **Project Dashboard** (Home, Executive Dashboard and their width variants) and **Design System** (the sheet and the theme panel). Source: `design-system/canvas/project/`; branch `worktree-design-canvas-xlr8flo`; work in a worktree, commit as `docs(design): ...`, push, never merge, no PR to `main`.
- The Executive Dashboard is the layout reference. Home is registered exception E-1.
- Inputs: `docs/ui-screen-inventory.md` (screens, roles, gaps), `design-system/MASTER.md` (binding), the sheet on the Design System page.

## Task: build the remaining screens as canvas pages

Add one canvas page per key flow, in this order, plus one page for common screens. Use `pages` in `project/canvas.json` (`{"id","name"}`) and give each board a `"page"`. Page names are Title Case.

| Page | Contents (from the inventory) |
|---|---|
| Project Dashboard | project dashboard with hierarchy visual, drill to ledger entries; project list and detail as needed |
| Requisition Create | header, item and service lines, catalogue suggestions, submission validation summary |
| Approval Inbox | pending, overdue, completed, delegated; decision panel with routing explanation |
| Budget Transfer | same level and cross-hierarchy, preview of both ancestries (signature moment 3) |
| Comparison Dashboard | RFQ bid comparison with the accessible table equivalent |
| Common Screens | sign in, MFA (challenge, enrol, recovery), password reset, sign up and verify, suspended tenant notice, session expired, logout, 403/404/500 with correlation id, demo banner, empty and error patterns, role-based onboarding tour |

Sign in belongs to Common Screens, not the flows (the inventory's "sign in" flow is covered there). Confirm the screen list with the operator before building anything that is a gap in the inventory.

## For each page

1. Boards: desktop 1440 plus 1024, 768, 375 and 1920 variants. Variants are tiny wrapper files that `dc-import` the main board (see `HomeW1024.dc.html`). Keep boards inside one page non-overlapping.
2. Reuse the shell exactly (copy from `Main.dc.html`; the DC format has no slot for a shared shell). Reuse `xlr8flo.css` classes before adding any; new classes go in that file, tokens only.
3. Follow the dashboard layout rule and the conventions in `DECISIONS.md` (equal card heights, pinned chrome, hover rules, money format, Title Case headings, 44px targets below 1024, stacked cards, tooltips on charts).
4. Every screen needs loading, empty, partial and error states, a keyboard path, visible focus, and a reduced-motion end state.
5. A new component or icon goes into the Design System page in the same change (`ThemePanel.dc.html`) with a test in `designsystem.spec.mjs`.
6. A flow gets a spec file: its happy path, keyboard path, states, axe in light and dark. Any new gate ships a test that plants a violation.
7. Run the whole suite, publish, commit, push. Record new decisions and any new exception in `DECISIONS.md`.

## Gotchas learned

- DC templates trim whitespace at text-node edges; put a word between two inline elements in its own `<span>` with a margin.
- `{{ }}` holes are lookups only; compute in `renderVals()`. Set `autoFocus`, `onKeyDown`, `aria-*` from there too. No global key handlers.
- A fixed or absolute box with an `align-self` other than `auto` shrink-wraps its content. `.legend span` selectors hit nested spans; use `>`.
- `container-type: inline-size` is not a stacking context; a sticky element with a z-index is. Mind the layering of popover layers.
- Publishing: files edited on the canvas (including theme defaults set in the Tweaks panel) make a publish refuse. Read the file, merge, republish. Never resend an old copy. Heights in `canvas.json` should come from a measured run, not a guess.
- Playwright: `test.use({reducedMotion})` did not apply; use `page.emulateMedia`. Wait for the dashboard's `.intro` class to clear instead of sleeping. Compare `-0` carefully.
- The canvas runtime is not committed. Read `artifact-type/dc-runtime.js` from the artifact and pass it as `DC_RUNTIME`.

## Operator preferences

Restraint over flourish ("more is not always great"); decisions that need no per-item upkeep (no colour list per card); consistent rules with registered exceptions; plain, short summaries; ask only when a decision is truly the operator's.

## Paste-ready start

> Continue the XLR8 FLO design canvas (https://claude.ai/artifact/95sB8BhSTJnNetSkmmNCNn). Read `design-system/canvas/HANDOFF.md`, then `DECISIONS.md` and `README.md`. Enter a worktree. Build the pages listed in the handoff one at a time, starting with Project Dashboard, confirming the screen list with me first. Do not amend MASTER.md.
