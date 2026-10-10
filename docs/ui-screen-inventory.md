# XLR8 FLO — UI screen inventory

Status: **draft for operator review** (8 Oct 2026). Derived from `docs/requirements.md` §4, `docs/claude-plan.md` §5 and `apps/web/src/routes/route-paths.ts`. Purpose: the list of screens Claude Design lays out, and the list `design-system/pages/*.md` is written against. It adds no requirement.

**Story column:** the roadmap story that builds the screen. **GAP** = the roadmap has no UI story for it, so a back-end story would ship with no way to use it. Each gap needs a story before its module's milestone is authored.

## 1. Who uses it (proposed roles, to confirm)

| Role | Primary job | Lives in |
|---|---|---|
| Requester | Raise and track requisitions | Requisitions, Approvals (as submitter) |
| Approver | Decide quickly, with context | Approval inbox |
| Project manager | Plan, watch budget, move money | Projects, Budget |
| Buyer / procurement | Run RFQs, award, issue POs | Sourcing, Purchase orders, Vendors |
| Finance / controller | Allocate, transfer, reconcile, close periods | Budget, Reporting, Organization |
| Warehouse / asset custodian | Receive, assign, register | Assets |
| Executive | Read the portfolio at a glance | Dashboards |
| Administrator | Users, roles, master data, workflows, fields | Administration |
| Operator (us) | Tenants, limits, graduation | Operator console |

## 2. Navigation (exists today in `route-paths.ts`)

Home · Organization · Administration · Projects · Budget · Reporting · Requisitions · Approvals · Vendors · Sourcing · Purchase orders · Assets · Help. Shell, breadcrumbs, command palette and keyboard help are built (E04-S02). Every screen below hangs under one of these.

## 3. Screens

### 3.1 Access (outside the shell)

| Screen | Route | Key states | Story |
|---|---|---|---|
| Sign in | `/login` | error, locked, throttled | E05-S10 |
| MFA challenge, enrol, recovery | `/login/mfa` | wrong code, expired | E05-S10 (enrol and recovery **GAP**) |
| Password reset request / set | `/reset` | no-enumeration message, expired token | **GAP** |
| Sign up and email verify | `/signup` | verification pending | **GAP** (E18-S02 is back end only) |
| Suspended tenant notice | — | read-only grace period | **GAP** (E18-S06) |

### 3.2 Home and reporting

| Screen | Key content | Story |
|---|---|---|
| Home (configurable) | my tasks, my projects, spend vs budget | E15-S06 |
| Executive dashboard | portfolio KPIs, drill to BU, project, document, transaction | E15-S06 |
| Operational dashboards | per module | E15-S06 |
| Report builder / viewer, export | filters, as-of time, export context header | **GAP** (E15-S08, S09 back end only) |
| Saved views and shared views | list, publish, archive (`/views/archive` exists) | **GAP** (E15-S07 crud only) |
| Global search results | authorized records only | **GAP** (E17-S01 is API) |
| Notifications and preferences | inbox, digest settings, locale, timezone, theme | **GAP** (E17-S02, S06) |

### 3.3 Organization and administration

| Screen | Story |
|---|---|
| Organization and BU/OU tree, addresses, settings | **GAP** (E05-S01, S02) |
| Master data lists: departments, ledger accounts, UoM, tax codes, payment terms, categories | **GAP** (E05-S03) |
| Fiscal calendar and period close | **GAP** (E05-S04) |
| FX rates | **GAP** (E05-S05) |
| Users, roles, effective-access explorer (`/administration`) | E02-S06 built |
| Custom field definitions and label groups | **GAP** (E16-S01, S07) |
| Tag swatches and tag component | E16-S08, S09 |
| Import wizard: template, mapping, dry-run preview, error report, progress | **GAP** (E08-S01 to S04) |
| Workflow builder (canvas) and list editor | E10-S11, S12 |
| Tenant data export, deletion | **GAP** (E18-S07, S08) |

### 3.4 Projects and budget

| Screen | Story |
|---|---|
| Project list: filter, sort, group, saved views | E06-S07 |
| Project detail: overview, hierarchy, phases, milestones, risks, notes, attachments | E06-S07 (phases, risks, notes **GAP**) |
| Project create and edit | E06-S07 |
| Project dashboard: hierarchy visual, drill to ledger entries | E06-S08 |
| Budget overview: allocated, reserved, committed, actual, available | **GAP** (E07-S06 is API) |
| Allocation form with reason and effective date | **GAP** (E07-S03) |
| Transfer form: same level and cross-hierarchy, with preview of both ancestries | **GAP** (E07-S07, S08) |
| Ledger entry list with source-document lineage | E06-S08 drill-down |
| Reconciliation drift report | **GAP** (E07-S09) |

### 3.5 Requisitions and approvals

| Screen | Story |
|---|---|
| Requisition list | E09-S11 |
| Requisition create and edit: header, item and service lines, catalogue suggestions | E09-S11 |
| Submission validation summary (what failed and where) | E09-S11 |
| Requisition detail: status, history, linked-record graph, notes | E09-S11 (graph **GAP**, E09-S09) |
| Return, amend, resubmit, with diff to prior values | **GAP** (E09-S08) |
| Approval inbox: pending, overdue, completed, delegated | E10-S10 |
| Approval decision panel: approve, reject, return, comment, routing explanation | **GAP** (E10-S06, S09) |
| Delegation and out-of-office | **GAP** (E10-S08) |

### 3.6 Vendors and sourcing

| Screen | Story |
|---|---|
| Vendor list, profile, contacts, documents with expiry | **GAP** (E11-S01 to S03) |
| Vendor onboarding questionnaire and scoring | **GAP** (E11-S05) |
| Vendor performance | **GAP** (E11-S07) |
| RFQ create from requisition lines, invitations, deadline | **GAP** (E12-S01 to S03) |
| Bid entry (buyer side; vendor portal is out of Phase 1, to confirm) | **GAP** (E12-S04) |
| Comparison dashboard with accessible table equivalent | E12-S10 |
| Evaluation scoring and conflict-of-interest declaration | **GAP** (E12-S07) |
| Award: split by line, non-lowest justification | **GAP** (E12-S08) |

### 3.7 Purchase orders

| Screen | Story |
|---|---|
| PO list and detail: lines, tax and freight shown separately, lifecycle | **GAP** (E13-S01 to S03) |
| PO create from awarded lines | **GAP** (E13-S01) |
| Change order with field-level diff | **GAP** (E13-S06) |
| Issue and transmission log, PDF preview | **GAP** (E13-S08, S09) |

### 3.8 Assets and warehouses

| Screen | Story |
|---|---|
| Warehouse list, locations, stock on hand | **GAP** (E14-S01, S04) |
| Stock movements, FIFO assignment | **GAP** (E14-S02, S03) |
| Stock count and adjustment | **GAP** (E14-S07) |
| Fixed-asset register and detail with PO lineage | **GAP** (E14-S05, S06) |

### 3.9 Cross-cutting surfaces

| Surface | Story |
|---|---|
| Contextual field help and tooltips | E17-S07 |
| Role-based onboarding tour | E17-S08 |
| Error with correlation id, in-app feedback | E20-S03 |
| Attachment upload with quarantine states | **GAP** (E09-S10, E13-S11) |
| Operator graduation dashboard | E20-S06 |
| Demo-tenant banner | **GAP** (E18-S09) |

## 4. What this says

- **30 stories carry a `ui` tag; roughly 70 screens are listed above.** About 55 of those screens have no UI story. That is the real finding: as roadmapped, M1 to M3 would deliver a working back end whose budget, vendor, sourcing and PO screens nobody has been told to build.
- **Smallest honest fix:** one UI story per module that lacks one, written as the module is authored, each pointing at its `design-system/pages/<module>.md`. Not 55 stories.
- **Decisions (operator, 8 Oct 2026):**
  1. Vendor portal: **none in v1.** The buyer keys bids in.
  2. Operator console: **proposed, not yet confirmed.** No console for MVP. Provisioning, suspension, export and deletion stay CLI. Only the read-only graduation dashboard (E20-S06) gets a page, behind an operator-only role that sees no tenant data.
  3. Mobile: **responsive first**; phone-specific tuning after MVP.
  4. Role list in §1: **confirmed for now.**

## 5. Next steps

1. You correct §1 and the §4 decisions.
2. Claude Design lays out the key flows, in this order: sign in, project dashboard, requisition create, approval inbox, budget transfer, comparison dashboard.
3. Each approved layout becomes `design-system/pages/<page>.md`; UI stories cite it.
4. Add the missing UI stories to `agents/roadmap.yaml` (needs a human decision per AGENTS.md rule 5).
