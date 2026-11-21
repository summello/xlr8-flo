# ACC-008 walkthrough: project to issued PO, keyboard only

Every route and control label below is checkable by grep in `apps/web/src`.

ACC-008: a complete project-to-PO workflow is operable without a mouse (M3 exit criterion). This is the
manual evidence for this release. The tester follows it on the local stack and attaches the result record
(section 4) to PR #22. Staging has no second deployed environment, so the local stack is where it runs.

## 1. Setup (not part of the evidence)

Run from the repository root unless a line says otherwise.

1. `agents/scripts/flo up` (Postgres 17, SeaweedFS, Mailpit).
2. `cp .env.example .env`. In `.env`, replace `MFA_ENCRYPTION_KEY` with a generated key (the command is in
   `.env.example`) and add `ORIGIN_SHARED_SECRET=` followed by at least 32 characters. Export the `.env` values in
   the shell you use below (`set -a; . ./.env; set +a`). The seed refuses to run unless `DATABASE_URL` is on
   localhost, `EMAIL_PROVIDER=smtp` and `STORAGE_PROVIDER=local`.
3. From `apps/api`: `export MIGRATIONS_DIR="$PWD/../../migrations"`, then `uv run python -m flo.kernel.migrate`
   (without `MIGRATIONS_DIR` the runner looks in `/app/migrations`).
4. Seed the walkthrough tenant: `FLO_WALKTHROUGH_PASSWORD=<a passphrase of your choice> uv run python -m
   flo.api.walkthrough_seed`. It prints the five emails (the Requester, Approver, Buyer, Sourcing approver and
   PO approver), never the password. All five share that password. A second run exits 3 ("already seeded"); use
   `--org-code` or reset the database.
5. The API, from `apps/api`: `uv run uvicorn flo.api.health:app --port 8000`.
6. The web, from `apps/web`: `npm run walkthrough`. It serves one origin and forwards `/api` to the API with the
   origin secret. Open the URL it prints. Mailpit is at `http://localhost:8025`.

**The jobs tick by hand.** The RFQ close and the PO email wait for the job tick. Run it where the steps below say
so, with a fresh pair of values each time:

```sh
CSRF="$(uuidgen)"
curl -sS -X POST http://127.0.0.1:8000/internal/jobs/tick \
  -H "X-FLO-Origin-Secret: $ORIGIN_SHARED_SECRET" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Cookie: flo_csrf=$CSRF" \
  -H "X-CSRF-Token: $CSRF"
```

It answers with HTTP 2xx. Where a person signs in, the step names which of the five (sign out, then sign in as
the next).

## 2. Rules for the tester

- Pointer off. Use only Tab, Shift+Tab, Enter, Space, Escape, the arrow keys and typing.
- A step passes only if focus is visible at every stop and nothing needs a pointer.
- The operating system's file dialog (bid evidence) is driven by keyboard too. On macOS, press Cmd+Shift+G and
  type the path.
- Where a screen has drag-and-drop, use its keyboard equivalent (the Move Up and Move Down buttons) and say so
  in the note.
- Record the browser and the operating system.

## 3. The chain, keyboard only

Every value the tester enters is fixed in this table, per screen, in walkthrough order. A field not listed is
left as the screen shows it.

| screen | screen (person) | field | value | rule that makes it required |
|---|---|---|---|---|
| 1 | New project (Requester) | Business unit | "Operations" (choose from the suggestions) | `ProjectForm.tsx` ~16 |
| 1 | | Project name | "Pump Station Refit" | ~17 |
| 1 | | Department | `OPS` | ~18 |
| 1 | | Ledger account | `6100` | ~19 |
| 1 | | Currency | `USD` | ~20 |
| 1 | | Budget | `50000.00` | submit refuses without, `projects/service.py` ~243-249 |
| 1 | | Sponsors | `requester@example.com` (the Requester) | same |
| 2 | New requisition (Requester) | Raise a request for | "An existing project" (the default) | `RequisitionForm.tsx` ~199 |
| 2 | | Title | "Pump spare kits" | `model.ts` ~88 |
| 2 | | Project | "Pump Station Refit" | ~89 |
| 2 | | Department | `OPS` "Operations" | ~92 |
| 2 | | Ledger account | `6100` "Maintenance spares" | ~93 |
| 2 | | Needed by | today's date plus 30 days | ~94-95 (not in the past) |
| 2 | | Delivery location | "1 Harbor Road, Springfield US" | ~96 |
| 2 | | Justification | "Spare kits for the pump station refit" | ~97 |
| 2 | | Line 1: Type | "Item" (the default) | `requisitions/schemas.py` ~197-206 |
| 2 | | Line 1: Description | "Pump spare kit" | `model.ts` ~100-103 |
| 2 | | Line 1: Quantity | `2` | same |
| 2 | | Line 1: Unit | "Each" (`EA`) | `requisitions/schemas.py` ~197-206 |
| 2 | | Line 1: Unit price | `1000.00` (estimate 2,000.00 USD, within the budget) | `model.ts` ~100-103 |
| 3 | New RFQ, reached by Start RFQ on the requisition (Buyer) | Title | "Pump spare kits RFQ" | `rfqs/new.tsx` ~137 |
| 3 | | Lines | the requisition's line, preselected with quantity 2 (leave it) | ~89-96, ~148 |
| 4 | RFQ editor (Buyer) | Specifications | "Two pump spare kits per the attached datasheet" | `sourcing/lifecycle.py` ~41-46 |
| 4 | | Delivery Location | "Operations warehouse" | same |
| 4 | | Delivery Terms | "DAP" | same |
| 4 | | Terms | "Net 30" | same |
| 4 | | Response Deadline Date and Time (24-hour) | now plus 65 minutes, rounded up to the minute, **in the zone the Time Zone field shows** (`UTC` unless set, `sourcing/schemas.py` ~193; past midnight there, the next date) | ~148-158 (at least one hour ahead) |
| 4 | | Invited vendor | "Acme Pumps", contact "Acme Orders" `vendor@example.com` | ~160-171 |
| 4 | | Criteria, evaluators, requirements | none (leave empty; no criteria means no weight check) | ~172-179 |
| 5 | Record Bid on the RFQ (Buyer) | Version Answered | the default (Version 1) | `BidSheet.tsx` ~34 |
| 5 | | Vendor's Currency | `USD` (the default) | ~63 |
| 5 | | Vendor Sent It On (date) / Time (24-hour) | the current date and time in the zone the label names, before the deadline | ~61-62 (`sentInstant`) |
| 5 | | Evidence | `docs/ops/acc-008-bid-evidence.txt` | ~75 |
| 5 | | Line 1: Not Quoted | unchecked (the default) | ~43 |
| 5 | | Line 1: Unit price | `950.00` | ~67 |
| 5 | | Line 1: Tax | `0` | ~68 |
| 5 | | Freight, Lead Time, Valid Until and the rest | blank | optional, ~73-76 |
| 6 | Comparison (Buyer) | Award Basis | **Best Average Cost** (one vendor takes the whole RFQ), then Start Award | `comparison/api.ts` ~8-13, `BasisCards.tsx` ~10-19 |
| 7 | Award (Buyer) | Award Rationale | "Only compliant bid, within budget" | `sourcing/awards.py` ~777-778 |
| 7 | | Lines | as carried from the comparison (1 line, Acme Pumps, quantity 2) | ~779-782 |
| 8 | New purchase orders from the award (Buyer) | Quantity to Order, line 1 | `2` (the default, the open quantity) | `purchase-orders/new.tsx` ~49 |
| 8 | | Override Reason | blank (not offered unless an override is needed) | `purchasing/schemas.py` ~47 |
| 9 | Close Purchase Order dialog (PO approver) | Reason (required) | "Walkthrough complete" | `ClosePo.tsx` ~46, `purchasing/schemas.py` ~92 |


Submit, approve and issue take no entered value: the approver presses Approve with no comment (a comment is
required only to reject, return or comment), and Issue uses the seeded recipient.

Steps (one hand-off each; the person signed in, the start URL, the keys pressed, what is focused on arrival,
the expected result):

1. **Project (Requester).** Start at `/projects/new`. Tab through the screen 1 fields, typing the values above,
   then activate **Create project**. Expect the project detail page with focus on its heading. Tab to
   **Change status**, choose Pending approval, activate **Confirm transition**. Expect the status to read
   pending approval.
2. **Approve the project (Approver).** Sign out, sign in as the Approver. Start at `/approvals`. Tab to the
   project's row and press Enter; focus lands in the decision panel. Activate **Approve**. Expect the project to be
   active (a requisition can use only an active project).
3. **Requisition (Requester).** Sign in as the Requester. Start at `/requisitions/new`. Fill screen 2, adding
   line 1; activate **Save draft**, then **Submit for approval**. Expect the requisition to read pending approval
   with its reservation shown.
4. **Approve the requisition (Approver).** As step 2, from `/approvals`.
5. **RFQ (Buyer).** Sign in as the Buyer. Open the approved requisition and Tab to **Start RFQ**, press Enter
   (this lands on `/sourcing/rfqs/new` with the line preselected). Fill screen 3 and activate **Create RFQ Draft**; the app lands on the RFQ detail page.
   Tab to **Edit** and press Enter (the editor at `/sourcing/rfqs/<id>/edit`). Fill screen 4: Search Vendors, select Acme Pumps and activate **Invite Selected**, then **Save
   Draft** and **Publish RFQ**; confirm in the dialog with **Publish RFQ**. Expect status published.
6. **Bid (Buyer).** On the RFQ detail, Tab to **Record Bid** and press Enter. Fill screen 5 (the evidence file
   through the keyboard-driven file dialog), then activate **Save Draft** and **Submit Bid**. Bids are keyed while
   the RFQ is published, so this happens during the wait.
7. **Close (job).** Wait until the response deadline has passed, then run the tick (section 1). Reload the RFQ:
   its status reads closed.
8. **Start Evaluation (Buyer).** On the RFQ detail Tab to **Start Evaluation** and press Enter. The app lands on
   the Evaluation screen, which for the Buyer (not an evaluator) reads "You are not an evaluator on this RFQ".
   Activate **Back to the RFQ**.
9. **Comparison and award (Buyer).** Activate **Open Comparison**. Choose **Best Average Cost** under Award Basis
   and activate **Start Award**. Fill screen 7 and activate **Submit for Approval**.
10. **Approve the award (Sourcing approver).** Sign in as the Sourcing approver; from `/approvals` approve it, as
    step 2.
11. **Purchase order (Buyer).** Sign in as the Buyer. Open the RFQ detail, Tab to **View Award** and press Enter
    (the approved award at `/sourcing/rfqs/<id>/award`). Tab to **Create PO** and press Enter; focus arrives on
    `/purchase-orders/new?award=<id>` with the awarded lines loaded. Keep the quantity (screen 8) and activate
    **Create Draft POs**. On the PO, activate **Submit for Approval**.
12. **Approve the PO (PO approver).** Sign in as the PO approver; from `/approvals` approve it. Open the PO,
    activate **Issue**, then **Issue** in the dialog. Run the tick.
13. **Transmission log.** On the PO detail the transmission card shows **Sent to Vendor**. Open Mailpit and find
    the email to `vendor@example.com`.
14. **Close (PO approver).** On the PO detail activate **Close Purchase Order**, enter the screen 9 reason and
    confirm with **Close Order**. Expect the PO to read closed.

## 4. Result record

Attach this table as a comment on PR #22. A failing step is filed as a story before M3 merges.

Date: ____ Tester: ____ Commit SHA: ____ Browser and OS: ____

| step | pass / fail | note |
|---|---|---|
| 1 to 14 | | one row per step |

## 5. Follow-up

The automated full-stack spec (Postgres, migrated and seeded tenant, the API and the built SPA, one `e2e-live`
Playwright spec with no `page.route` and no `.click()`) is built before the first paying customer. The seed and the
harness above are its starting point.
