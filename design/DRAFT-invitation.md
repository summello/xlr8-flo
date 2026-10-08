# DRAFT — Invitation acceptance (back end): issue, validate, accept, withdraw, resend

**STATUS: story request, not a packet.** No roadmap entry, no id. Opus authors the real packet; the operator adds the roadmap entry.

**Epic** E05 · **M1** (proposed) · **security/M** · after **E05-S09**, **E05-S10**, **E05-S11**; existing-account path after **DRAFT-multi-org-membership** · tags: `auth`, `security`, `identity`
UI is a separate `ui/S` story: DRAFT-invitation-ui.

> **Conventions:** read `docs/m1-conventions.md`. It applies unless this packet says otherwise. The behaviour below is specified by `design-system/pages/invitation.md` (branch `worktree-canvas-project-dashboard`) §1, §2, §5.

## notes.blocked

Copied unchanged from `invitation.md` §6 "Still open". Not decided here:

- **Does a resend restart the 7 days?** This page assumes yes.
- **Multi-organization membership is not built.** E05-S09 D-M1-23 says one identity belongs to exactly one organization. The existing-account path and the chooser depend on a story that changes that (DECISIONS.md, conflicts under 6c). Build the new-account path first; the existing-account path waits for it.
- **Data residency.** Separate tenants do not by themselves keep data in a country. This page shows a tenant label and makes no residency claim.
- The administrator's invite screen (who, role, scope, resend, withdraw) is not drawn.

Also blocked on (from STORY-NOTES §1): D-M1-23 must be amended by Opus in `docs/claude-plan.md` §1 before the existing-account path is authored. Resend-restarts-7-days is implemented as written in the contract below *as an assumption*; confirm before authoring.

## Why this story exists

A user can be created today only by the operator bootstrap (E05-S09) or by self-serve signup (E18-S02, which creates a **new** organization). Nothing lets an administrator bring a colleague into an existing tenant. E05-S10 lists invitation acceptance as out of scope and no roadmap story owns it.

## Requirements

- **AUTH-001** — User profiles link users to BU/OUs, roles, projects, locale, timezone, and notification preferences.
- **AUTH-003** — Passwords follow current NIST guidance: long passphrases and paste allowed, common/compromised values blocked, no composition rules.
- **AUTH-004** — Password at least 15 characters when it is the only factor.
- **AUTH-005** — Salted, adaptively costly hashing (reuse `LocalIdentityProvider`, argon2id).
- **AUTH-006** — MFA mandatory for privileged users.
- **AUTH-007** — Authentication errors must not reveal whether an account exists.
- **AUTH-008** — Tokens single-use, short-lived, securely generated, invalidated after use.
- **AUTH-011** — Account activation and recovery are auditable.
- **SEC-006** — Allow-list validation at trust boundaries; parameterized queries.
- **SEC-008** — Authorization prevents cross-organization and object-identifier access.
- **TEN-010** — `org_id` from the session (here: from the invitation, then the new session); foreign tenant 404.

## Design decisions (operator, 9 Oct 2026, DECISIONS 41 to 44) and to confirm

- Decided: only administrators invite; resend required; 7-day expiry; privileged roles enrol in MFA before first use.
- **D-draft-5 Token.** 32 random bytes from `secrets`, URL-safe base64. Stored as SHA-256 only (`token_hash bytea UNIQUE`); looked up by hash with constant-time compare; plaintext appears only in the email body and is never logged, audited or returned by any API.
- **D-draft-6 Lifetime is a setting.** `invitation_ttl_days` (default 7, validated range 1 to 30) in `kernel/config.py`; the UI copy and the email read the same value.
- **D-draft-7 Resend** issues a new token, sets `token_hash` to the new hash (the old link then matches nothing and renders Unavailable), and sets `expires_at = now + ttl` (assumption, see notes.blocked). One row per invitation; no second row.
- **D-draft-8 Existing-account path** accepts only when the signed-in identity's email equals the invited email (case-folded); otherwise the response is the uniform **Unavailable** answer. It adds an `identity_membership` row (requires DRAFT-multi-org-membership) and never moves or merges data.
- **D-draft-9 Roles on accept.** The invitation's `role` and scope are granted through the existing identity service `grant_role(...)` inside `tenant_transaction(Scope(invitation.org_id))`. Granting a role in `MFA_REQUIRED_ROLE_CODES` increments `identity.privileged_role_grants` and forces MFA enrolment (E05-S09 contract), which is how decision 44 is met; this story builds no new MFA logic.

## Contract

**Migration** (`20261008_NNNN`, reversible, data preserved). `invitation(id uuid pk, org_id uuid not null references organization, email citext not null, full_name text null, role_code text not null, scope_type text not null, scope_id uuid null, invited_by uuid not null references identity, token_hash bytea not null unique, created_at timestamptz not null default now(), sent_at timestamptz not null, expires_at timestamptz not null, used_at timestamptz null, used_by uuid null references identity, withdrawn_at timestamptz null, withdrawn_by uuid null)`. RLS by `org_id` for the administrator surface. The **public** token lookup runs before any tenant is known, exactly like login: it reads by `token_hash` through a narrow SECURITY DEFINER lookup (or the same documented global-table treatment as `login_attempt`, D-M1-30), returns only what the screen needs, and the table is added to the isolation gate's allow-list or helper list with a test naming the reason. State which in the real packet. Partial unique index: at most one **open** invitation (unused, unwithdrawn) per `(org_id, email)`; issuing a second for the same pair while one is open returns 409 `CONFLICT` label `invitation_open`.

**Routes** (module `identity`, service functions; routers hold no logic):

| Route | Auth | Behaviour |
|---|---|---|
| `POST /api/v1/invitations` (Idempotency-Key) | administrator, org scope | issue; body `{email, full_name?, role_code, scope_type, scope_id?}`; org from the session; 201 with invitation id (never the token); email via outbox |
| `GET /api/v1/invitations` | administrator | list for the session's org (for the follow-up admin screen) |
| `POST /api/v1/invitations/{id}/resend` (Idempotency-Key) | administrator | D-draft-7; email via outbox |
| `POST /api/v1/invitations/{id}/withdraw` (Idempotency-Key) | administrator | sets `withdrawn_at`; idempotent |
| `GET /api/v1/invitations/by-token` | public | token in a request header (`X-Invitation-Token`), **not** a path or query, so it stays out of access logs; returns `{state: "valid", org_name, tenant_label, inviter_name, inviter_title, role_name, access_words, expires_at, email, full_name}` or `{state: "expired"}`, or the uniform 404 problem for used, withdrawn, malformed and unknown |
| `POST /api/v1/invitations/accept` (Idempotency-Key) | public, or session for existing account | token in header; new account body `{full_name, password, accepted_terms}`; existing account body empty; see below |

**Authorization.** Only a user holding an administrator permission may issue, list, resend, withdraw. Find the existing permission (do not invent); others, and any id from another tenant, get **404**, never 403 (STORY-NOTES; AGENTS §3.2). Role and scope in the body are validated against roles and scopes **of the session's organization**; unknown ones are 422 `VALIDATION_FAILED`.

**Uniform failure (AUTH-007).** Used, withdrawn, malformed and unknown tokens produce byte-identical responses (status, body, headers apart from `correlation_id`) on both `by-token` and `accept`; the body reveals nothing about the organization. Expired is the only distinct case. Both public routes go through the E05-S11 throttle (same keys, same uniform treatment, same 150 to 300 ms jitter on a throttled attempt, `Retry-After` only for the client-prefix limit). `Referrer-Policy: no-referrer` and `Cache-Control: no-store` on both.

**Accept, new account.** One transaction: re-validate by hash `FOR UPDATE` on the invitation row **before** checking `used_at`, `withdrawn_at`, `expires_at`; apply the password policy through `LocalIdentityProvider` (15-char minimum, common and breached refused; weak password is 422 `VALIDATION_FAILED` with the error on `password`, and **nothing is created**, the invitation stays open); create the identity with email **from the invitation, never the body**; insert the membership; `grant_role` per D-draft-9; set `used_at`, `used_by`; audit; outbox welcome email. Two concurrent accepts of one token: exactly one succeeds, the other renders Unavailable. If an identity with that email already exists, respond Unavailable on the unauthenticated path (no enumeration) and require the existing-account path.

**Accept, existing account** (after DRAFT-multi-org-membership): requires a session (MFA satisfied); checks the signed-in email equals the invited email else uniform Unavailable; adds membership, grants role, marks used, audits. Never alters other memberships.

**Email.** Through the outbox only. Body states the tenant (name, label), inviter, role, expiry date, and the link `…/invite/<token>`. No password, no role-secret. A resend sends a new email.

**Audit (AUTH-011).** Events `invitation.issued`, `invitation.resent`, `invitation.withdrawn`, `invitation.accepted`. Each carries actor, invitation id, org id, role, scope, inviter, time, client prefix; **never** the token, its hash, the password or the full email in a log line (the audit row may hold the email, logs may not).

## Acceptance criteria

- [ ] Admin issues, invitee accepts with a valid password: identity, membership, role grant, `used_at`, audit row and outbox row exist; a privileged role leaves MFA enrolment pending
- [ ] A non-administrator, and an administrator of another tenant, get **404** on issue, list, resend and withdraw (isolation cases added to `tests/isolation/` and `COVERED`)
- [ ] The organization, role and scope on accept come from the invitation row: a body, query or header carrying other values changes nothing (test with a forged `org_id`, `role_code`, `email`)
- [ ] Used, withdrawn, malformed and unknown tokens: byte-identical responses on both public routes; no organization text; expired is distinct and contains no organization text either
- [ ] An invitation unaccepted after 7 days (clock injected) is expired; unaccepted at 6 days 23 h it is valid; the TTL setting changes both
- [ ] Resend: old token then Unavailable immediately; new token valid; `expires_at` restarts (the assumption, one test that is trivially changed if the operator decides otherwise)
- [ ] Replayed `Idempotency-Key` on accept returns the first result and creates one identity; two concurrent accepts of one token: exactly one wins (real Postgres, two connections)
- [ ] Weak password: 422 on `password`, no identity, invitation still open, input otherwise unaffected
- [ ] Existing-account path: signed-in address differing from the invited one renders Unavailable; matching one adds a second membership and leaves the first untouched (needs DRAFT-multi-org-membership)
- [ ] Throttle: repeated bad tokens from one prefix throttle with the uniform response
- [ ] Token never appears in a log line, audit row, problem body, or `Referer`; token only in the email body and the request header (test captures logs and audit)
- [ ] Email is sent only via outbox (no direct send in a request handler)
- [ ] Migration up, down, data-preservation
- [ ] Plant: make a used-token response differ from an unknown-token one and the byte-identical test fails; read `used_at` before the `FOR UPDATE` and the concurrent-accept test fails; take `email` from the body and the forged-email test fails

## Files

`migrations/<head-date>_<next>_invitation.py`, `apps/api/tests/kernel/test_migrate.py`, `modules/identity/service.py` and a new `modules/identity/invitation.py` (service logic), `modules/identity/schemas.py`, `api/invitations.py` (router, included where routers are registered), `kernel/config.py` (`invitation_ttl_days`), the email template in the existing email/outbox module, the job-runner purge only if old rows are purged (not required), tests under `apps/api/tests/identity/` and `tests/isolation/`, regenerated OpenAPI client.

## Out of scope

The administrator's invite screen (list as `notes.followup`) · SSO · password reset · inviting to several tenants at once · bulk invite · changing a role after acceptance · any UI (see DRAFT-invitation-ui).

## Dependencies

E05-S09 (membership, bootstrap, `grant_role` wiring), E05-S10 (sign-in and the MFA enrolment screen the accepted state hands to), E05-S11 (throttle). Existing-account path: **DRAFT-multi-org-membership**, which itself needs an Opus decision.
