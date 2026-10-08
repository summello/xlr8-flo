# Page spec: Sign in, two-step verification, enrolment, recovery codes

**Stories that build this:** E05-S10 (sign in, MFA challenge, enrolment, logout), E05-S11 (throttling, no UI of its own). **Read first:** `design-system/MASTER.md`, then this file. Where this file and MASTER disagree, this file wins for these routes only, and only where it says so (§9).

**Source of truth for the layout:** the canvas boards `SignIn*.dc.html` in `design-system/canvas/project/` (published on the Common Screens page of the design canvas). `xlr8flo.css` in that folder holds the classes (`.auth*`, `.summary`, `.field-err`, `.inp.lg`, `.code-in`, `.qr`, `.codes`, `.art-*`) as token-only CSS. Port them to Tailwind and tokens; do not copy values.

Decisions this page records: DECISIONS.md 6c, items 34 to 37. Requirements: AUTH-002, 003, 004, 006, 007, 009, SEC-007, A11Y-006, UX-004, UX-005.

---

## 1. Routes and what each one is

| Route | Screen | Notes |
|---|---|---|
| `/sign-in` | Sign in | The only public entry. An unauthenticated visit to any other route redirects here with `return` (same-origin relative path only; reject any scheme or `//`). |
| `/sign-in/mfa` | Code challenge | Reached after a correct password for a user with MFA. |
| `/sign-in/mfa/enroll` | Enrolment, then recovery codes | Reached when the role requires MFA and none is set up. The recovery-codes step is the second half of this route. |
| (shell menu) | Sign out | Not a page. Calls logout and returns to `/sign-in`. |

These screens live **outside the app shell**: no sidebar, no top bar.

## 2. Layout

One centred card, two panels side by side, over the backdrop.

- **Card:** `min(960px, 100% of viewport)`, grid `5fr / 6fr`, `--radius-card`, 1px `--border-strong`, `--shadow-lg`, solid `--surface`. Centred on both axes (vertical drift under 40px is fine). The card is always opaque and above the backdrop.
- **Left panel (aside):** `--sunken` ground, 1px `--border` on its right. Brand mark and name, the headline **One Record for Every Capital Dollar** (`t-title`, 26/32), one sentence, three points each with an icon in a 32px `--surface` tile: *Plan With Real Budgets*, *Approve With Confidence*, *Buy Through a Completed RFQ*. Captions use `--fg-secondary`, not `--fg-muted` (muted fails 4.5:1 on `--sunken`).
- **Right panel (main):** 40px padding (32px 28px at 768 to 1023). `h1` 28/34, one `secondary` line under it, then the form. Everything else is spaced 20px.
- **Below 768:** the card is full width with no radius, border or shadow and the panels stack. The aside shrinks to a strip: brand row and headline only (hide the points and the sentence). The form follows. Padding 16px.
- **Backdrop:** §8.
- Page background `--canvas`. Light and dark both: verify contrast in both themes, not only light.

## 3. Sign in (`/sign-in`)

Heading `Sign In`, subline `Use your work email and password.`

| Control | Spec |
|---|---|
| Email | label `Email`; `type=email`, `autocomplete="username"`; 44px (`.inp.lg`). |
| Password | label `Password`; `autocomplete="current-password"`; trailing text button `Show` / `Hide` with `aria-pressed`; 44px. |
| Forgot link | `Forgot your password?`, right-aligned under the password. Goes to the reset flow (planned, DECISIONS 6c item 1); until it exists, route to a stub that says so. |
| Submit | `Sign in`, primary, full width, 44px. While submitting: disabled, `aria-busy="true"`, label `Signing in`, a 16px spinner before it. |
| Footer | `New to XLR8 FLO? Create an account` (links to sign up). Hide this link when self-serve sign-up is off for the deployment. |

Tab order: email, password, Show, Forgot, Sign in, Create an account.

### States (exact copy)

| State | Summary title | Body | Focus and fields |
|---|---|---|---|
| Submitting | none | none | Button disabled and busy. |
| Empty fields | `Fix 2 things to continue` (count real) | links: `Enter your email address`, `Enter your password`, each to its field | Summary takes focus. Each bad field: `aria-invalid="true"`, error under it with an icon and `Enter your email address.` / `Enter your password.` |
| Wrong password, unknown email, **locked account, throttled attempt** | `We could not sign you in` | `The email or password is incorrect, or the account is locked. Try again or reset your password.` | Summary takes focus. **Email kept, password cleared.** No field is marked invalid. |
| Network failure | `We could not reach the server` | `Nothing was submitted. Your email is kept. Check your connection and try again.` | Warning style. Email kept. |
| Session ended | `Your session ended` (info) | `Sign in again to pick up where you left off.` | Shown after any 401; preserves `return`. |

**The four credential failures are one state.** E05-S10 D-M1-27 and E05-S11 D-M1-29 require the UI never to branch on which of them happened, and the server answers all four with the same 401 problem body. There is no "too many attempts" screen and no countdown. A test must assert that a throttled attempt renders the same alert, with no text matching `too many`, `attempts` or `wait`.

## 4. Code challenge (`/sign-in/mfa`)

Heading `Two-Step Verification`, subline `Enter the 6-digit code from your authenticator app.`

- One input, label `Verification code`, `inputmode="numeric"`, `maxlength=6`, `autocomplete="one-time-code"`, mono 24px, letter-spaced, centred. Autofocus on arrival.
- `Verify` primary. Below: `Use a recovery code` (planned screen; stub until built) and `Back to sign in`.
- **Invalid or expired code:** field error `That code is not valid or has expired. Enter the newest code.` with icon, `aria-invalid`, the typed digits **kept**. No account hint, no remaining-attempts number.
- Success: land on the stored `return` or Home.

## 5. Enrolment (`/sign-in/mfa/enroll`)

Heading `Secure Your Account`, subline `Your organization requires a second step. It takes a minute.`

1. Ordered list: step 1 `Scan this code with an authenticator app.` with the QR (148px, `--surface` tile, 1px `--border-strong`, `role=img`, label `Sample QR code for the authenticator app`; the real label says `QR code for your authenticator app`). Beside it: caption `Or enter this key by hand`, the key in mono grouped in fours, and a `Copy key` button. **The typed key is the non-visual route; it is required, not optional.** Step 2 `Enter the 6-digit code it shows.`
2. Code field (same as §4) and `Turn on and continue`.

### Recovery codes (second half of the route)

Heading `Save Your Recovery Codes`, subline `Each code works once if you lose your device. We show them only now.`

- 8 codes in a two-column box (`.codes`, mono 14px, `role=list` labelled `Recovery codes`).
- `Copy codes` and `Download` buttons.
- Checkbox `I saved these codes somewhere safe.` (18px box, 24px minimum row). `Continue to XLR8 FLO` stays **disabled until it is checked**.
- Codes are never logged, never put in a URL, and are shown once. Reloading the page must not show them again.

## 6. Accessibility (all verified by tests in the canvas, repeat them in the app)

- Every field has a programmatic label; errors are linked with `aria-describedby`; an error never relies on colour (icon and text).
- On failure, focus moves to the error summary (`role=alert`, `tabindex=-1`) and its links jump to the fields.
- 44px targets for inputs, buttons and the Show toggle at every width (these screens are used on phones).
- Visible focus ring everywhere (MASTER). No autofill blocking; `autocomplete` values as listed.
- axe: zero violations on all three routes in light and dark (E05-S10 acceptance).

## 7. Motion on the form (not the backdrop)

Card entrance: fade up 6px over `--dur-page` (320ms) ease-out, once. Panel content swap (password to code step): the same fade, 200ms. The spinner rotates 900ms linear. **No springs, no overshoot** (this is a ledger product). Reduced motion: the card appears at once and the spinner is replaced by the word `Signing in`.

## 8. Backdrop: the quiet capital-project scene

Decorative. `aria-hidden="true"`, `pointer-events: none`, no focusable children, `z-index: -1`, bottom-anchored, height `min(64vh, 560px)` (min 380px), faded upward with a mask. Drawn as outlines in token colours: `--fg-secondary` at 0.22 (skyline), 0.42 (structures), `--phase-plan` at 0.7 (crane jib), dotted grid at 0.35.

Elements: tower crane at the left edge (mast, lattice, jib, hook line and a beam), a frame building of 8 floors at the right edge, an excavator below the card, a low skyline, a ground line. Geometry is in the canvas board; port the SVG as-is.

| Element | Animation | Duration | Properties |
|---|---|---|---|
| Building floors | Each floor fades up 10px in turn, holds, fades out together | 22s loop, 1.1s stagger | `opacity`, `transform` |
| Crane jib | Sways ±1.6° about the mast top | 16s alternate | `transform` |
| Hanging load | Bobs −4px to +6px | 7s alternate | `transform` |
| Excavator arm and bucket | Swings −4°/+5° and −8°/+10° | 9s alternate | `transform` |
| Dot grid | Breathes 0.18 to 0.4 | 10s alternate | `opacity` |

Rules, all enforced by `tests/auth.spec.mjs` in the canvas (copy the tests):
- **Only `transform` and `opacity`.** Never width, height, margin, top, left, stroke-dash or path data.
- **Every loop is at least 6 seconds.** Nothing fast, nothing that draws the eye.
- **The un-animated state is the finished picture** (opacity 1, rotation 0). Keyframes use `from` states only, with no `fill-mode: forwards`.
- **Reduced motion:** no looping animation runs (`animation-iteration-count: 1`, near-zero duration), the scene is the static picture.
- The scene never sits behind text: the card is opaque and above it.
- Do not add parallax, pointer tracking or a JS animation library. CSS keyframes only.

## 9. Overrides of MASTER.md (registered)

| Id | Override | Why |
|---|---|---|
| E-2 | The 300ms transition cap (AGENTS 3.4) does not apply to the **backdrop's** ambient loops (7 to 22s). They are not transitions and are bound by §8 instead. | A long, slow, loop-only scene is the point of the surface. |
| E-3 | Inputs and buttons on these routes are 44px, not the dense control height. | Phone use; the screens are entered cold. |
| E-4 | No app shell on these routes. | Pre-authentication. |

## 10. Tests the story must leave behind (E05-S10 plus this page)

- Playwright happy path: cold browser, `/`, redirect to `/sign-in`, sign in, land on `return`; sign out returns to `/sign-in`.
- Uniform failure: wrong password, unknown email and a seeded locked account render byte-identical alerts; email kept, password empty, alert focused.
- MFA: valid code signs in; invalid code shows the field error and keeps the digits; enrolment completes with a valid TOTP; recovery codes gate the Continue button.
- Keyboard-only path through every control with a visible ring.
- axe on `/sign-in`, `/sign-in/mfa`, `/sign-in/mfa/enroll` in light and dark.
- Backdrop: the animation audit in §8 (properties, minimum loop, hidden from assistive tech, reduced motion), with a planted violation proving it fails.
- Phone (375): no horizontal scroll, 44px targets.
