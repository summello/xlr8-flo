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

## 1. Invitation acceptance (and the story it needs first)

**Why a story is needed.** E05-S10 puts "invitation acceptance" out of scope and no roadmap story owns it, so a user can be created today only by the operator bootstrap (E05-S09) or by self-serve signup (E18-S02, which creates a *new* organization). Nothing lets an administrator bring a colleague into an existing tenant.

**Operator decisions, 9 Oct 2026** (DECISIONS 6c, items 41 to 44): a person may belong to several tenants and one organization may exist as several tenants; only administrators invite; resend is required and invitations expire after 7 days; privileged roles must enrol in MFA before first use.

**Two stories, in this order**

**A. Multi-organization membership (prerequisite).** E05-S09 D-M1-23 says one identity belongs to exactly one organization and calls multi-organization membership out of scope. The operator has reversed that. This needs a `docs/claude-plan.md` section 1 entry first (Opus), then a story: change `identity_membership` so the primary key is `(identity_id, org_id)` (reversible migration, data preserved), make the session carry the chosen `org_id`, add `/sign-in/organization` (page file `sign-in.md` section 3b), a switcher in the shell menu, a tenant label on the organization, and TEN-010 isolation tests proving a session for tenant A never reads tenant B. Kind `security/M` or `L`. Not owned by any coding agent until Opus authors the packet.

**B. Invitation acceptance.** Depends on A for the existing-account path; the new-account path does not.

**Paste this to the coding agent that will write the story requests:**

> Write story requests for two XLR8 FLO stories. Do not write code and do not edit `agents/roadmap.yaml`.
>
> Read first: `AGENTS.md`, `agents/project-memory.md`, `docs/m1-conventions.md`, `design-system/MASTER.md`, `design-system/pages/sign-in.md`, `design-system/pages/invitation.md`, `design-system/canvas/DECISIONS.md` (section 6c, items 34 to 44 and the conflicts list), `design/E05-S09.md`, `design/E05-S10.md`, `design/E05-S11.md`, requirements AUTH-001, 003, 004, 005, 006, 007, 008, 011, SEC-006, SEC-008, TEN-010.
>
> **Story A, Multi-organization membership.** Draft packet `design/DRAFT-multi-org-membership.md`: migration of `identity_membership` to `(identity_id, org_id)`, session carries the chosen organization, chooser route, switcher, tenant label, isolation tests, and the exact list of E05-S09 decisions it supersedes. Flag that D-M1-23 must be amended in `docs/claude-plan.md` section 1 by Opus before this is authored; put that in `notes.blocked`.
>
> **Story B, Invitation acceptance.** Draft packet `design/DRAFT-invitation.md` in the format of `design/E05-S10.md`: back end (an `invitation` table with the token hashed, org_id, role, scope, inviter, invitee email, expires_at 7 days from send, used_at, withdrawn_at; issue, validate, accept, withdraw and resend in the identity module; only a user with an administrator role may issue, a non-admin gets 404; resend invalidates the previous link and restarts the 7 days; accept is a state-changing POST with `Idempotency-Key`; the email goes through the outbox; audit entries without the token or password), the screen exactly as `invitation.md` (route `/invite/:token`, every state, copy verbatim), the uniform-failure rule (used, withdrawn, malformed and unknown tokens render one Unavailable response; only expiry differs; reuse the E05-S11 throttle), acceptance criteria a reviewer can verify mechanically, tests matching section 7 of the page file, files in and out of scope. Split the screen into its own `ui/S` story so the cheap author can take it. Out of scope: the administrator's invite screen (list it as a follow-up), SSO, password reset.
>
> In both packets, copy every item under "Still open" in `invitation.md` section 6 into `notes.blocked` unchanged. Do not decide them. State dependencies: A needs an Opus decision; B depends on E05-S09, E05-S10, E05-S11 and, for the existing-account path, on A.

**Still open (do not let an agent guess):** whether a resend restarts the 7 days (assumed yes); that data residency is not a requirement today and separate tenants do not put data in a country; the administrator's invite screen is not drawn.

---

## 2. Sign-up UI (E18-S02 is back end only)

Request a `ui/S` story: `/sign-up` and `/sign-up/verify` per `design-system/pages/sign-up.md`. Dependencies: E18-S02, E05-S10 (shared layout). Open: which anti-abuse control E18-S02 uses (the page leaves a slot and says not to guess), and whether the verification landing page (`/verify?token=`) is in the same story. The page file fixes two things that are easy to get wrong: password minimum **15** (AUTH-004), and the verify screen must read the same for a new and an existing address (AUTH-007).

## 3. Not designed yet, in the order planned (DECISIONS 6c)

Password reset (request, set, expired link; E02-S03 is back end only), recovery-code sign-in, email verified, logout and session expired, 403/404/500 with correlation id, suspended tenant notice, demo banner, empty and error patterns, onboarding tour. Each will get its own page file when drawn.

## 4. Requisitions: E09-S11 should cite the page files

E09-S11 (create and edit, validation summary, list, detail) should carry, under Conventions: `Read design-system/pages/requisition-create.md and design-system/pages/requisitions.md. They are the layout, copy, states, validation messages and tests for these screens.` The operator decision that **a requisition over its funding cannot be submitted** (DECISIONS 33) must appear as an acceptance criterion with the two-concurrent-submissions test in real Postgres. Open questions are listed at the end of each page file; copy them into `notes.blocked` unchanged. The linked-record graph (E09-S09) and return/amend/resubmit (E09-S08) remain gaps with no UI.

---

## 3. Approvals: stories the canvas needs

Written 9 Oct 2026. Page specs: `design-system/pages/approval-inbox.md`, `approval-workflow-builder.md`. The engine stories exist (E10-S01 to S09, E09-S08); several screens have **no UI story**. Requests, in order:

1. **E10-S10 gains scope.** It lists inbox views only. Add: the decision panel with the routing explanation (reads E10-S09), the result state and `Next`, and the stale-version and reason refusals (reads E10-S06, E10-S07). Cite `approval-inbox.md` sections 1 and 2.
2. **Approval delegation UI** (new, after E10-S08): availability screen, eligible-delegate picker, revoke, organization tab for administrators, attribution list. Cite section 5. Needs an operator answer on revoke behaviour and re-delegation first.
3. **Withdraw, return and resubmit UI** (new, after E09-S08 and E10-S06): the withdraw dialog, the returned banner, the resubmit diff with the funding check. Cite sections 3 and 4. Needs the material-change rule decided.
4. **E10-S11 gains scope.** The packet should require the outline (APR-002) in the same story, cycle prevention on connect, the problems tab, publish dialog and Route Preview, and add a list and version-history screen (`approval-workflow-builder.md` sections 1 and 5). Route Preview needs a read endpoint that evaluates a draft against sample attributes and returns the matched rules; that is not in E10-S09 today.

**Operator decisions needed (the canvas shows each as an assumption):** who may withdraw and until when; what is a material change; revoke returns undecided tasks or leaves them; re-delegation; duplicate approvals allowed with intent; conditions beyond the estimate before Phase 2.
