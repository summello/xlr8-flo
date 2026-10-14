# Handoff: Common Screens (remaining screens) (registered 14 Oct 2026, nothing drawn yet)

Access and system screens still undrawn on the Common Screens page (DECISIONS 6c). The built boards stay on that page; these are added to it, in this canvas.

**Page in the canvas:** `common-screens` in `design-system/canvas-2/project/canvas.json` (second canvas, https://claude.ai/artifact/62JVFUjfv8Bw3KiKXEWs5r). Method, anti-goals, self-review and tests are those of `design-system/canvas/HANDOFF-m3.md` sections 3 to 6, unchanged: Operate mode, tokens only, restraint, no disabled submit, status never by colour alone, boards at 1440 plus 1920, 1024, 768 and 375, every state, a spec file with axe in light and dark, and a page file `design-system/pages/<page>.md` for the coding agents. **Confirm the screen list and the questions below with the operator before building**, record the answers in `design-system/canvas/DECISIONS.md` (the one decision log, numbering continues), then draw.

## Screens

| Screen | Roadmap story | Requirements |
|---|---|---|
| Password reset: request (the same message whether or not the address exists), set a new password, expired link | E02-S03 | AUTH-007, AUTH-008 |
| Recovery-code entry from the two-step challenge; email verified; verification link expired | E05-S10 | AUTH-006, AUTH-007 |
| Signed out, and session expired, as screens and not only the sign-in notice | E02-S06 | AUTH-001 |
| 403, 404 and 500 with a correlation id and a copy button (a foreign tenant record is always 404) | E20-S03 | SUP-001, SUP-002, OBS-006 |
| Suspended tenant notice (read-only grace period) | E18-S06 | TEN-007 |
| Demo-tenant banner and its states | E18-S09 | TEN-011, PRIV-004 |
| Empty and error patterns, as one reference board | E04-S03 | UX-004, UX-005 |
| Role-based onboarding tour, skippable | E17-S08 | UX-009, UX-010, SUP-005 |
| Organization switcher in the shell menu (DECISIONS 41) | E05-S09 | AUTH-001, ORG-001 |

## Requirement text (verbatim from `docs/requirements.md` and `docs/claude-plan.md`)

- **AUTH-007** — Authentication errors must not reveal whether an account exists.
- **AUTH-008** — Password reset tokens must be single-use, short-lived, securely generated, and invalidated after use.
- **AUTH-006** — MFA must be available and should be mandatory for privileged users; phishing-resistant methods should be preferred.
- **AUTH-001** — User profiles must link users to BU/OUs, roles, projects, locale, timezone, and notification preferences.
- **SUP-001** — Every user-facing error must display a correlation id that support can use without exposing business data (OBS-006).
- **SUP-002** — An in-app feedback control must capture the current route, correlation id and tenant.
- **OBS-006** — Support users must be able to use a correlation identifier without receiving unauthorized business data.
- **TEN-007** — Tenant suspension must block sign-in and writes while preserving reads and export for a configurable grace period.
- **TEN-011** — A demo/sandbox tenant with representative seed data must exist for sales and for agent development, and must be visibly marked as non-production.
- **PRIV-004** — Production data must not be copied to non-production environments without authorization and protection/de-identification.
- **UX-004** — Loading states must distinguish initial loading, incremental loading, empty results, and failure.
- **UX-005** — Errors must state what happened, what was preserved, and how the user can recover.
- **UX-009** — Role-based onboarding must introduce essential navigation, terminology, and first tasks without blocking experienced users.
- **UX-010** — Contextual help, quick-start guidance, and administrator/operator documentation must support lean training and self-service adoption.
- **SUP-005** — Administrator and operator documentation must be updated in the same PR as the feature it describes (UX-010).
- **ORG-001** — An organization must support multiple Business Units and/or Operating Units, each with a unique code.

## Questions for the operator before building (drafted, not asked yet)

1. Which sign-out and session-end reasons get their own wording: idle timeout, absolute lifetime, an administrator ending the session, a password change elsewhere?
2. After a session ends, does sign-in return the person to where they were, or to Home?
3. Is the suspended-tenant grace period shown with a date, and who sees it: everyone, or administrators only?
4. Does the onboarding tour differ by role, and is it replayable from Help?

## Reuse

The shell, `.page`, `.card`, `.tabs`, `.toolbar`, `.row` grids, `.pill`, `.sig`, `.fold`, `.dialog`, `.sheet`, `.summary`, `.action-bar` and the required-field tag from `xlr8flo.css`; `design-system/MASTER.md` sections 2.3, 7.1 to 7.16 and 8 for components. A new component goes on the Design System sheet in `design-system/canvas/` (`ThemePanel.dc.html`) with a test in `designsystem.spec.mjs`.

## Not here

A story to build the screens. Raise one per screen group in `design-system/canvas/STORY-NOTES.md` when the page is drawn.
