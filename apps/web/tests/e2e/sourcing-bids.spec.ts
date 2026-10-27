import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { comparison } from "./comparison-fixture";

const rfqId = "11111111-1111-4111-8111-111111111111";
const vendorId = "44444444-4444-4444-8444-444444444444";
const unitId = "55555555-5555-4555-8555-555555555555";
const reqId = "33333333-3333-4333-8333-333333333333";
const NOW = "2026-10-01T00:00:00Z";
const minute = (ms: number) => new Date(Math.floor((Date.now() + ms) / 60_000) * 60_000).toISOString();
const HOUR = 3_600_000;

type Options = { lead?: boolean; deadline?: string; tz?: string; preload?: "other-late-draft" | "other-ontime-draft" | "submitted" | "replaced" };
type Stored = { id: string; status: "draft" | "submitted"; sent_at: string; currency: string; mine: boolean; version: number; lines: { rfq_line_id: string; quoted: boolean; unit_price: string | null; tax: string | null }[]; evidence: string | null; keyed_at?: string; late: { reason: string } | null; replaces: string | null; freight: string };

/** An in-memory stand-in for the sourcing API: one published RFQ with two lines and one invited vendor. */
async function stubBids(page: Page, { lead = false, deadline = minute(2 * 24 * HOUR), tz = "UTC", preload }: Options = {}) {
  const state = { bids: [] as Stored[], posts: [] as { path: string; method: string; key?: string; ifMatch?: string; body: unknown }[] };
  const make = (patch: Partial<Stored>): Stored => ({ id: crypto.randomUUID(), status: "draft", sent_at: minute(-HOUR), currency: "USD", mine: true, version: 1, lines: [], evidence: null, late: null, replaces: null, freight: "0", ...patch });
  if (preload === "other-late-draft") state.bids.push(make({ mine: false, evidence: "e1", sent_at: new Date(new Date(deadline).getTime() + HOUR).toISOString(), lines: [{ rfq_line_id: "l1", quoted: true, unit_price: "1", tax: "0" }, { rfq_line_id: "l2", quoted: false, unit_price: null, tax: null }] }));
  // sent an hour before the deadline, keyed an hour after it: on time, because the vendor's time decides (decision 145)
  if (preload === "other-ontime-draft") state.bids.push(make({ mine: false, evidence: "e1", sent_at: new Date(new Date(deadline).getTime() - HOUR).toISOString(), keyed_at: new Date(new Date(deadline).getTime() + HOUR).toISOString() }));
  if (preload === "replaced") {
    const original = make({ status: "submitted", evidence: "e1", freight: "2", lines: [{ rfq_line_id: "l1", quoted: true, unit_price: "10", tax: "1" }, { rfq_line_id: "l2", quoted: false, unit_price: null, tax: null }] });
    state.bids.push(original, make({ status: "submitted", evidence: "e1", replaces: original.id, lines: [{ rfq_line_id: "l1", quoted: true, unit_price: "9", tax: "1" }, { rfq_line_id: "l2", quoted: false, unit_price: null, tax: null }] }));
  }
  if (preload === "submitted") state.bids.push(make({ status: "submitted", evidence: "e1", currency: "EUR", version: 1 }));
  const rev = (b: Stored) => 1 + b.lines.length + (b.evidence ? 1 : 0) + (b.status === "submitted" ? 5 : 0) + (b.late ? 1 : 0);
  const passed = () => new Date(deadline).getTime() <= Date.now();
  const read = (b: Stored) => {
    const sealed = (b.status === "draft" && !b.mine) || (b.status === "submitted" && !passed());
    const head = { id: b.id, rfq_id: rfqId, vendor_id: vendorId, keyed_by: b.mine ? unitId : "99999999-9999-4999-8999-999999999999", status: b.status, sent_at: b.sent_at, keyed_at: b.keyed_at ?? NOW, submitted_at: b.status === "submitted" ? NOW : null, replaces_bid_id: b.replaces, replaced_by_bid_id: state.bids.find(x => x.replaces === b.id && x.status === "submitted")?.id ?? null, row_version: rev(b), late: b.late ? { accepted_by_name: "Lena Lead", accepted_by_email: "lead@example.test", at: NOW, reason: b.late.reason } : null };
    if (sealed) return head;
    const qty: Record<string, number> = { l1: 4, l2: 2 };
    const quoted = b.lines.filter(l => l.quoted);
    const lineTotal = (l: Stored["lines"][number]) => Number(l.unit_price) * qty[l.rfq_line_id]!;
    const lines = Math.round(quoted.reduce((n, l) => n + lineTotal(l), 0) * 100) / 100, tax = quoted.reduce((n, l) => n + Number(l.tax), 0), freight = Number(b.freight);
    const money = (n: number) => n.toFixed(2);
    return { ...head, rfq_version: b.version, currency: b.currency, evidence_attachment_id: b.evidence, freight: b.freight, lead_time_days: 14, valid_until: "2099-01-31", delivery_terms: "DAP Kuwait", warranty: "2 years", exceptions: "Excludes crating", current: b.status === "submitted", requirements: [{ requirement_id: "q1", met: false, note: "Certificate pending" }],
      lines: b.lines.map(l => ({ rfq_line_id: l.rfq_line_id, quantity: String(qty[l.rfq_line_id]), quoted: l.quoted, unit_price: l.unit_price, tax: l.tax ?? "0", line_total: l.quoted ? money(lineTotal(l)) : null })),
      totals: { lines: money(lines), tax: money(tax), freight: money(freight), total: money(lines + tax + freight) } };
  };
  const detail = () => ({
    id: rfqId, number: "RFQ-0001", title: "Centrifugal pumps", status: "published", unit_id: unitId, currency: "USD", owner_email: "buyer@example.test", version: 1, edit_revision: 1, created_at: NOW, updated_at: NOW, deadline_at: deadline, deadline_tz: tz, published_at: NOW,
    lines: [1, 2].map(n => ({ id: `l${n}`, line_no: n, requisition_id: reqId, requisition_number: "REQ-0042", requisition_line_id: `b${n}`, requisition_line_no: n, requisition_version: 1, quantity: n === 1 ? "4.0000" : "2.0000", description: `Item ${n}`, uom_code: "EA", needed_by: null, currency: "USD", open_qty: "0.0000", specification: null })),
    invitees: [{ vendor_id: vendorId, contact_ids: ["c1"], vendor_snapshot: { trading_name: "Acme Tooling" }, override_reason: null, invited_by_email: "buyer@example.test", invited_at: NOW, eligible: true, reasons: [], deliveries: [{ contact_id: "c1", version: 1, state: "sent" }], latest_sent_version: 1, not_told_of: [] }],
    can_edit: true, can_approve: lead, bids_received: state.bids.filter(b => b.status === "submitted" && !state.bids.some(x => x.replaces === b.id && x.status === "submitted")).length, requirements: [{ id: "q1", position: 1, text: "ISO 9001 certified", waived: false, waived_by_email: null, waived_by_name: null, waived_reason: null }],
  });
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    const send = (body: unknown, status = 200, headers: Record<string, string> = {}) => route.fulfill({ status, contentType: "application/json", headers, body: JSON.stringify(body) });
    const base = `/api/v1/sourcing/rfqs/${rfqId}`;
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/me") return send({ identity_id: unitId });
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (path === "/api/v1/sourcing/rfqs") return send({ rows: [{ id: rfqId, number: "RFQ-0001", title: "Centrifugal pumps", status: "published", unit_id: unitId, owner_email: "buyer@example.test", deadline_at: deadline, deadline_tz: tz, invitee_count: 1, requisition_numbers: ["REQ-0042"], bids_received: detail().bids_received }], next_cursor: null, counts: { drafts: 0, open: 1, evaluating: 0, awarded: 0, all: 1 } });
    if (path === base) return send(detail());
    if (path === `${base}/evaluation`) return send({ status: "published", scoring_open: false, criteria: [], source: "default", edit_revision: 1, me: null, evaluators: null, evaluator_count: 0, complete: false, vendors: [], scores: [], weighted: null });
    if (path === `${base}/comparison`) return send(comparison());
    if (path === `${base}/versions` || path === `${base}/history`) return send([]);
    if (path === `${base}/bids` && method === "GET") return send(state.bids.filter(b => lead || b.mine).map(read));
    const record = (body: unknown) => state.posts.push({ path: path.replace(base, ""), method, key: req.headers()["idempotency-key"], ifMatch: req.headers()["if-match"], body });
    if (path === `${base}/bids` && method === "POST") {
      const body = req.postDataJSON(); record(body);
      const bid = make({ sent_at: body.sent_at, currency: body.currency, version: body.rfq_version, lines: [] }); state.bids.push(bid);
      return send(read(bid), 201, { etag: `"${rev(bid)}"` });
    }
    const upload = /\/api\/v1\/documents\/bid\/([^/]+)\/attachments$/.exec(path);
    if (upload && method === "POST") { record("file"); return send({ id: "evidence-1" }, 201); }
    const one = new RegExp(`^${base}/bids/([^/]+)(/submit|/replace)?$`).exec(path);
    const bid = one && state.bids.find(b => b.id === one[1]);
    if (!one || !bid) return send({ detail: "Not found." }, 404);
    if (method === "GET") return send(read(bid), 200, { etag: `"${rev(bid)}"` });
    if (method === "PATCH") {
      const body = req.postDataJSON(); record(body);
      Object.assign(bid, { sent_at: body.sent_at, currency: body.currency, version: body.rfq_version, freight: body.freight, evidence: body.evidence_attachment_id ?? bid.evidence, lines: body.lines });
      return send(read(bid));
    }
    if (one[2] === "/submit") {
      const body = req.postDataJSON() ?? {}; record(body);
      if (new Date(bid.sent_at).getTime() > new Date(deadline).getTime()) {
        if (!body.accept_late_reason) return send({ title: "Conflict", status: 409, detail: "The vendor sent this bid after the bidding stop time. Enter a reason.", checks: { problem: "late_bid" } }, 409);
        if (!lead) return send({ title: "Forbidden", status: 403, detail: "Forbidden." }, 403);
        bid.late = { reason: body.accept_late_reason };
      }
      bid.status = "submitted";
      return send(read(bid));
    }
    if (one[2] === "/replace") {
      const body = req.postDataJSON(); record(body);
      if (passed()) return send({ title: "Conflict", status: 409, detail: "The bidding stop time has passed.", checks: { problem: "deadline_passed" } }, 409);
      const next = make({ sent_at: body.sent_at, currency: bid.currency, version: bid.version, replaces: bid.id, lines: [{ rfq_line_id: "l1", quoted: false, unit_price: null, tax: null }, { rfq_line_id: "l2", quoted: false, unit_price: null, tax: null }] }); state.bids.push(next);
      return send(read(next), 201);
    }
    return send({ detail: "Not found." }, 404);
  });
  return state;
}

const stamp = (iso: string) => ({ date: iso.slice(0, 10), time: iso.slice(11, 16) });
async function key(page: Page, sent: { date: string; time: string }, { prices = true }: { prices?: boolean } = {}) {
  await page.getByLabel("Vendor Sent It On (date)").fill(sent.date);
  await page.getByLabel(/^Time \(24-hour/).fill(sent.time);
  await page.getByLabel("Evidence (the vendor's email or file)").setInputFiles({ name: "vendor-email.txt", mimeType: "text/plain", buffer: Buffer.from("quote") });
  if (prices) {
    await page.getByLabel("Unit price, line 1 (USD)").fill("10");
    await page.getByLabel("Tax, line 1 (USD)").fill("5");
    await page.getByLabel("Line 2 not quoted").check();
  }
}

test("record a bid with one Not Quoted line and typed tax, submit it, see it received and counted", async ({ page }) => {
  const state = await stubBids(page);
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await expect(page.getByRole("heading", { name: "Bids", exact: true })).toBeVisible();
  await expect(page.getByText("Bid: No bid yet")).toBeVisible();
  await page.getByRole("button", { name: "Record Bid" }).click();
  await key(page, stamp(minute(-HOUR)));
  await page.getByRole("button", { name: "Save Draft" }).click();
  await expect(page.getByRole("row", { name: /^Total/ })).toContainText("45.00 USD"); // 4 x 10 + 5 tax, from the server
  const patch = state.posts.find(p => p.method === "PATCH")!;
  expect(patch.ifMatch).toMatch(/^"?\d+"?$/);
  expect((patch.body as { lines: unknown[] }).lines).toEqual([{ rfq_line_id: "l1", quoted: true, unit_price: "10", tax: "5" }, { rfq_line_id: "l2", quoted: false }]);
  await page.getByRole("button", { name: "Submit Bid" }).click();
  await expect(page.getByText("Received. The figures stay sealed")).toBeVisible();
  await expect(page.locator("section[aria-labelledby=rfq-card-bids]").getByText("1 bid received.")).toBeVisible(); // the detail count moves at once
  expect(state.posts.every(p => p.method === "PATCH" || p.key)).toBe(true); // every POST carries an Idempotency-Key
  await page.keyboard.press("Escape");
  await expect(page.getByText("Bid: Received (sealed)")).toBeVisible();
  await page.goto("/sourcing/rfqs");
  await expect(page.getByRole("columnheader", { name: "Bids" })).toBeVisible({ timeout: 15_000 }); // the grid mounts after a session call
  await expect(page.getByRole("row", { name: /RFQ-0001/ }).getByRole("cell", { name: "1", exact: true })).toBeVisible();
});

test("a quoted line with a blank tax cannot be saved and the error names the line", async ({ page }) => {
  const state = await stubBids(page);
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await page.getByRole("button", { name: "Record Bid" }).click();
  await key(page, stamp(minute(-HOUR)));
  await page.getByLabel("Tax, line 1 (USD)").fill("");
  await page.getByRole("button", { name: "Save Draft" }).click();
  await expect(page.getByRole("alert").getByText("Enter the tax on this line, or 0 if none.")).toBeVisible();
  await expect(page.getByLabel("Unit price, line 1 (USD)")).toHaveValue("10"); // input kept
  expect(state.posts).toHaveLength(0);
});

test.describe("the vendor's time is read in the RFQ's zone, whatever the browser's", () => {
  test.use({ timezoneId: "America/New_York" });
  test("23:30 in Asia/Kolkata for a 23:59 Asia/Kolkata deadline is on time", async ({ page }) => {
    const state = await stubBids(page, { deadline: "2099-12-01T18:29:00.000Z", tz: "Asia/Kolkata" });
    await page.goto(`/sourcing/rfqs/${rfqId}`);
    await page.getByRole("button", { name: "Record Bid" }).click();
    await key(page, { date: "2099-12-01", time: "23:30" });
    await page.getByRole("button", { name: "Submit Bid" }).click();
    await expect(page.getByText("Received. The figures stay sealed")).toBeVisible();
    expect((state.posts[0]!.body as { sent_at: string }).sent_at).toBe("2099-12-01T18:00:00.000Z");
    await expect(page.getByText("This bid is late")).toHaveCount(0);
  });
});

test("a clock-change gap in the vendor's time is refused in words", async ({ page }) => {
  await stubBids(page, { tz: "Europe/London" });
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await page.getByRole("button", { name: "Record Bid" }).click();
  await key(page, { date: "2026-03-29", time: "01:30" });
  await page.getByRole("button", { name: "Save Draft" }).click();
  await expect(page.getByText("This time does not exist in Europe/London (the clocks change). Enter the vendor's time again.").first()).toBeVisible();
});

test("late: a buyer is blocked with no accept action, a lead accepts the same draft with a reason", async ({ page, browser }) => {
  const deadline = minute(-2 * HOUR), sent = stamp(minute(-HOUR));
  await stubBids(page, { deadline });
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await page.getByRole("button", { name: "Record Bid" }).click();
  await key(page, sent);
  await page.getByRole("button", { name: "Submit Bid" }).click();
  await expect(page.getByText("This bid is late.")).toBeVisible();
  await expect(page.getByText("Ask a sourcing lead to open this draft and accept it.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Accept Late Bid/ })).toHaveCount(0);

  const lead = await browser.newPage();
  const state = await stubBids(lead, { lead: true, deadline, preload: "other-late-draft" });
  await lead.goto(`/sourcing/rfqs/${rfqId}`);
  await expect(lead.getByText("Bid: Draft, Late — Waiting for a Lead")).toBeVisible();
  await lead.getByRole("button", { name: "Record Bid" }).click();
  await lead.getByRole("button", { name: "Accept Late Bid and Save" }).click();
  await expect(lead.getByText("Enter a reason of 1 to 500 characters.").first()).toBeVisible();
  await lead.getByLabel("Reason for Accepting a Late Bid").fill("Courier strike");
  await lead.getByRole("button", { name: "Accept Late Bid and Save" }).click();
  await expect(lead.getByText("Late, Accepted By Override").first()).toBeVisible();
  expect(state.posts.at(-1)!.body).toEqual({ accept_late_reason: "Courier strike" });
  expect(state.posts.at(-1)!.ifMatch).toBeTruthy();
  await lead.keyboard.press("Escape");
  await expect(lead.getByText("Bid: Late, Accepted By Override")).toBeVisible();
});

test("lateness is the vendor's sent time, never the keyed time", async ({ page }) => {
  await stubBids(page, { lead: true, deadline: minute(-2 * HOUR), preload: "other-ontime-draft" });
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await expect(page.getByText("Bid: Draft", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Record Bid" }).click();
  await expect(page.getByText("This draft was sent on time.")).toBeVisible();
  await expect(page.getByText("This bid is late.")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Accept Late Bid/ })).toHaveCount(0);
});

test("after the deadline the Bids card opens the comparison and a submitted bid shows its whole response", async ({ page }) => {
  await stubBids(page, { lead: true, deadline: minute(-HOUR), preload: "replaced" });
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await page.getByRole("link", { name: "Open the bid comparison" }).click();
  await expect(page).toHaveURL(`/sourcing/rfqs/${rfqId}/comparison`);
  await expect(page.getByRole("heading", { name: "RFQ-0001 · Centrifugal pumps", level: 2 })).toBeVisible();
  await page.getByRole("link", { name: "Back to the RFQ" }).click();
  await page.getByRole("button", { name: "Record Bid" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("link", { name: "Open the vendor's email or file" })).toHaveAttribute("href", /\/attachments\/e1\/content$/);
  for (const text of ["14 days", "DAP Kuwait", "2 years", "Excludes crating", "ISO 9001 certified", "Not Met", "Certificate pending"]) await expect(dialog.getByText(text).first()).toBeVisible();
});

test("keyboard: follow the replacement chain from the replacement to the original and back", async ({ page }) => {
  await stubBids(page, { lead: true, preload: "replaced" });
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await page.getByRole("button", { name: "Record Bid" }).click(); // opens the replacement: the current bid
  const back = page.getByRole("button", { name: "Open the original bid" });
  await back.focus();
  await page.keyboard.press("Enter");
  const forward = page.getByRole("button", { name: "Open the replacement bid" });
  await expect(forward).toBeVisible();
  await forward.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Open the original bid" })).toBeVisible();
});

test("replace needs a reason and a sent time before the deadline; after it the action is gone", async ({ page }) => {
  const state = await stubBids(page, { preload: "submitted" });
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await page.getByRole("button", { name: "Record Bid" }).click();
  await page.getByRole("button", { name: "Replace", exact: true }).click();
  await page.getByRole("button", { name: "Replace Bid" }).click();
  await expect(page.getByText("Enter a reason of 1 to 500 characters.")).toBeVisible();
  await page.getByLabel("Vendor Sent It On (date)").fill(stamp(minute(-HOUR)).date);
  await page.getByLabel(/^Time \(24-hour/).fill(stamp(minute(-HOUR)).time);
  await page.getByLabel("Reason for Replacing").fill("Typo in price");
  await page.getByRole("button", { name: "Replace Bid" }).click();
  await expect(page.getByRole("button", { name: "Submit Bid" })).toBeVisible(); // the replacement draft opens for keying
  expect(state.posts.at(-1)!.body).toMatchObject({ reason: "Typo in price" });
  // the replacement opens with the original's currency and the sent time just entered, and the first save carries them
  const sent = stamp(minute(-HOUR));
  await expect(page.getByLabel("Vendor's Currency")).toHaveValue("EUR");
  await expect(page.getByLabel("Vendor Sent It On (date)")).toHaveValue(sent.date);
  await expect(page.getByLabel(/^Time \(24-hour/)).toHaveValue(sent.time);
  await page.getByLabel("Evidence (the vendor's email or file)").setInputFiles({ name: "v.txt", mimeType: "text/plain", buffer: Buffer.from("q") });
  await page.getByLabel("Line 1 not quoted").uncheck(); // a replacement copies structure only: every line starts Not Quoted
  await page.getByLabel("Unit price, line 1 (EUR)").fill("9");
  await page.getByLabel("Tax, line 1 (EUR)").fill("0");
  await page.getByRole("button", { name: "Save Draft" }).click();
  await expect.poll(() => state.posts.some(p => p.method === "PATCH")).toBe(true);
  expect(state.posts.find(p => p.method === "PATCH")!.body).toMatchObject({ currency: "EUR", rfq_version: 1, sent_at: new Date(`${sent.date}T${sent.time}:00Z`).toISOString() });

  const after = await page.context().newPage();
  await stubBids(after, { deadline: minute(-HOUR), preload: "submitted" });
  await after.goto(`/sourcing/rfqs/${rfqId}`);
  await after.getByRole("button", { name: "Record Bid" }).click();
  await expect(after.getByText("Bids can no longer be replaced after the bidding stop time.")).toBeVisible();
  await expect(after.getByRole("button", { name: "Replace", exact: true })).toHaveCount(0);
});

test("keyboard: Record Bid opens the sheet, focus stays inside, Escape returns to Record Bid", async ({ page }) => {
  await stubBids(page);
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  const opener = page.getByRole("button", { name: "Record Bid" });
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    expect(await dialog.evaluate(d => d.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
});

for (const theme of ["light", "dark"] as const) {
  test(`the bid sheet passes axe in ${theme} theme`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await stubBids(page);
    await page.goto(`/sourcing/rfqs/${rfqId}`);
    await page.getByRole("button", { name: "Record Bid" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
