# Handoff: Organization and Administration (registered 14 Oct 2026, nothing drawn yet)

Structure, master data and tenant-level administration.

**Page in the canvas:** `org-admin` in `design-system/canvas-2/project/canvas.json` (second canvas, https://claude.ai/artifact/62JVFUjfv8Bw3KiKXEWs5r). Method, anti-goals, self-review and tests are those of `design-system/canvas/HANDOFF-m3.md` sections 3 to 6, unchanged: Operate mode, tokens only, restraint, no disabled submit, status never by colour alone, boards at 1440 plus 1920, 1024, 768 and 375, every state, a spec file with axe in light and dark, and a page file `design-system/pages/<page>.md` for the coding agents. **Confirm the screen list and the questions below with the operator before building**, record the answers in `design-system/canvas/DECISIONS.md` (the one decision log, numbering continues), then draw.

## Screens

| Screen | Roadmap story | Requirements |
|---|---|---|
| Organization and BU/OU tree, settings and their precedence | E05-S01 | ORG-001, ORG-002, ORG-012 |
| Bill To and Ship To addresses with effective dates | E05-S02 | ORG-005 |
| Master data lists: departments, ledger accounts, UoM, tax codes, payment terms, categories | E05-S03 | ORG-009, ORG-010, ORG-011, XFN-009 |
| Fiscal calendar and period open and close | E05-S04 | BUD-010, FIN-010 |
| FX rates: ECB ingest, rate table, effective dating, missing-rate handling | E05-S05 | FX-001, FX-002, FX-003, FX-004, FIN-003 |
| Custom field definitions | E16-S01 | CUS-001, CUS-002, CUS-003, CUS-010 |
| Label groups and tags | E16-S07 | TAG-004, TAG-005, TAG-006, TAG-011, TAG-012, TAG-013, TAG-014 |
| Import wizard: template, mapping, dry-run preview, error report, progress | E08-S01 | IMP-001, IMP-002, IMP-003, IMP-004, IMP-005, IMP-006, IMP-007, IMP-008, IMP-009, IMP-012 |
| Tenant data export and tenant deletion | E18-S07 | TEN-008, COMP-005, PRIV-005, TEN-009, PRIV-002, PRIV-003, PRIV-006, AUD-008 |

## Requirement text (verbatim from `docs/requirements.md` and `docs/claude-plan.md`)

- **ORG-001** — An organization must support multiple Business Units and/or Operating Units, each with a unique code.
- **ORG-002** — Approval configuration must be definable organization-wide or per BU/OU, with a documented precedence rule.
- **ORG-012** — Authorized users must be able to determine which setting applies when organization and BU/OU configurations overlap.
- **ORG-005** — Each BU/OU must maintain Bill To and Ship To addresses with effective dates.
- **ORG-009** — Codes referenced by posted transactions must be deactivated rather than deleted.
- **ORG-010** — Departments, ledger accounts, currencies, units of measure, tax codes, payment terms, and item/service categories must be governed master data.
- **ORG-011** — Master-data records must support effective dates and active/inactive status.
- **XFN-009** — Foreign-key fields such as department, ledger account, vendor, project, and item must use scoped dropdown/type-ahead selection rather than free text.
- **BUD-010** — Fiscal calendars must be configurable by organization.
- **FIN-010** — Closing or reopening a financial period must require authorization and create an audit record.
- **FX-001** — Each organization must declare a base currency; documents retain their original currency (FIN-002).
- **FX-002** — Rates come from the ECB daily reference feed, stored with rate, source, and effective date, retained permanently.
- **FX-003** — A conversion shown to a user must display rate, source and effective date, and must never mutate the source document (SRC-010, FIN-003).
- **FX-004** — A missing rate for a required date must block the conversion with an actionable error, never fall back to a stale rate silently.
- **FIN-003** — A transaction must not mix currencies unless its exchange-rate policy, rate, source, and effective date are recorded.
- **CUS-001** — Authorized administrators must be able to add typed custom fields to supported document types without a code deployment.
- **CUS-002** — Field types must include text, number, money, date/time, boolean, single/multi-select, and reference where feasible.
- **CUS-003** — Definitions must support label, help text, required status, default, validation, scope, effective dates, display order, and permissions.
- **CUS-010** — Deleting a field with historical values must be prohibited; it may be retired and hidden from new entry.
- **TAG-004** — Tenants must be able to define **tags** (custom labels) applicable to configured document types, independent of status.
- **TAG-005** — Tags must be organized into **label groups** — named dimensions such as Risk Tier, Funding Source, CapEx Category — that own their tags.
- **TAG-006** — A label group must declare single- or multi-select, whether it is required on a document type, and whether it is a **reporting dimension**.
- **TAG-011** — A tag referenced by any document must not be deletable; it may be retired, hiding it from new entry while preserving history (consistent with ORG-009).
- **TAG-012** — Tag and group names are tenant-supplied data: length-capped, never rendered as raw HTML, and always shown with their group as context in filters (`Risk Tier: High`) so groups may share tag names.
- **TAG-013** — Tags must be scoped by organization and BU/OU, and must respect field-level authorization where a group is marked sensitive.
- **TAG-014** — Tag and group changes must be audited, and must not retroactively reinterpret historical values (consistent with CUS-007).
- **IMP-001** — The system must support robust Excel and CSV imports aligned to the application’s data model.
- **IMP-002** — Import templates must declare required columns, data types, accepted codes, examples, and template version.
- **IMP-003** — A mapping step must allow source columns to map to fields, including approved custom fields.
- **IMP-004** — Imports must validate file structure, formulas/macros policy, types, references, permissions, duplicates, and business rules before commit.
- **IMP-005** — Users must receive row/column-level errors and a downloadable error report.
- **IMP-006** — A dry-run preview must show creates, updates, skips, warnings, and errors before commit.
- **IMP-007** — The committed batch must be atomic where practical, or explicitly report partial outcomes with recovery instructions.
- **IMP-008** — Imports must be idempotent when a documented external key or batch key is reused.
- **IMP-009** — Each batch must retain uploader, time, file checksum, mapping/template version, counts, result, and resulting record identifiers.
- **IMP-012** — Large imports and exports must run asynchronously with progress, cancellation where safe, and completion/failure notification.
- **TEN-008** — A tenant must be able to export all its business data and attachments in documented formats, unattended (COMP-005).
- **COMP-005** — The organization must be able to export its business data and attachments in documented formats.
- **PRIV-005** — Data exports and sensitive-record access must be attributable and subject to authorization.
- **TEN-009** — Tenant deletion must honour financial and audit retention, de-identify where history must remain, and produce a certificate of deletion.
- **PRIV-002** — Data classes, owners, retention periods, and deletion/archive behavior must be documented.
- **PRIV-003** — Record deletion must honor financial/audit retention and legal holds; de-identification should be used where history must remain.
- **PRIV-006** — Backup expiry and deletion must align with the approved retention policy.
- **AUD-008** — Audit retention and legal hold must be configurable according to organizational and jurisdictional policy.

## Questions for the operator before building (drafted, not asked yet)

1. Is the organization tree edited in place, or through a versioned proposal that takes effect on a date?
2. Who may close a fiscal period, and can it be reopened?
3. Which master data lists are tenant-editable and which are seeded and read-only?
4. Does tenant deletion show the retention and legal-hold effect before it is confirmed?

## Reuse

The shell, `.page`, `.card`, `.tabs`, `.toolbar`, `.row` grids, `.pill`, `.sig`, `.fold`, `.dialog`, `.sheet`, `.summary`, `.action-bar` and the required-field tag from `xlr8flo.css`; `design-system/MASTER.md` sections 2.3, 7.1 to 7.16 and 8 for components. A new component goes on the Design System sheet in `design-system/canvas/` (`ThemePanel.dc.html`) with a test in `designsystem.spec.mjs`.

## Not here

A story to build the screens. Raise one per screen group in `design-system/canvas/STORY-NOTES.md` when the page is drawn.
