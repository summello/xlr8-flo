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

## 5. Budget Transfer: stories the canvas needs (DECISIONS 6e and 6f)

Page spec: `design-system/pages/budget-transfer.md`. **E07-S07 and E07-S08 are engine stories; there is no UI story.**

1. **A UI story for the transfer pages** (list, create with the Type switch, detail, reversal) citing the page spec, BUD-001 to 008, 011, APR-003, ACC-002. Depends on E07-S07, E07-S08, E10-S06 (decisions) and the ledger sheet.
2. **BUD-003 and requirements 7.3 need a decision-log entry (Opus) before E07-S08 is built.** Decided 9 Oct 2026 (DECISIONS 96): ancestors are **recounted** from the two project entries; the two projects are the only ledger entries. The requirement says "balanced, linked ledger entries for every affected level". Proposed entry for `docs/claude-plan.md` section 1: *"BUD-003 and 7.3 are met by one transaction that (a) locks the two project funding rows in ascending id order, (b) validates availability, (c) inserts the balanced, linked entries on the two projects under one transfer id, and (d) recomputes the roll-up of every ancestor on both paths inside the same transaction, returning it level by level. Ancestor levels are derived, never separately persisted, so they cannot drift (invariant 9)."* Until the entry exists, the story writes `notes.blocked`.
3. **Allocation and adjustment (BUD-001) have no story.** They share the transfer form (decision 67). They need the engine (an allocation from the unallocated pool, an adjustment up or down on one project), the approval workflow coverage (APR-003) and the same ledger linkage.
4. **A reversal story:** a new linked transfer through the same route, refused if the target lacks the funds (decision 76); the original is never touched.
5. **Organization settings:** evidence required (off, Cross Hierarchy only, always) and the policy flag for transfers across levels. Both need a settings story and an audit entry on change.
6. **Answered 9 Oct 2026 (DECISIONS 98 to 103):** reversal is Finance Administrator only, one `BT-` prefix, allocations draw on available budget, allocation and adjustment reuse the transfer workflow, the policy flag is `Allow Cross-Level Transfers`. **New story needed: approval thresholds configurable by tenant, entity and user** (a settings screen, resolution order, an audit entry on change, and the routing explanation naming the scope that matched). Notification copy is drafted in the page spec section 10.

## 6. Bid comparison: stories the canvas needs (DECISIONS 6g)

Page spec: `design-system/pages/rfq-comparison.md`. E12-S10 is the UI story and cites it.

1. **E12-S10 should cite the page spec** and requirements SRC-006 to 011, 014, 016, A11Y-009, RPT-013. Depends on E12-S06, E12-S04, E12-S07, E12-S05.
2. **The comparison engine (E12-S06) must return both award bases** (Lowest Cost Supplier per line, Best Average Cost for one vendor among full bids), landed cost per vendor and per line, the normalized figures with rate, source and effective date, and the markers' inputs. The page computes nothing.
3. **Sealed bids (E12-S05):** no figure may leave the server before the deadline, for any role. A test must prove it.
4. **An award UI story is missing** (E12-S08 is an engine story). `Start Award` hands off to it: split by line, scope and reserved-funds guard (SRC-016), non-lowest justification (SRC-014).
5. **A 5-vendor limit per RFQ** must be enforced on invitation (E12-S02), not only shown.
6. **Compliance requirements** need a home (per RFQ or per template) and a waiver rule; scoring and conflict-of-interest declarations (E12-S07) feed the Scoring tab.
7. **Answered 10 Oct 2026 (DECISIONS 112 to 119):** freight pro rata by line value, rate warning after 7 days with a refresh for any sourcing role, a sealed round hidden from everyone, ties and overrides, compliance requirements per RFQ with an audited waiver. **Open:** how a split award interacts with vendor minimum order values.
8. **New story needed: a tokens story that contrast-measures MASTER 7.13 in dark mode** before any button code is written.

## 7. Vendors: stories the canvas needs (DECISIONS 6h)

Page spec: `design-system/pages/vendors.md`. **E11-S01 to S07 are back-end stories; no vendor UI story exists.** Requests, in order:

1. **Vendor list and profile UI** (new, after E11-S01, S02, S03): list with tabs and filters, profile cards, contacts, documents table and document sheet, `Missing` rows, expiry look-ahead. Cite the spec sections 3.1, 3.2, 3.6 and VEN-001 to 003, 011, OBS-004.
2. **Eligibility as a shared server rule and a shared component** (new, before any RFQ or PO UI): the server returns `eligible` plus ordered reasons for a given unit (VEN-012); the web app has one `Eligibility` component used by the profile, the list, the RFQ invitation picker and the PO vendor field. Never recomputed in the browser.
3. **Tax and banking masking UI** (new, after E11-S04): masked by default, audited `Reveal` for vendor managers and finance, `Restricted` for everyone else, no value in the API response, DOM, exports or logs before an audited reveal. A planted-violation test is part of the story.
4. **Vendor request form and duplicate check UI** (new, after E11-S05, S06): the request form, live duplicate check at the field, `Use Existing Vendor`, manager override with a reason, saved drafts.
5. **Onboarding decision panel** (extends E10-S10): the vendor evidence block, in decision-panel order (DECISIONS 65). Cite spec section 3.4.
6. **Status, scope and local-disable dialogs** (new, after E11-S01, S06): `Suspend`, `Reactivate`, `Change Availability` go for approval; `Disable Here` and `Enable Here` take effect at once. Needs the organization setting `Allow Local Disable`.
7. **Questionnaire administration UI** (new, after E11-S05): versioned list, editor with move buttons, weights total 100, publish dialog, preview. Versions are immutable (CUS-007).
8. **Performance scorecard UI** (new, after E11-S07): scored orders, add score, appended disputes with adjustment. Scoring happens per PO and period by purchasing users in M3; goods receipt in M4 (E14) will automate it.
9. **Answered 13 Oct 2026 (DECISIONS 6h):** status vocabulary, expired required document makes a vendor not eligible, reveal for vendor managers and finance, local disable without approval, below-threshold informs, duplicates block with manager override, Service and Delivery contact receives RFQs and POs, no minimum order value yet, manual scoring in M3. **Open:** required document types and their configuration, reveal timeout, category scoring beyond four, drag reordering.
10. **Needs an operator-approved MASTER amendment:** the `vendor` status map and the `signal` and `eligibility` components (page spec section 9).

## 8. Sourcing: stories the canvas needs (DECISIONS 6i)

Page spec: `design-system/pages/sourcing.md`. Only E12-S10 (comparison) is a UI story today; E12-S08 and S09 are blocked on money rules. Requests, in order:

1. **RFQ list and RFQ detail UI** (new, after E12-S01, S03): list with tabs and counts, detail with lifecycle, versions, invitees and the sealed bids card. Cite spec sections 4.1 and 4.4 and SRC-004, SRC-015.
2. **New RFQ from approved lines UI** (new, after E12-S01): the line picker, the open-quantity guard at the field, lines from several requisitions that share unit and currency, `Start RFQ` on the requisition detail. Cite SRC-001, SRC-003, REQ-014, REQ-015.
3. **RFQ editor, invitations and amend UI** (new, after E12-S02, S03): compliance requirements, attachment visibility, the eligibility picker (cap of 5, override with a reason), publish confirmation, amend with diff, reason and who is told, extend deadline. Needs the vendor **eligibility signal** story from Vendors section 7 first. Cite SRC-002, SRC-004, SRC-005, VEN-012.
4. **Record a bid UI** (new, after E12-S04, S05): the vendor's sent time with evidence, keyed time, lines in the vendor's currency, `Not Quoted`, compliance answers, late bid blocked and accepted only by override with a reason. Cite SRC-006, SRC-008, SRC-010.
5. **Evaluation UI** (new, after E12-S07): conflict declaration first, scores and comments, sealed until the deadline, recusal. Cite SRC-011.
6. **Award draft UI** (new, after E12-S08; **blocked on the money rule**): per-line vendor and quantity, justification for non-lowest lines, scope and reserved-funds check, over scope refused with the figure. Cite SRC-012 to 014, SRC-016. This is the award UI story that section 6 item 4 asked for.
7. **Award decision panel** (extends E10-S10): `Award Evidence` in decision-panel order. Cite spec section 4.8.
8. **Cancel and supersede dialogs** (new, after E12-S09; **blocked**): consequence for invitees, bids, open quantities and budget; history kept. Cite SRC-017, WF-005.
9. **Answered 13 Oct 2026 (DECISIONS 6i):** page renamed Sourcing, status vocabulary, several requisitions per RFQ, the vendor's sent time decides lateness, sourcing lead is `rfq.approve` on the unit, award approval only when a workflow is published, unused reservation released once at approval (assumption), no minimum-order warning, non-lowest justification on by default. **Open:** the reservation rule (E09-S13), cancel budget effect (E12-S09), separation of duty on an override, criteria and weights ownership.
10. **Needs an operator-approved MASTER amendment:** the RFQ and award status maps.

