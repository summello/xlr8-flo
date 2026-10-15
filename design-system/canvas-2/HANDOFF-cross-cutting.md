# Handoff: Cross-Cutting Surfaces (registered 14 Oct 2026, nothing drawn yet)

Surfaces used by many pages.

**Page in the canvas:** `cross-cutting` in `design-system/canvas-2/project/canvas.json` (second canvas, https://claude.ai/artifact/62JVFUjfv8Bw3KiKXEWs5r). Method, anti-goals, self-review and tests are those of `design-system/canvas/HANDOFF-m3.md` sections 3 to 6, unchanged: Operate mode, tokens only, restraint, no disabled submit, status never by colour alone, boards at 1440 plus 1920, 1024, 768 and 375, every state, a spec file with axe in light and dark, and a page file `design-system/pages/<page>.md` for the coding agents. **Confirm the screen list and the questions below with the operator before building**, record the answers in `design-system/canvas/DECISIONS.md` (the one decision log, numbering continues), then draw.

## Screens

| Screen | Roadmap story | Requirements |
|---|---|---|
| Attachment upload with quarantine states, as its own reference surface | E09-S10 | COL-001, COL-002, COL-003, COL-004, COL-005, COL-006, COL-007, COL-008 |
| Internal and vendor-facing notes and attachments, shown in words | E13-S11 | PO-017, EML-005, COL-002 |

## Requirement text (verbatim from `docs/requirements.md` and `docs/claude-plan.md`)

- **COL-001** — All business documents must support timestamped notes and attachments.
- **COL-002** — Notes must identify author and visibility, including internal-only versus externally shareable.
- **COL-003** — Edited notes should preserve edit history; notes used as approval rationale must be immutable.
- **COL-004** — Attachments must retain original filename, generated storage key, media type, size, checksum, uploader, upload time, scan status, and linked record.
- **COL-005** — Uploads must use allow-listed extensions/types, content and signature validation, safe filenames, size limits, access control, and malware scanning.
- **COL-006** — Files must be stored outside the public web root and downloaded through an authorized, time-limited path or application response.
- **COL-007** — Quarantined, rejected, or unscanned files must not be available to ordinary users or external recipients.
- **COL-008** — Attachment versions, deletion/retention actions, downloads of sensitive files, and external sharing must be audited.
- **PO-017** — POs must support notes, terms, supporting attachments, and vendor-facing attachments. Internal-only content must never be included accidentally.
- **EML-005** — Vendor-facing email must never include internal-only notes or attachments (PO-017); this must be covered by an automated test.

## Questions for the operator before building (drafted, not asked yet)

1. Is the scan-state vocabulary the one already drawn on the requisition and vendor documents, or does it need a fuller set?

## Reuse

The shell, `.page`, `.card`, `.tabs`, `.toolbar`, `.row` grids, `.pill`, `.sig`, `.fold`, `.dialog`, `.sheet`, `.summary`, `.action-bar` and the required-field tag from `xlr8flo.css`; `design-system/MASTER.md` sections 2.3, 7.1 to 7.16 and 8 for components. A new component goes on the Design System sheet in `design-system/canvas/` (`ThemePanel.dc.html`) with a test in `designsystem.spec.mjs`.

## Not here

A story to build the screens. Raise one per screen group in `design-system/canvas/STORY-NOTES.md` when the page is drawn.
