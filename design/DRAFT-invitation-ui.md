# DRAFT — Invitation acceptance screen: `/invite/:token`

**STATUS: story request, not a packet.** No roadmap entry, no id. Split from DRAFT-invitation so a cheap author can take it (`ui/S`).

**Epic** E05 · **M1** (proposed) · **ui/S** · after **DRAFT-invitation** (new-account path), **E05-S10** (card, backdrop, fields); existing-account state after **DRAFT-multi-org-membership** · tags: `auth`, `ui`

> **Conventions:** read `docs/m1-conventions.md`, `design-system/MASTER.md` (binding), `design-system/pages/sign-in.md`, then `design-system/pages/invitation.md` (both on branch `worktree-canvas-project-dashboard`; copy into the worktree first). The page file is the layout, copy, states and motion; **copy is verbatim.** Types come from the generated OpenAPI client.

## notes.blocked

Copied unchanged from `invitation.md` §6 "Still open". Not decided here:

- **Does a resend restart the 7 days?** This page assumes yes.
- **Multi-organization membership is not built.** E05-S09 D-M1-23 says one identity belongs to exactly one organization. The existing-account path and the chooser depend on a story that changes that (DECISIONS.md, conflicts under 6c). Build the new-account path first; the existing-account path waits for it.
- **Data residency.** Separate tenants do not by themselves keep data in a country. This page shows a tenant label and makes no residency claim.
- The administrator's invite screen (who, role, scope, resend, withdraw) is not drawn.

Also: the existing-account state is not buildable until DRAFT-multi-org-membership lands; ship the new-account states first and leave the existing-account branch behind a clearly named guard recorded in `notes.followup`.

## Why this story exists

DRAFT-invitation delivers the API. Without a screen the email link opens nothing.

## Requirements

- **AUTH-003 / AUTH-004** — password follows NIST guidance, minimum 15 characters, paste allowed, no composition rules.
- **AUTH-007** — nothing on the screen reveals whether an account exists; every unusable link renders one message.
- **AUTH-006** — privileged roles continue to MFA enrolment after accept.
- **SEC-008 / TEN-010** — organization, role and scope shown come from the server record, never the URL.
- **A11Y-006** (AGENTS §3.4) — programmatic labels, field errors, summary, input preserved. **UX-004** loading/empty/partial/error states. **UX-005** errors say what happened, what was kept, how to recover.

## Contract

Route `/invite/:token`, outside the app shell, TanStack Router per existing conventions. On mount call `GET /api/v1/invitations/by-token` with the token in the `X-Invitation-Token` header; the token is read from the path **once**, then the URL is replaced with `/invite` via `history.replaceState` so it is not in history, and never goes to analytics, logs or error text. The document sets `Referrer-Policy: no-referrer` for this route (meta tag or header via the existing static-header mechanism; state which).

States, exactly as `invitation.md` §5: Checking · Form · Field errors · Server unreachable · Expired · Unavailable · Existing account (guarded, see notes) · Accepted. Left panel per §3 (aside only for form, field-errors, network and done; generic left panel for checking, expired, unavailable). Form per §4: read-only email (`readonly`, `aria-readonly="true"`, `--sunken`), full name (prefilled, editable, required), password (`autocomplete="new-password"`, Show/Hide with `aria-pressed`, at least 15, paste allowed), terms checkbox, `Accept and create account`. Validation, summary, focus and kept input exactly as `sign-up.md` §2 if that story exists; otherwise implement the same pattern from E05-S10's sign-in form and record `notes.followup`. Server password rejection clears the password and keeps everything else. After accept: **Accepted** state; the button goes to `/sign-in/mfa/enroll` for roles needing MFA, else `Continue to XLR8 FLO` to Home; lifetime text reads the server's expiry value, not a hardcoded 7.

The accept POST sends `Idempotency-Key` (one key per form submission, reused on retry). Tokens, passwords and the recovery data never appear in `console`, error boundaries or telemetry.

## Acceptance criteria

- [ ] Valid token happy path to the Accepted screen; organization, tenant label, inviter, role, access and expiry on the screen come from the fixture and change when it changes
- [ ] Email field rejects edits (typed keys and paste); the POST never sends an email field
- [ ] Expired renders `Invitation Expired` with the nothing-changed box; used, withdrawn, malformed and unknown tokens render `Invitation Unavailable` with **byte-identical** HTML and no organization text anywhere in the DOM
- [ ] Token absent from the address bar and history after load; absent from console output and the network request URL (header only)
- [ ] Every validation message from `sign-up.md`/the page file; input preserved after failure; password cleared on server rejection; network failure keeps input and says nothing was submitted
- [ ] `@axe-core/playwright` zero violations on every state in light and dark; keyboard-only path to accepted; phone width 375 keeps the four-row list visible
- [ ] Reduced motion respected; transitions within MASTER limits; tokens only (no hardcoded colour, spacing, radius, duration); Phosphor icons; MASTER.md §8 checklist in the commit body
- [ ] Existing-account state: signed-in address differing from the invited one renders Unavailable (only when its backing story has landed; otherwise recorded in `notes.followup`)
- [ ] Plant: render the organization name on the Unavailable screen and the no-organization-text test fails; leave the token in the URL and the history test fails; make the email input editable and the read-only test fails

## Files

`apps/web/src/routes/invite*.tsx`, `apps/web/src/features/invitation/**`, `apps/web/e2e/invitation.e2e.ts`, unit tests beside the feature, the route registration, regenerated OpenAPI client usage. Reuse the auth card, backdrop and field components from E05-S10; do not fork them.

## Out of scope

The administrator's invite screen (who, role, scope, resend, withdraw) · sign-up UI · password reset · SSO · the chooser and switcher (DRAFT-multi-org-membership) · any API change.

## Dependencies

DRAFT-invitation (API), E05-S10 (shared auth layout and MFA enrolment screen). Existing-account state: DRAFT-multi-org-membership.
