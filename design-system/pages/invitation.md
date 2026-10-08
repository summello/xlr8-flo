# Page spec: Invitation acceptance

**Stories:** none yet. E05-S10 lists "invitation acceptance" as out of scope and no roadmap story owns it. See `design-system/canvas/STORY-NOTES.md`, item 1, for the story request. **Read first:** `design-system/MASTER.md`, then `design-system/pages/sign-in.md`, which this page inherits (card, backdrop, fields, summary, 44px controls, motion, overrides E-2 to E-4). Only the differences are written here.

**Source of truth:** canvas boards `SignInInvite`, `SignInInviteErrors`, `SignInInviteNetwork`, `SignInInviteChecking`, `SignInInviteExpired`, `SignInInviteGone`, `SignInInviteDone`, `SignInInviteW375`. Requirements: AUTH-001, 003, 004, 005, 006, 007, 008, 011, SEC-006, SEC-008, TEN-010, A11Y-006, UX-004, UX-005.

---

## 1. What it is

A person receives an email from an administrator, opens the link, and joins **the tenant that invited them**, with the role and access the administrator chose. It is the only way a person joins an existing tenant (self-serve sign-up creates a new one). **Only users with an administrator role can invite.** A person may belong to several tenants, including two tenants of the same organization (for example two portfolios under different data laws); a tenant is identified by organization name plus a **tenant label**, such as `Northwind Capital, EMEA`.

**Route:** `/invite/:token`. Outside the app shell. The token is a bearer secret: it never appears in logs, analytics, referrers (`Referrer-Policy: no-referrer` on this route), or error messages.

## 2. What the UI must never do (security shape, binding)

1. **The organization, role and scope come from the invitation record on the server, never from the URL, a query string, a header or the request body** (AGENTS 3.2: `org_id` resolves from the session; here from the invitation, then from the new session).
2. **The email address is fixed by the invitation.** It is shown read-only (`readonly`, `aria-readonly="true"`, `--sunken` ground) and cannot be changed. If the person needs a different address, an administrator re-invites.
3. **One message for every unusable link** except expiry: used, withdrawn, malformed and unknown tokens all render `Invitation Unavailable`. The unavailable screen shows **nothing** about the organization (the aside is the generic one). Do not reveal whether a token ever existed.
4. Token: single use, short lived, hashed at rest, constant-time lookup, invalidated on accept (AUTH-008). Accepting is a state-changing POST: it takes `Idempotency-Key` and goes through the kernel middleware; a replayed key returns the first result.
5. Accepting is audited (AUTH-011): who invited, who accepted, role, scope, time, client, never the token or password.
6. The login throttle (E05-S11) applies to the token endpoint as well, with the same uniform response.

## 3. Layout

The card from `sign-in.md`, with a **different left panel**: the same brand row, then the headline **Join Northwind Capital, EMEA on XLR8 FLO** (organization name and tenant label from the invitation, `t-title` 26/32), one sentence `Amara Okafor invited you to plan and track capital projects together.`, a key/value list (labels `t-label`, values body 13px, 110px label column):

| Label | Value |
|---|---|
| Invited By | inviter name and job title |
| Tenant | organization name and tenant label |
| Your Role | role name |
| Access | the scope in words (business unit, project, or "all business units") |
| Expires | absolute date and relative time, e.g. `14 Oct 2026, in 6 days` |

and the caption `Your organization and role come from this invitation, not from the link you opened.` (use `--fg-secondary`).

Below 768 the panel shrinks to the strip described in `sign-in.md`: brand row, headline, the four-row list and the caption stay; only the one-sentence intro is dropped. The list must stay visible on a phone, because it is how the person confirms who is inviting them to what.

The aside is shown only for the form, field-errors, network and done states. Checking, expired and unavailable show the generic left panel.

## 4. The form (`Accept Your Invitation`)

Subline `Choose a password to finish creating your account.`

| Control | Spec |
|---|---|
| Email, from the invitation | read-only, see §2 |
| Full name | `autocomplete="name"`. Prefilled if the administrator supplied one, still editable. Required. |
| Password | as sign-up: `autocomplete="new-password"`, Show/Hide, at least 15 characters, no composition rules, common and breached values refused, paste allowed. |
| Terms | checkbox, required |
| Submit | `Accept and create account` |
| Footer | `Already have an account? Sign in to accept`. Signs in, then shows the existing-account screen in section 5. |

Validation, summary, focus, kept input: exactly as `sign-up.md` §2. Server rejection of the password clears it; everything else stays. Network failure: warning summary `We could not reach the server`, body `Nothing was submitted. What you typed is kept. Try again.`

## 5. States

| State | Heading | Copy | Notes |
|---|---|---|---|
| Checking | `Checking Your Invitation` | `This takes a moment.` | `role=status`; four skeleton rows at the final field height (44px, last 60% wide). Generic left panel. Shown while the token is validated. |
| Form | `Accept Your Invitation` | see §4 | Aside shows the invitation. |
| Field errors | same | summary + field errors, input kept | |
| Server unreachable | same | warning summary | |
| Expired | `Invitation Expired` | `Invitations last 7 days. Ask the person who invited you to send a new one.` + info box `Nothing was changed` / `No account was created from this link.` + `Go to sign in` | Expiry is the one distinct case, because it tells the person what to do and reveals nothing about the account. |
| Unavailable | `Invitation Unavailable` | `This link has already been used, was withdrawn, or is not valid. Ask the person who invited you for a new one.` + `Go to sign in` | Used, withdrawn, malformed and unknown all land here. |
| Existing account | `Join Northwind Capital` | Shown after sign in (and code) when the person already has an account. Subline `You are signed in as <email>.` Info box `Your other organizations are not affected` / `Accepting adds <tenant> to your account as a separate tenant. Its data stays separate from the others.` Buttons `Accept and join` (primary) and `Not now`. The signed-in address must equal the invited address; if not, render **Unavailable**. Accepting adds a membership; it never moves or merges data between tenants. |
| Accepted | `Welcome to Northwind Capital, EMEA` | `Your account is ready. One more step: your role needs two-step verification.` + info box `Account created` / `You can sign in with <email>.` + `Set up two-step verification` | If the role does not require MFA, the button reads `Continue to XLR8 FLO` and goes to Home. The next screen is the enrolment screen from `sign-in.md`. |

Invitation lifetime is **7 days from the moment it is sent**, shown in the copy. Treat the number as a setting; the copy must read it from the same value. **Resend** (administrator action, required): issues a new link, invalidates the previous one immediately (the old link then renders **Unavailable**), and restarts the 7 days. An unaccepted invitation expires at 7 days and cannot be revived, only resent. After accepting, a person with two or more memberships sees **Choose an Organization** at their next sign in (`sign-in.md` section 3b).

## 6. Decisions (operator, 9 Oct 2026) and what is still open

Decided: (1) a person may belong to several tenants, and one organization may exist as several tenants; (2) only administrators invite, for now; (3) resend is required and invitations expire after 7 days unaccepted; (4) privileged roles must enrol in two-step verification before first use.

Still open:
- **Does a resend restart the 7 days?** This page assumes yes.
- **Multi-organization membership is not built.** E05-S09 D-M1-23 says one identity belongs to exactly one organization. The existing-account path and the chooser depend on a story that changes that (DECISIONS.md, conflicts under 6c). Build the new-account path first; the existing-account path waits for it.
- **Data residency.** Separate tenants do not by themselves keep data in a country. This page shows a tenant label and makes no residency claim.
- The administrator's invite screen (who, role, scope, resend, withdraw) is not drawn.

## 7. Tests

Valid token happy path to the accepted screen; the organization, role and scope on the screen come from the server fixture and change when it changes; the email field rejects edits; expired, used, withdrawn and unknown tokens (the last three assert byte-identical HTML and no organization text); a replayed `Idempotency-Key`; a resend invalidates the previous link; an invitation unaccepted after 7 days renders Expired; only an administrator can issue one (a non-admin gets 404, never 403); the existing-account path rejects a signed-in address that differs from the invited one; token never in a log line or referrer; every validation message; input preserved; axe in light and dark on every state; keyboard-only path; phone (375).
