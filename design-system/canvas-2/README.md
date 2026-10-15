# Second design canvas

Source of the Claude Design canvas **XLR8 FLO Design System 2** (https://claude.ai/artifact/62JVFUjfv8Bw3KiKXEWs5r, private). Created 14 Oct 2026 because the first canvas (`../canvas/`, https://claude.ai/artifact/95sB8BhSTJnNetSkmmNCNn) reached the 512-file limit of a canvas.

> **Authority: this folder is a drawing, not a specification of values.** Same rule as `../canvas/README.md`: `../MASTER.md` and `apps/web/src/styles/tokens.css` win, then `../pages/*.md`.

## What is here

| Page | State | Boards |
|---|---|---|
| Common Screens | built (sign in, two-step verification, sign up and verify, invitation); the remaining screens are listed in `HANDOFF-common-screens.md` | 27 |
| Vendors | built (DECISIONS 6h, `../pages/vendors.md`) | 60 |
| Home and Reporting, Organization and Administration, Projects and Budget, Requisitions and Approvals, Assets and Warehouses, Cross-Cutting Surfaces | **registered, nothing drawn**: one `HANDOFF-<page>.md` brief each, listing the screens (every GAP in `../../docs/ui-screen-inventory.md`), their stories and requirement text | 0 |

The pages are grouped by module, the way the first canvas is.

## How the two canvases relate

- **One decision log:** `../canvas/DECISIONS.md` (items 6m onward cover this split). **One design system sheet:** `ThemePanel.dc.html` and `DesignSystem.dc.html` stay in the first canvas; a new component is added there with a test in `designsystem.spec.mjs`.
- **`project/xlr8flo.css` is a copy** of `../canvas/project/xlr8flo.css`. Change it in the first canvas and copy it here; `tests/css-sync.spec.mjs` fails if they differ.
- **Cross links** between canvases are absolute links to the other canvas's page (a board cannot be deep-linked); the five links from the first canvas to a vendor profile open this canvas in a new tab.
- Boards here link to no board in the first canvas.

## Tests

```bash
cd design-system/canvas-2/tests
ln -s "$(npm root -g)" node_modules      # once, gitignored
# in the SAME shell call: DC_RUNTIME (artifact-type/dc-runtime.js from either canvas) and AXE_CORE
DC_RUNTIME=... AXE_CORE=../../../apps/web/node_modules/axe-core/axe.min.js PORT=4180 npx playwright test --workers=1
```

Port 4174 is the default so both canvases can be served at once. Specs: `auth.spec.mjs`, `vendors.spec.mjs`, `copy.spec.mjs`, `required.spec.mjs`, `lint.spec.mjs`, `pages.spec.mjs`, `css-sync.spec.mjs`. The two onboarding decision panels for a vendor request are boards on the Approval Inbox page of the first canvas and are tested there (`../canvas/tests/vendor-decision.spec.mjs`).
