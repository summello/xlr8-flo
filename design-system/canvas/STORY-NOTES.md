# Story notes from the design canvas

Written 9 Oct 2026. These are **requests for stories**, not stories. Per AGENTS.md rule 5 and §2.1, a story needs a roadmap entry (`agents/roadmap.yaml`) decided by the operator, then a design packet at `design/<STORY_ID>.md` written by Opus. Nothing here edits the roadmap.

Page specs written for the coding agents (read `design-system/MASTER.md` first, then the page file): `design-system/pages/sign-in.md`, `sign-up.md`, `invitation.md`. Canvas boards are the layout reference.

---

## 0. Change to an existing packet: E05-S10

E05-S10 should cite the page file and gain two lines (the packet lives on `milestone/M1-budget-spine`, so edit it there):

- Under Conventions: `Read design-system/pages/sign-in.md. It is the layout, copy, states and motion for /sign-in, /sign-in/mfa and /sign-in/mfa/enroll.`
- Under the failure-state list: `A throttled attempt (E05-S11) renders the same alert as a wrong password. There is no distinct "too many attempts" state.` The canvas first drew one and removed it after re-reading E05-S11 D-M1-29.

Also add the recovery-codes step to the enrolment route (the packet says "QR/secret plus recovery codes"; the page file specifies the screen and that Continue stays disabled until the codes are acknowledged).

---

## 1. Invitation acceptance (the one you asked for)

**Why a story is needed.** E05-S10 puts "invitation acceptance" out of scope and no roadmap story owns it, so a user can be created today only by the operator bootstrap (E05-S09) or by self-serve signup (E18-S02, which creates a *new* organization). Nothing lets an administrator bring a colleague into an existing organization.

**Paste this to the coding agent that will write the story:**

> Write a story request for **Invitation acceptance** for XLR8 FLO. Do not write code and do not edit `agents/roadmap.yaml`.
>
> Read first: `AGENTS.md`, `agents/project-memory.md`, `docs/m1-conventions.md`, `design-system/MASTER.md`, `design-system/pages/sign-in.md`, `design-system/pages/invitation.md`, `design-system/canvas/DECISIONS.md` (section 6c), `design/E05-S09.md`, `design/E05-S10.md`, `design/E05-S11.md`, and the requirements AUTH-001, 003, 004, 005, 006, 007, 008, 011, SEC-006, SEC-008, TEN-010.
>
> Deliver a draft design packet in the same format as `design/E05-S10.md`, saved as `design/DRAFT-invitation.md` (the operator assigns the id), covering:
> 1. **Back end.** An `invitation` table (token stored hashed, org_id, role, scope, inviter, invitee email, expires_at default 7 days, used_at, withdrawn_at); issue, validate, accept, withdraw and resend operations in the identity module; accept is a state-changing POST with `Idempotency-Key`; the confirmation email goes through the outbox. Single-use, short-lived, constant-time lookup, invalidated on accept. Audit entries for invited, accepted, withdrawn, expired, never containing the token or password.
> 2. **Tenancy.** Organization, role and scope come from the invitation record, never from the URL, body or header. A new membership row, then a session with `org_id` on it (E05-S09).
> 3. **The screen** exactly as `design-system/pages/invitation.md`, route `/invite/:token`, every state in its table, copy verbatim.
> 4. **Uniform failure.** Used, withdrawn, malformed and unknown tokens return one response and render `Invitation Unavailable`; only expiry is distinct. Reuse E05-S11's throttle on the token endpoint.
> 5. **Acceptance criteria** as checkboxes a reviewer can verify mechanically, and a test list matching section 7 of the page file (including byte-identical unavailable responses and a replayed Idempotency-Key).
> 6. **Files in scope and out of scope.** Out of scope: the administrator's invite screen and user administration (list as a follow-up story), SSO, password reset.
> 7. **Open questions.** Copy the four open questions from section 6 of `invitation.md` into `notes.blocked` unchanged. Do not decide them.
>
> Size and kind: propose `security/M` for the back end and a separate `ui/S` story for the screen so the cheap author can take the screen. Name the dependencies (E05-S09 for membership, E05-S11 for the throttle, E05-S10 for the shared access layout and the MFA enrolment route it hands off to).

**Operator decisions needed before the packet can be finished** (the same four open questions):
1. Can one person belong to two organizations? (If yes, `Sign in to accept` adds a membership.)
2. Who may invite, and from which screen? (Needs a user-administration story.)
3. Resend and withdraw by the administrator.
4. Are privileged roles forced through MFA enrolment before first use? (AUTH-006 says mandatory for privileged users; the page assumes yes.)

---

## 2. Sign-up UI (E18-S02 is back end only)

Request a `ui/S` story: `/sign-up` and `/sign-up/verify` per `design-system/pages/sign-up.md`. Dependencies: E18-S02, E05-S10 (shared layout). Open: which anti-abuse control E18-S02 uses (the page leaves a slot and says not to guess), and whether the verification landing page (`/verify?token=`) is in the same story. The page file fixes two things that are easy to get wrong: password minimum **15** (AUTH-004), and the verify screen must read the same for a new and an existing address (AUTH-007).

## 3. Not designed yet, in the order planned (DECISIONS 6c)

Password reset (request, set, expired link; E02-S03 is back end only), recovery-code sign-in, email verified, logout and session expired, 403/404/500 with correlation id, suspended tenant notice, demo banner, empty and error patterns, onboarding tour. Each will get its own page file when drawn.

## 4. Requisition create (decided, not started)

Screens and the block-on-short-funds rule are in DECISIONS 6b. No story request yet; it follows the access screens.
