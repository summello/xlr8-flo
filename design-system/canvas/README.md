# Claude Design canvas source

Source of the Claude Design canvas `xlr8flo-design-system` (https://claude.ai/artifact/95sB8BhSTJnNetSkmmNCNn, private).

- Files under `project/` mirror the published canvas paths.
- `project/xlr8flo.css` transcribes the tokens in `../MASTER.md` and adds the motion, state and overlay classes. It does not amend MASTER.md; MASTER.md stays the source of truth.
- Boards: `Main` (Home), `ExecutiveDashboard`, `DesignSystem` (+ `ThemePanel`), and width variants `HomeW*`, `ExecW*`, `HomeCollapsed`.
- Sample data only. Layout and interaction review, not a build spec.
- The canvas on claude.ai is the working copy; refresh these files from it when a round is approved.

## Tests

Playwright runs the real boards locally, with the canvas runtime served as `support.js`.

```
cd design-system/canvas/tests
ln -s "$(npm root -g)" node_modules        # or npm i -D playwright
DC_RUNTIME=<dc-runtime.js> AXE_CORE=<axe.min.js> npx playwright test
```

- `DC_RUNTIME`: the canvas's `artifact-type/dc-runtime.js` (read it from the artifact; it is not committed).
- `AXE_CORE`: defaults to `apps/web/node_modules/axe-core/axe.min.js` in a full checkout.
- `lint.spec.mjs`: no layout property animated, no colour literal in an artboard, every `var(--token)` resolves, a reduced-motion block exists. Each gate is also run against a planted violation.
- `shell.spec.mjs`: responsive overflow, sidebar modes, toggle placement, touch targets, stacked cards, popover, command menu, record sheet, toast.
- `motion.spec.mjs`: duration budget per element, reduced motion, intro count-up, tabs, tooltips.
- `a11y.spec.mjs`: axe in light and dark across the default, collapsed, popover, command menu, sheet, drawer and tab states. Includes a planted violation to prove the harness fails.
