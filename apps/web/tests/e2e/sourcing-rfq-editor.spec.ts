import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const rfqId = "11111111-1111-4111-8111-111111111111";
const unitId = "55555555-5555-4555-8555-555555555555";
const reqId = "33333333-3333-4333-8333-333333333333";
const NOW = "2026-10-01T00:00:00Z";
const DEADLINE = "2030-01-01T10:00:00Z";
const vid = (n: number) => `44444444-0000-4000-8000-00000000000${n}`;

const VENDORS = [
  { n: 1, name: "Alpha Pumps", ok: true }, { n: 2, name: "Beta Valves", ok: true }, { n: 3, name: "Gamma Seals", ok: true }, { n: 4, name: "Delta Ltd", ok: false },
  { n: 5, name: "Epsilon Co", ok: true }, { n: 6, name: "Zeta Works", ok: true }, { n: 7, name: "Eta Parts", ok: true },
];
const eligibility = (ok: boolean) => ok ? { eligible: true, reasons: [], warnings: [] } : { eligible: false, reasons: [{ code: "insurance", text: "No valid insurance certificate", document_type: "insurance" }], warnings: [] };

type Options = { canApprove?: boolean; status?: "draft" | "published"; sourced?: boolean; blank?: boolean };

/** An in-memory stand-in for the RFQ, candidates, invitee, publish and amend endpoints. */
async function stub(page: Page, { canApprove = true, status = "draft", sourced = false, blank = true }: Options = {}) {
  const rfq = {
    status: status as string, version: status === "draft" ? 0 : 1, edit_revision: 1, deadline_at: DEADLINE,
    specifications: blank ? "" : "Pump spec", delivery_location: blank ? "" : "Dock 4", delivery_terms: blank ? "" : "DAP", terms: blank ? "" : "Net 30",
    requirements: [] as { id: string; position: number; text: string; waived: boolean; waived_reason: string | null }[],
    invitees: (status === "published" ? [1].map(n => invitee(n, false, null)) : []) as ReturnType<typeof invitee>[],
    lineSpec: null as string | null, quantity: "4.0000",
  };
  const state = { rfq, patches: [] as { body: Record<string, unknown>; ifMatch: string | undefined }[], amends: [] as Record<string, unknown>[], invites: [] as Record<string, unknown>[], published: 0, candidateUrls: [] as URL[], vendorListCalls: 0, versions: [] as unknown[] };
  function invitee(n: number, overridden: boolean, reason: string | null) {
    const v = VENDORS[n - 1]!;
    return {
      vendor_id: vid(n), contact_ids: [`c${n}`], contacts: [{ id: `c${n}`, name: `Contact ${n}`, email: `c${n}@vendor.test`, type: "service_delivery", receives_documents: true, chosen: true }, { id: `d${n}`, name: `Second ${n}`, email: `d${n}@vendor.test`, type: "billing", receives_documents: true, chosen: false }],
      vendor_snapshot: { trading_name: v.name }, override_reason: overridden ? reason : null, invited_by_email: "lead@example.test", invited_by_name: null, invited_at: NOW,
      eligible: v.ok || overridden ? v.ok : false, reasons: v.ok ? [] : ["No valid insurance certificate"], deliveries: [], latest_sent_version: null, not_told_of: [],
    };
  }
  const read = () => ({
    id: rfqId, number: "RFQ-0001", title: "Centrifugal pumps", status: rfq.status, unit_id: unitId, currency: "USD", owner_email: "lead@example.test", owner_name: null,
    version: rfq.version, edit_revision: rfq.edit_revision, created_at: NOW, updated_at: NOW, deadline_at: rfq.deadline_at, deadline_tz: "UTC", published_at: rfq.status === "draft" ? null : NOW,
    specifications: rfq.specifications, delivery_location: rfq.delivery_location, delivery_terms: rfq.delivery_terms, terms: rfq.terms,
    lines: [{ id: "a1", line_no: 1, requisition_id: reqId, requisition_number: "REQ-0042", requisition_line_id: "b1", requisition_line_no: 1, requisition_version: 3, quantity: rfq.quantity, description: "Pump, 3 inch", uom_code: "EA", needed_by: null, currency: "USD", open_qty: "0.0000", specification: rfq.lineSpec }],
    requirements: rfq.requirements.map(r => ({ ...r, waived_by_email: null, waived_by_name: null })), attachments: [], invitees: rfq.invitees, can_edit: true, can_approve: canApprove, bids_received: 0,
  });
  const bump = () => { rfq.edit_revision += 1; };
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    const send = (body: unknown, code = 200) => route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/me") return send({ identity_id: unitId });
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (path.startsWith("/api/v1/vendors")) { state.vendorListCalls += 1; return send({ detail: "No." }, 403); }
    if (path === "/api/v1/master/rfq_requirement_template") return send({ rows: [{ id: "t1", code: "STD", name: "Standard", active: true }], next_cursor: null });
    const base = `/api/v1/sourcing/rfqs/${rfqId}`;
    if (path === base && method === "GET") return send(read());
    if (path === `${base}/versions`) return send(Array.from({ length: rfq.version }, (_, i) => ({ version: i + 1, reason: i ? String(state.amends[0]?.reason ?? "") : null, diff: i ? { terms: { was: "Net 30", now: "Net 45" } } : {}, snapshot: {}, published_at: NOW, published_by_email: "lead@example.test", published_by_name: null })));
    if (path === `${base}/history`) return send([{ action: "rfq.publish", at: NOW, actor_email: "lead@example.test", actor_name: null }]);
    if (path === `${base}/bids`) return send([]);
    if (path === base && method === "PATCH") {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.patches.push({ body, ifMatch: req.headers()["if-match"] });
      for (const k of ["specifications", "delivery_location", "delivery_terms", "terms"] as const) if (k in body) rfq[k] = (body[k] as string | null) ?? "";
      if (body.line_specifications) rfq.lineSpec = Object.values(body.line_specifications as Record<string, string>)[0] ?? null;
      bump(); return send(read());
    }
    if (path === `${base}/requirements/from-template`) { rfq.requirements = [{ id: "r1", position: 1, text: "Insurance certificate", waived: false, waived_reason: null }, { id: "r2", position: 2, text: "Safety record", waived: false, waived_reason: null }]; bump(); return send(read()); }
    if (path === `${base}/invitee-candidates`) {
      state.candidateUrls.push(url);
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      return send({ rows: VENDORS.filter(v => v.name.toLowerCase().includes(q)).map(v => ({ vendor_id: vid(v.n), trading_name: v.name, eligibility: eligibility(v.ok), invited: rfq.invitees.some(i => i.vendor_id === vid(v.n)) })), next_cursor: null });
    }
    if (path === `${base}/invitees` && method === "POST") {
      const body = req.postDataJSON() as { vendor_id: string; override_reason?: string };
      state.invites.push(body);
      const n = VENDORS.findIndex(v => vid(v.n) === body.vendor_id) + 1;
      if (rfq.invitees.length >= 5) return send({ title: "Conflict", status: 409, detail: `Up to 5 vendors per RFQ. Remove one to invite ${VENDORS[n - 1]!.name}.`, checks: { problem: "invitee_limit" } }, 409);
      if (!VENDORS[n - 1]!.ok && !(body.override_reason && canApprove)) return send({ title: "Conflict", status: 409, detail: `${VENDORS[n - 1]!.name} is not eligible: No valid insurance certificate`, checks: { problem: "vendor_not_eligible" } }, 409);
      rfq.invitees.push(invitee(n, !VENDORS[n - 1]!.ok, body.override_reason ?? null)); bump(); return send(read(), 201);
    }
    if (path.startsWith(`${base}/invitees/`) && method === "PATCH") { bump(); return send(read()); }
    if (path === `${base}/publish`) {
      state.published += 1;
      const errors = ([["specifications", "Add the specifications."], ["delivery_location", "Add the delivery location."], ["delivery_terms", "Add the delivery terms."], ["terms", "Add the terms."]] as const).filter(([k]) => !rfq[k].trim()).map(([field, message]) => ({ field, message }));
      if (rfq.invitees.length === 0) errors.push({ field: "invitees", message: "Invite at least one vendor." } as never);
      if (errors.length) return send({ title: "Validation failed", status: 422, detail: errors[0]!.message, errors, checks: { problem: "cannot_publish" } }, 422);
      rfq.status = "published"; rfq.version = 1; bump(); return send(read());
    }
    if (path === `${base}/amend`) {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.amends.push(body);
      if (typeof body.deadline_at === "string" && body.deadline_at < rfq.deadline_at) return send({ title: "Validation failed", status: 422, detail: "Choose a deadline later than the current one.", errors: [{ field: "deadline_at", message: "Choose a deadline later than the current one." }], checks: { problem: "deadline_earlier" } }, 422);
      const lines = body.lines as { quantity: string }[] | undefined;
      if (sourced && lines && Number(lines[0]!.quantity) < Number(rfq.quantity)) return send({ title: "Conflict", status: 409, detail: "This requisition is fully sourced, so its quantity cannot be reduced here.", checks: { problem: "requisition_sourced" } }, 409);
      if (typeof body.terms === "string") rfq.terms = body.terms;
      rfq.version += 1; bump(); return send(read());
    }
    return send({ detail: "Not found." }, 404);
  });
  return state;
}

const EDIT = `/sourcing/rfqs/${rfqId}/edit`, AMEND = `/sourcing/rfqs/${rfqId}/amend`;
const fillDraft = async (page: Page) => {
  await page.getByLabel(/^Specifications/).fill("Pump spec");
  await page.getByLabel(/^Delivery Location/).fill("Dock 4");
  await page.getByLabel(/^Delivery Terms/).fill("DAP");
  await page.getByLabel(/^Terms/).fill("Net 30");
};
const invite = (page: Page, name: string) => page.getByRole("checkbox", { name: `Invite ${name}` });

test("lead: edit a draft, add a template, invite three vendors (one ineligible, overridden), publish, amend, see v2", async ({ page }) => {
  const state = await stub(page);
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await page.getByRole("link", { name: "Edit" }).click();
  await expect(page).toHaveURL(EDIT);
  await fillDraft(page);
  await page.getByLabel("Requirements Template").selectOption("t1");
  await page.getByRole("button", { name: "Add From Template" }).click();
  await expect(page.getByRole("textbox", { name: "Requirement 1" })).toHaveValue("Insurance certificate");
  await invite(page, "Alpha Pumps").check();
  await invite(page, "Beta Valves").check();
  await expect(invite(page, "Delta Ltd")).toBeDisabled(); // reasons are shown; only Override opens it
  await expect(page.getByText("No valid insurance certificate").first()).toBeVisible();
  await page.getByRole("button", { name: "Override eligibility for Delta Ltd" }).click();
  await page.getByRole("button", { name: "Invite Selected" }).click();
  await expect(page.getByText("Enter a reason to override Delta Ltd.").first()).toBeVisible();
  await page.getByLabel("Override reason for Delta Ltd").fill("Sole source");
  await page.getByRole("button", { name: "Invite Selected" }).click();
  await expect(page.getByText("3 of 5 invited.")).toBeVisible();
  expect(state.invites.map(b => b.override_reason)).toEqual([undefined, undefined, "Sole source"]);
  await page.getByRole("button", { name: "Publish RFQ" }).click();
  const dialog = page.getByRole("dialog", { name: "Publish RFQ" });
  await expect(dialog.getByText("Contact 1")).toBeVisible();
  expect(state.patches[0]!.ifMatch).toBe("5"); // saved first with the current revision: 1, the template, three invitations
  await dialog.getByRole("button", { name: "Publish RFQ" }).click();
  await expect(page).toHaveURL(`/sourcing/rfqs/${rfqId}`);
  await page.getByRole("link", { name: "Amend" }).click();
  await expect(page).toHaveURL(AMEND);
  await page.getByLabel(/^Terms/).fill("Net 45");
  await page.getByLabel(/^Reason for the Amendment/).fill("Payment terms moved");
  await expect(page.getByRole("heading", { name: "What Changed" })).toBeVisible();
  await page.getByRole("button", { name: "Amend RFQ" }).click();
  await expect(page).toHaveURL(`/sourcing/rfqs/${rfqId}`);
  await expect(page.getByText("Version 2").first()).toBeVisible();
  expect(state.amends[0]).toEqual({ reason: "Payment terms moved", terms: "Net 45" });
  expect(state.vendorListCalls).toBe(0); // the picker never calls /vendors
});

test("a requester without rfq.approve is offered no Override", async ({ page }) => {
  await stub(page, { canApprove: false });
  await page.goto(EDIT);
  await expect(page.getByText("No valid insurance certificate").first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Override/ })).toHaveCount(0);
  await expect(invite(page, "Delta Ltd")).toBeDisabled();
});

test("a sixth invitee is refused in words, even for the lead", async ({ page }) => {
  const state = await stub(page);
  await page.goto(EDIT);
  for (const name of ["Alpha Pumps", "Beta Valves", "Gamma Seals", "Epsilon Co", "Zeta Works"]) await invite(page, name).check();
  await page.getByRole("button", { name: "Invite Selected" }).click();
  await expect(page.getByText("5 of 5 invited.")).toBeVisible();
  await invite(page, "Eta Parts").check();
  await page.getByRole("button", { name: "Invite Selected" }).click();
  await expect(page.getByText("Up to 5 vendors per RFQ. Remove one to invite Eta Parts.").first()).toBeVisible();
  expect(state.rfq.invitees).toHaveLength(5);
});

test("publish lists every problem in a focused summary and keeps the input", async ({ page }) => {
  const state = await stub(page);
  await page.goto(EDIT);
  await page.getByLabel(/^Specifications/).fill("Only this");
  await page.getByRole("button", { name: "Publish RFQ" }).click();
  await page.getByRole("dialog", { name: "Publish RFQ" }).getByRole("button", { name: "Publish RFQ" }).click();
  const summary = page.getByRole("alert").filter({ hasText: "Review the fields below" });
  await expect(summary).toBeFocused();
  for (const message of ["Add the delivery location.", "Add the delivery terms.", "Add the terms.", "Invite at least one vendor."]) await expect(summary.getByText(message)).toBeVisible();
  await expect(summary.getByText("Add the specifications.")).toHaveCount(0);
  await expect(page.getByLabel(/^Specifications/)).toHaveValue("Only this");
  expect(state.published).toBe(1);
  await expect(page.getByLabel(/^Delivery Location/)).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByRole("button", { name: "Publish RFQ" })).toBeEnabled();
});

test("amend: requirements, files and the vendor list are read-only; contacts stay editable; What Changed lists exactly the edits", async ({ page }) => {
  const state = await stub(page, { status: "published", blank: false });
  await page.goto(AMEND);
  await expect(page.getByText("Requirements cannot change after publication.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Upload File|Add From Template|Remove|Invite Selected/ })).toHaveCount(0);
  await expect(page.getByLabel("Search Vendors")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Who Will Be Told" })).toBeVisible();
  await page.getByRole("checkbox", { name: /Second 1/ }).check();
  await page.getByRole("button", { name: "Save contacts for Alpha Pumps" }).click();
  await expect(page.getByRole("heading", { name: "What Changed" })).toBeVisible();
  await expect(page.getByText("Nothing has changed yet.")).toBeVisible(); // contacts are not part of the diff
  await page.getByLabel(/^Terms/).fill("Net 45");
  await page.getByLabel(/^Quantity, line 1/).fill("6");
  const changed = page.locator("section[aria-labelledby=rfq-card-changed]");
  await expect(changed.getByRole("listitem")).toHaveCount(2);
  await expect(changed.getByText("Terms: now Net 45,")).toBeVisible();
  await expect(changed.locator("s", { hasText: "Net 30" })).toBeVisible();
  await expect(changed.locator("s", { hasText: "4" })).toBeVisible();
  expect(state.amends).toHaveLength(0);
});

test("amend: an earlier deadline shows on its field; a lowered quantity on a sourced requisition shows in the summary and on the lines card", async ({ page }) => {
  await stub(page, { status: "published", blank: false, sourced: true });
  await page.goto(AMEND);
  await page.getByLabel(/^Response Deadline Date/).fill("2029-12-31");
  await page.getByLabel(/^Reason for the Amendment/).fill("Vendors asked");
  await page.getByRole("button", { name: "Amend RFQ" }).click();
  await expect(page.getByLabel(/^Response Deadline Date/)).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByText("Choose a deadline later than the current one.").first()).toBeVisible();
  await page.getByLabel(/^Response Deadline Date/).fill("2030-01-01");
  await page.getByLabel(/^Quantity, line 1/).fill("2");
  await page.getByRole("button", { name: "Amend RFQ" }).click();
  const summary = page.getByRole("alert").filter({ hasText: "Review the fields below" });
  await expect(summary.getByText("This requisition is fully sourced, so its quantity cannot be reduced here.")).toBeVisible();
  await expect(page.locator("section[aria-labelledby=rfq-card-items]").getByText("This requisition is fully sourced")).toBeVisible();
  await expect(page.getByLabel(/^Quantity, line 1/)).toHaveValue("2");
  await expect(page.getByLabel(/^Reason for the Amendment/)).toHaveValue("Vendors asked");
});

test("keyboard only: tick a vendor in the picker and drive the publish dialog", async ({ page }) => {
  await stub(page, { blank: false });
  await page.goto(EDIT);
  await invite(page, "Alpha Pumps").focus();
  await page.keyboard.press("Space");
  await expect(invite(page, "Alpha Pumps")).toBeChecked();
  await page.getByRole("button", { name: "Invite Selected" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("1 of 5 invited.")).toBeVisible();
  await page.getByRole("button", { name: "Publish RFQ" }).focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Publish RFQ" });
  await expect(dialog.getByRole("button", { name: "Keep Editing" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  for (const [name, route, status] of [["edit", EDIT, "draft"], ["amend", AMEND, "published"]] as const) {
    test(`/sourcing/rfqs/:id/${name} passes axe in ${theme} theme`, async ({ page }) => {
      await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
      await stub(page, { status, blank: false });
      await page.goto(route);
      await page.locator(".app-shell").waitFor();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.getByLabel(/^Specifications/)).toBeVisible();
      if (name === "edit") await expect(invite(page, "Alpha Pumps")).toBeVisible();
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      if (name === "edit") {
        await page.getByRole("button", { name: "Publish RFQ" }).click();
        await expect(page.getByRole("dialog", { name: "Publish RFQ" })).toBeVisible();
        expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      }
    });
  }
}
