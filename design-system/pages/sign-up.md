# Page spec: Sign up and verify your email

**Stories:** E18-S02 is back end only (self-serve signup with email verification and anti-abuse controls). **There is no UI story yet.** See `design-system/canvas/STORY-NOTES.md`, item 2. **Read first:** `design-system/MASTER.md`, then `design-system/pages/sign-in.md`, which this page inherits entirely (card, panels, backdrop, field and summary patterns, 44px controls, motion, overrides E-2 to E-4). Only the differences are written here.

**Source of truth:** canvas boards `SignInSignup`, `SignInSignupErrors`, `SignInVerify` in `design-system/canvas/project/`. Requirements: AUTH-002, 003, 004, 005, 007, 008, SEC-006, SEC-007, A11Y-006, UX-005.

---

## 1. Routes

| Route | Screen |
|---|---|
| `/sign-up` | Create your account |
| `/sign-up/verify` | Check your email (shown after submit) |
| `/verify?token=…` | Landing page for the emailed link: success or expired. **Planned, not designed yet** (DECISIONS 6c item 2). |

Outside the app shell. Shown only when self-serve sign-up is enabled for the deployment; when it is off, `/sign-up` returns 404 and the `Create an account` link on sign in is hidden.

## 2. Create your account

Heading `Create Your Account`, subline `Start with your work email. We verify it next.`

| Control | Spec |
|---|---|
| Full name | `autocomplete="name"`. Required. |
| Company | `autocomplete="organization"`. Required. Becomes the tenant name; the person becomes its first administrator (E18-S01 and E18-S02 own that). Name and Company sit side by side in a 2-column grid that wraps below 200px per cell. |
| Work email | `type=email`, `autocomplete="email"`. Required. |
| Password | `autocomplete="new-password"`, Show/Hide toggle, hint `At least 15 characters. A passphrase works well; pasting from a password manager is fine.` |
| Terms | Checkbox `I agree to the terms of service and privacy notice.` Required. |
| Submit | `Create account`, primary, full width. |
| Footer | `Already have an account? Sign in`. |

**Password rules come from the requirements, not from taste.** AUTH-004: at least 15 characters when the password may be the only factor (it is, at sign-up). AUTH-003: allow long passphrases and paste; **no composition rules** (no "must contain a symbol"), **no strength meter that scolds**; refuse values that are common or known to be compromised. Do not truncate, trim or restrict characters. Never show the password back except through the Show toggle.

### Validation

Client checks run on submit, the server repeats them. On failure: error summary (`role=alert`, focused) titled `Fix N things to continue` (real count), each item a link to its field, each field marked `aria-invalid` with an icon and text error under it, **all typed input kept** (the password too is kept here because the user chose it; it is cleared only on a server-side rejection). Exact messages:

| Field | Message |
|---|---|
| Full name | `Enter your full name.` |
| Company | `Enter your company name.` |
| Work email | `Enter a valid work email.` |
| Password, short | `Use at least 15 characters.` |
| Password, common or breached | `This password is too common or has appeared in a breach. Choose another.` |
| Terms | `Agree to continue.` |

## 3. Check your email (`/sign-up/verify`)

Heading `Check Your Email`. Body: `If <address> can create an account, a verification link is on its way. It expires in 24 hours.` **The wording is deliberately conditional** (AUTH-007): the response and this screen must be identical whether or not the address already has an account. Never say "that email is already registered" anywhere in the flow.

- Info box: `Nothing to do here once you click the link: it brings you back and finishes setup.`
- Buttons: `Resend link` and `Use a different email`. Resend is limited to once a minute; the toast says `A new link is on its way. You can resend again in a minute.` Caption: `You can resend once a minute. Check your spam folder if it does not arrive.`
- The verification token is single use, short lived, generated securely and invalidated after use (AUTH-008).

## 4. Anti-abuse (open)

E18-S02 lists "anti-abuse controls" without naming one. **No challenge widget is drawn.** If the story adds one, it goes between the terms checkbox and the button, must have a non-visual alternative, must keep all typed input on failure, and needs its own state on this page. Do not guess; raise it in `notes.blocked`.

## 5. Tests

Happy path to the verify screen; each validation message above; the error summary takes focus and links work; input preserved after failure; the verify copy is identical for a new and an existing address (assert the same HTML); resend toast; axe in light and dark on `/sign-up` and `/sign-up/verify`; keyboard-only path; phone (375) no sideways scroll and 44px targets.
