# Handoff: Home and Reporting (registered 14 Oct 2026, nothing drawn yet)

Screens that read across the whole product: reports, saved views, search and notifications.

**Page in the canvas:** `home-reporting` in `design-system/canvas-2/project/canvas.json` (second canvas, https://claude.ai/artifact/62JVFUjfv8Bw3KiKXEWs5r). Method, anti-goals, self-review and tests are those of `design-system/canvas/HANDOFF-m3.md` sections 3 to 6, unchanged: Operate mode, tokens only, restraint, no disabled submit, status never by colour alone, boards at 1440 plus 1920, 1024, 768 and 375, every state, a spec file with axe in light and dark, and a page file `design-system/pages/<page>.md` for the coding agents. **Confirm the screen list and the questions below with the operator before building**, record the answers in `design-system/canvas/DECISIONS.md` (the one decision log, numbering continues), then draw.

## Screens

| Screen | Roadmap story | Requirements |
|---|---|---|
| Report builder and viewer, with export and the as-of context header | E15-S08 | RPT-007, RPT-009, RPT-010, IMP-010, IMP-011 |
| Long reports and bulk exports: progress, and the notification when ready | E15-S09 | RPT-011, IMP-012, PERF-004 |
| Saved personal views and governed shared views: list, publish, archive | E15-S07 | RPT-008, XFN-003 |
| Global search results, authorized records only | E17-S01 | XFN-001, SEC-008, SEC-010 |
| Notifications: inbox and per-event preferences, locale, timezone, theme | E17-S02 | XFN-005, XFN-006, AUTH-001 |
| User preferences: locale, timezone, theme, notification settings, grid defaults | E17-S06 | AUTH-001, UX-007, XFN-003 |

## Requirement text (verbatim from `docs/requirements.md` and `docs/claude-plan.md`)

- **RPT-007** — Dashboard totals must reconcile to exported detail under the same filters and as-of time.
- **RPT-009** — Reports must support HTML, PDF, and Excel output.
- **RPT-010** — Exports must include report name, filters, scope, generation time, timezone, currency context, and page numbering where relevant.
- **IMP-010** — Exports must respect the user’s authorization, active filters, field-level restrictions, locale, timezone, and data-retention policy.
- **IMP-011** — Operational and executive reports must export to HTML, PDF, and Excel.
- **RPT-011** — Long reports must run asynchronously and must not block normal transactional work.
- **IMP-012** — Large imports and exports must run asynchronously with progress, cancellation where safe, and completion/failure notification.
- **PERF-004** — Imports, complex reports, bulk exports, email delivery, and recalculation must run as background jobs with visible progress.
- **RPT-008** — Users must be able to save personal views; authorized users may publish governed shared views.
- **XFN-003** — Grid and dashboard preferences must be saved per user without altering shared records.
- **XFN-001** — Global search must return only authorized records and support key identifiers, names, vendor, project, and document numbers.
- **SEC-008** — Authorization must prevent cross-organization, cross-BU/OU, cross-project, and object-identifier access.
- **SEC-010** — Authentication, recovery, import, export, search, and integration endpoints must have abuse-aware throttling.
- **XFN-005** — Notifications must support in-app and email channels with configurable event preferences, except mandatory security/administrative notices.
- **XFN-006** — Approval, due-date, document-expiry, failed-transmission, import/export, and material project-risk events must be eligible for notification.
- **AUTH-001** — User profiles must link users to BU/OUs, roles, projects, locale, timezone, and notification preferences.
- **UX-007** — Dates, numbers, and currencies must display in the user’s locale while preserving canonical stored values.

## Questions for the operator before building (drafted, not asked yet)

1. Which report types does a first release ship, so the builder is drawn for those and not for any report?
2. Who may publish a shared view, and who may retire one?
3. Does search return records the person can see but not open, or only records they can open?
4. Is the notification inbox the same surface as the approval inbox, or a separate one?

## Reuse

The shell, `.page`, `.card`, `.tabs`, `.toolbar`, `.row` grids, `.pill`, `.sig`, `.fold`, `.dialog`, `.sheet`, `.summary`, `.action-bar` and the required-field tag from `xlr8flo.css`; `design-system/MASTER.md` sections 2.3, 7.1 to 7.16 and 8 for components. A new component goes on the Design System sheet in `design-system/canvas/` (`ThemePanel.dc.html`) with a test in `designsystem.spec.mjs`.

## Not here

A story to build the screens. Raise one per screen group in `design-system/canvas/STORY-NOTES.md` when the page is drawn.
