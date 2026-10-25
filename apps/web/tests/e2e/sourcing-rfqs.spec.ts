import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const rfqId = "11111111-1111-4111-8111-111111111111";
const draftId = "22222222-2222-4222-8222-222222222222";
const reqId = "33333333-3333-4333-8333-333333333333";
const vendorId = "44444444-4444-4444-8444-444444444444";
const unitId = "55555555-5555-4555-8555-555555555555";
const NOW = "2026-10-01T00:00:00Z";
const minute = (ms: number) => new Date(Math.floor((Date.now() + ms) / 60_000) * 60_000).toISOString();

type Options = { canEdit?: boolean; bounced?: boolean; refuseServer?: boolean; twoPages?: boolean };

/** An in-memory stand-in for the sourcing API: one published RFQ and one draft. */
async function stubRfqs(page: Page, { canEdit = true, bounced = false, refuseServer = false, twoPages = false }: Options = {}) {
  const state = { deadline: minute(2 * 86_400_000), version: 1, posts: [] as { path: string; key: string | undefined; body: unknown }[], requests: [] as URL[] };
  const detail = () => ({
    id: rfqId, number: "RFQ-0001", title: "Centrifugal pumps", status: "published", unit_id: unitId, currency: "USD", owner_email: "buyer@example.test",
    version: state.version, edit_revision: state.version, created_at: NOW, updated_at: NOW, deadline_at: state.deadline, deadline_tz: "UTC", published_at: NOW,
    lines: [{ id: "a1", line_no: 1, requisition_id: reqId, requisition_number: "REQ-0042", requisition_line_id: "b1", requisition_line_no: 1, requisition_version: 1, quantity: "4.0000", description: "Pump, 3 inch", uom_code: "EA", needed_by: null, currency: "USD", open_qty: "0.0000", specification: null }],
    invitees: [{ vendor_id: vendorId, contact_ids: ["c1"], vendor_snapshot: { trading_name: "Acme Tooling" }, override_reason: null, invited_by_email: "buyer@example.test", invited_at: NOW, eligible: true, reasons: [],
      deliveries: [{ contact_id: "c1", version: 1, state: bounced ? "bounced" : "sent" }], latest_sent_version: 1, not_told_of: state.version > 1 ? [2] : [] }],
    can_edit: canEdit, can_approve: false,
  });
  const row = (id: string, number: string, status: string) => ({ id, number, title: "Centrifugal pumps", status, unit_id: unitId, owner_email: "buyer@example.test", deadline_at: state.deadline, deadline_tz: "UTC", invitee_count: 1, requisition_numbers: ["REQ-0042"], bids_received: 0 });
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    const send = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/me") return send({ identity_id: unitId });
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (path === "/api/v1/sourcing/rfqs") {
      state.requests.push(url);
      const tab = url.searchParams.get("tab") ?? "all";
      const all = [row(rfqId, "RFQ-0001", "published"), row(draftId, "RFQ-0002", "draft")];
      const rows = tab === "drafts" ? all.slice(1) : tab === "open" ? all.slice(0, 1) : tab === "all" ? all : [];
      if (twoPages && tab === "all") {
        const second = url.searchParams.get("cursor") === "second-page";
        return send({ rows: [all[second ? 1 : 0]], next_cursor: second ? null : "second-page", counts: { drafts: 1, open: 1, evaluating: 0, awarded: 0, all: 2 } });
      }
      return send({ rows, next_cursor: null, counts: { drafts: 1, open: 1, evaluating: 0, awarded: 0, all: 2 } });
    }
    if (path === `/api/v1/sourcing/rfqs/${rfqId}`) return send(detail());
    if (path === `/api/v1/sourcing/rfqs/${rfqId}/bids`) return send([]);
    if (path === `/api/v1/sourcing/rfqs/${rfqId}/versions`) return send(Array.from({ length: state.version }, (_, i) => ({ version: i + 1, reason: i ? "Vendors asked" : null, diff: i ? { deadline_at: { was: "2026-10-02", now: "2026-10-09" } } : {}, snapshot: {}, published_by_email: "buyer@example.test", published_at: NOW })));
    if (path === `/api/v1/sourcing/rfqs/${rfqId}/history`) return send([...(state.version > 1 ? [{ action: "rfq.extend", at: NOW, actor_email: "buyer@example.test", reason: "Vendors asked" }] : []), { action: "rfq.publish", at: NOW, actor_email: "buyer@example.test", reason: null }]);
    if (path === `/api/v1/sourcing/rfqs/${rfqId}/extend-deadline` && req.method() === "POST") {
      const body = req.postDataJSON();
      state.posts.push({ path, key: req.headers()["idempotency-key"], body });
      if (refuseServer) return send({ title: "Validation failed", status: 422, detail: "Choose a deadline later than the current one.", errors: [{ field: "deadline_at", message: "The vendors have already been told a later date." }] }, 422);
      state.deadline = body.deadline_at; state.version = 2;
      return send(detail());
    }
    return send({ detail: "Not found." }, 404);
  });
  return state;
}

const FORBIDDEN = ["Evaluators", "Start RFQ", "Cancel RFQ", "Open Comparison", "View Award"];

test("tabs and counts, open an RFQ, extend its deadline and see the new version unsent", async ({ page }) => {
  const state = await stubRfqs(page);
  await page.goto("/sourcing/rfqs");
  await expect(page.getByRole("tab", { name: /^Drafts/ })).toContainText("1");
  await expect(page.getByRole("tab", { name: /^All/ })).toContainText("2");
  await expect(page.getByText("1 of 5").first()).toBeVisible();
  await page.getByRole("tab", { name: /^Drafts/ }).click();
  await expect(page.getByRole("link", { name: "RFQ-0001" })).toHaveCount(0);
  await page.getByRole("tab", { name: /^Open/ }).click();
  expect(state.requests.every(u => Number(u.searchParams.get("page_size")) <= 50)).toBe(true);
  await page.getByRole("link", { name: "RFQ-0001" }).click();
  await expect(page.getByRole("heading", { name: /RFQ-0001/ })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "Published" }).first()).toHaveAttribute("aria-current", "step");
  await expect(page.getByRole("link", { name: "REQ-0042" }).first()).toBeVisible();
  await page.getByRole("button", { name: "Extend Deadline" }).click();
  await page.getByLabel("New Date").fill("2099-01-01");
  await page.getByRole("button", { name: "Extend Deadline" }).last().click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.posts[0]!.key).toBeTruthy();
  await expect(page.getByText("Version 2").first()).toBeVisible();
  await expect(page.getByText("Not yet told of version 2")).toBeVisible();
  await expect(page.getByText("Vendors asked").first()).toBeVisible();
});

test("the shared grid loads the next page with the server's cursor", async ({ page }) => {
  const state = await stubRfqs(page, { twoPages: true });
  await page.goto("/sourcing/rfqs");
  await expect(page.getByRole("link", { name: "RFQ-0001" })).toBeVisible();
  await expect(page.getByRole("link", { name: "RFQ-0002" })).toHaveCount(0);
  await page.getByRole("button", { name: /^Load next/ }).click();
  await expect(page.getByRole("link", { name: "RFQ-0002" })).toBeVisible();
  await expect(page.getByRole("link", { name: "RFQ-0001" })).toBeVisible();
  const cursors = state.requests.map(u => u.searchParams.get("cursor")); // dev strict mode may repeat the first load
  expect(cursors.at(-1)).toBe("second-page");
  expect(cursors.filter(c => c === "second-page")).toHaveLength(1);
  await expect(page.getByRole("button", { name: /^Load next/ })).toHaveCount(0);
});

test("the field refuses an earlier and an equal deadline before anything is sent", async ({ page }) => {
  const state = await stubRfqs(page);
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await page.getByRole("button", { name: "Extend Deadline" }).click();
  const submit = page.getByRole("button", { name: "Extend Deadline" }).last();
  await submit.click(); // the dialog opens on the current deadline: equal
  await expect(page.getByRole("alert").filter({ hasText: "later than the current one" })).toBeVisible();
  await expect(page.getByLabel("New Date")).toBeFocused();
  await page.getByLabel("New Date").fill("2020-01-01");
  await submit.click();
  await expect(page.getByRole("alert").filter({ hasText: "later than the current one" })).toBeVisible();
  expect(state.posts).toHaveLength(0);
});

test("a server refusal shows on the field and the input is kept", async ({ page }) => {
  const state = await stubRfqs(page, { refuseServer: true });
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await page.getByRole("button", { name: "Extend Deadline" }).click();
  await page.getByLabel("New Date").fill("2099-01-01");
  await page.getByLabel(/^Reason/).fill("Vendors asked");
  await page.getByRole("button", { name: "Extend Deadline" }).last().click();
  await expect(page.getByRole("alert").filter({ hasText: "already been told a later date" })).toBeVisible();
  await expect(page.getByLabel("New Date")).toHaveValue("2099-01-01");
  await expect(page.getByLabel(/^Reason/)).toHaveValue("Vendors asked");
  expect(state.posts).toHaveLength(1);
});

test("without rfq.edit there is no Extend Deadline and no Change Contact; with it, a bounce offers Change Contact", async ({ page }) => {
  await stubRfqs(page, { canEdit: false, bounced: true });
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await expect(page.getByRole("heading", { name: "Invitees" })).toBeVisible();
  await expect(page.getByText("1 bounced")).toBeVisible();
  await expect(page.getByRole("button", { name: /Extend Deadline|Change Contact/ })).toHaveCount(0);
});

test("a bounce shows Change Contact to someone who can edit", async ({ page }) => {
  await stubRfqs(page, { bounced: true });
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  await expect(page.getByRole("button", { name: /Change Contact/ })).toBeVisible();
});

test("/sourcing/rfqs shows New RFQ and it lands on the new page", async ({ page }) => {
  await stubRfqs(page);
  await page.goto("/sourcing/rfqs");
  await page.getByRole("link", { name: "New RFQ", exact: true }).click();
  await expect(page).toHaveURL("/sourcing/rfqs/new");
});

test("nothing owned by a later story is in the DOM", async ({ page }) => {
  await stubRfqs(page);
  for (const path of ["/sourcing/rfqs", `/sourcing/rfqs/${rfqId}`]) {
    await page.goto(path);
    await expect(page.locator(".req-page")).toBeVisible();
    for (const name of FORBIDDEN) {
      await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
      await expect(page.getByRole("link", { name, exact: true })).toHaveCount(0);
      await expect(page.getByRole("columnheader", { name, exact: true })).toHaveCount(0);
      await expect(page.getByRole("heading", { name, exact: true })).toHaveCount(0);
    }
  }
});

test("keyboard: tabs by arrows, dialog opens on the safe button, Escape returns focus to the opener", async ({ page }) => {
  await stubRfqs(page);
  await page.goto("/sourcing/rfqs");
  await page.getByRole("tab", { name: /^All/ }).focus();
  await page.keyboard.press("Home");
  await expect(page.getByRole("tab", { name: /^Drafts/ })).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /^Open/ })).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: /^All/ })).toBeFocused();
  await page.goto(`/sourcing/rfqs/${rfqId}`);
  const opener = page.getByRole("button", { name: "Extend Deadline" });
  await opener.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Keep Current Deadline" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(opener).toBeFocused();
});

for (const theme of ["light", "dark"] as const) {
  for (const route of ["/sourcing/rfqs", `/sourcing/rfqs/${rfqId}`]) {
    test(`${route.replace(rfqId, ":id")} passes axe in ${theme} theme`, async ({ page }) => {
      await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
      await stubRfqs(page, { bounced: true });
      await page.goto(route);
      await page.locator(".app-shell").waitFor();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.getByText(route.endsWith("rfqs") ? "RFQ-0001" : "Acme Tooling").first()).toBeVisible();
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    });
  }
}
