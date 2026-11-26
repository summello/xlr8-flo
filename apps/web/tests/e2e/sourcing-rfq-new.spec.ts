import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const unitId = "55555555-5555-4555-8555-555555555555";
const otherUnit = "55555555-5555-4555-8555-555555555556";
const newRfqId = "99999999-9999-4999-8999-999999999999";
const id = (n: number) => `aaaaaaaa-0000-4000-8000-00000000000${n}`;
const NOW = "2026-10-01T00:00:00Z";

type PostMode = "ok" | "abort" | "hold" | "over" | "in_flight";
type Options = { units?: 200 | 403; lines403?: boolean };

const line = (n: number, number: string, patch: Record<string, unknown> = {}) => ({
  line_id: id(n), line_no: n, description: `Part ${n}`, uom_code: "EA", currency: "USD", needed_by: null, requisition_id: number === "REQ-00418" ? "bbbbbbbb-0000-4000-8000-000000000001" : "bbbbbbbb-0000-4000-8000-000000000002",
  requisition_number: number, requisition_version: 3, unit_id: unitId, requested_qty: "10.0000", sourced_qty: "0.0000", awarded_qty: "0.0000", ordered_qty: "0.0000", cancelled_qty: "0.0000", open_qty: "10.0000",
  selectable: true, reason: null, ...patch,
});
const LINES = [
  line(1, "REQ-00418"),
  line(2, "REQ-00418", { requested_qty: "8.0000", sourced_qty: "3.0000", open_qty: "5.0000" }),
  line(3, "REQ-00418", { requested_qty: "2.5000", open_qty: "2.5000" }),
  line(4, "REQ-00500"),
  line(5, "REQ-00500", { currency: "EUR" }),
];

/** An in-memory stand-in for the units, lines and RFQ-create endpoints. */
async function stub(page: Page, { units = 200, lines403 = false }: Options = {}) {
  const state = { lineRequests: [] as URL[], unitRequests: [] as URL[], posts: [] as { key: string | undefined; body: { title: string; lines: { requisition_line_id: string; quantity: string; requisition_version: number }[] } }[], mode: "ok" as PostMode, release: () => {}, held: new Promise<void>(() => {}) };
  state.held = new Promise<void>(resolve => { state.release = resolve; });
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    const send = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/me") return send({ identity_id: unitId });
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: unitId });
    if (path === "/api/v1/org/units") {
      state.unitRequests.push(url);
      if (units === 403) return send({ title: "Forbidden", status: 403, detail: "No." }, 403);
      return send({ rows: [{ id: unitId, name: "North", active: true, kind: "bu" }, { id: otherUnit, name: "South", active: true, kind: "bu" }], next_cursor: null });
    }
    if (path === "/api/v1/sourcing/requisition-lines") {
      state.lineRequests.push(url);
      if (lines403) return send({ title: "Forbidden", status: 403, detail: "No." }, 403);
      const q = url.searchParams.get("q") ?? "", cursor = url.searchParams.get("cursor");
      if (url.searchParams.get("unit_id") === otherUnit) return send({ rows: [], next_cursor: null });
      if (q === "REQ-00418") return send(cursor === "p2" ? { rows: [LINES[2]], next_cursor: null } : { rows: LINES.slice(0, 2), next_cursor: "p2" });
      return send({ rows: LINES, next_cursor: null });
    }
    if (path === "/api/v1/sourcing/rfqs" && req.method() === "POST") {
      state.posts.push({ key: req.headers()["idempotency-key"], body: req.postDataJSON() });
      if (state.mode === "abort") return route.abort();
      if (state.mode === "hold") await state.held;
      if (state.mode === "in_flight") return send({ title: "Conflict", status: 409, type: "https://xlr8flo.app/errors/request_in_flight", detail: "Still running." }, 409);
      if (state.mode === "over") {
        return send({ title: "Conflict", status: 409, type: "https://xlr8flo.app/errors/conflict", detail: "Over.", checks: { problem: "over_open_quantity", [`open.${id(1)}`]: "3.0000", [`requested.${id(1)}`]: "10.0000" }, errors: [{ field: `lines/${id(1)}`, message: "Open 3.0000, requested 10.0000." }] }, 409);
      }
      return send({ id: newRfqId, number: "RFQ-0009", title: "T", status: "draft", unit_id: unitId, currency: "USD", owner_email: "a@b.test", version: 1, edit_revision: 1, created_at: NOW, updated_at: NOW, deadline_at: NOW, deadline_tz: "UTC", published_at: null, lines: [], invitees: [], can_edit: true, can_approve: false }, 201);
    }
    return send({ detail: "Not found." }, 404);
  });
  return state;
}

const START = `/sourcing/rfqs/new?unit=${unitId}&unitName=North&requisition=REQ-00418`;
const create = (page: Page) => page.getByRole("button", { name: /RFQ Draft$/ });
const box = (page: Page, n: number, req = "REQ-00418") => page.getByRole("checkbox", { name: `Source line ${n} of ${req}` });
const qty = (page: Page, n: number, req = "REQ-00418") => page.getByRole("textbox", { name: `Quantity to source, line ${n} of ${req}` });

test("Start RFQ path: units 403, fixed unit, both pages pre-checked, split one line, create, land on the RFQ", async ({ page }) => {
  const state = await stub(page, { units: 403 });
  await page.goto(START);
  await expect(page.getByRole("combobox", { name: /Business Unit/ })).toHaveCount(0);
  await expect(page.getByText("North", { exact: true })).toBeVisible();
  for (const n of [1, 2, 3]) await expect(box(page, n)).toBeChecked();
  await expect(page.getByRole("checkbox")).toHaveCount(3);
  const lineUrls = state.lineRequests.filter(u => u.searchParams.get("q") === "REQ-00418");
  expect(lineUrls.every(u => u.searchParams.get("unit_id") === unitId)).toBe(true);
  expect(lineUrls.some(u => u.searchParams.get("cursor") === "p2")).toBe(true);
  await expect(qty(page, 2)).toHaveValue("5");
  await expect(qty(page, 3)).toHaveValue("2.5");
  await box(page, 3).uncheck();
  await qty(page, 1).fill("4");
  await page.getByLabel(/^Title/).fill("Pumps");
  await create(page).click();
  await expect(page).toHaveURL(`/sourcing/rfqs/${newRfqId}`);
  expect(state.posts).toHaveLength(1);
  expect(state.posts[0]!.body).toEqual({ title: "Pumps", lines: [{ requisition_line_id: id(1), requisition_version: 3, quantity: "4" }, { requisition_line_id: id(2), requisition_version: 3, quantity: "5" }] });
});

test("New RFQ with the units 403 and no unit shows guidance and loads no lines", async ({ page }) => {
  const state = await stub(page, { units: 403 });
  await page.goto("/sourcing/rfqs/new");
  await expect(page.getByText("Start an RFQ from an approved requisition. Open it and choose Start RFQ.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Go to Requisitions" })).toHaveAttribute("href", "/requisitions");
  expect(state.lineRequests).toHaveLength(0);
});

test("units 200: choose a unit, lines carry unit_id, changing the unit clears the selection", async ({ page }) => {
  const state = await stub(page);
  await page.goto("/sourcing/rfqs/new");
  expect(state.lineRequests).toHaveLength(0);
  await page.getByRole("combobox", { name: /Business Unit/ }).selectOption(unitId);
  await box(page, 4, "REQ-00500").check();
  await expect(box(page, 1, "REQ-00418")).toBeEnabled();
  await expect(box(page, 5, "REQ-00500")).toBeDisabled(); // EUR after USD
  await expect(page.getByText(/Different currency/)).toBeVisible();
  expect(state.lineRequests.every(u => u.searchParams.get("unit_id") === unitId)).toBe(true);
  await page.getByRole("combobox", { name: /Business Unit/ }).selectOption(otherUnit);
  await expect(page.getByText("No approved lines are open to source here.")).toBeVisible();
  await expect(page.getByRole("checkbox")).toHaveCount(0);
});

test("a 403 on the lines call shows the access state", async ({ page }) => {
  await stub(page, { units: 403, lines403: true });
  await page.goto(START);
  await expect(page.getByText("You cannot start RFQs here. Ask an administrator for the RFQ create permission.")).toBeVisible();
});

test("above open is refused at the field with the figure; Create stays enabled and sends nothing", async ({ page }) => {
  const state = await stub(page, { units: 403 });
  await page.goto(START);
  await expect(box(page, 1)).toBeChecked();
  await qty(page, 1).fill("11");
  await page.getByLabel(/^Title/).fill("Pumps");
  await expect(create(page)).toBeEnabled();
  await create(page).click();
  await expect(page.getByText("10 are open on REQ-00418. Amend the requisition to source more").first()).toBeVisible();
  await expect(qty(page, 1)).toHaveValue("11");
  await expect(create(page)).toBeEnabled();
  expect(state.posts).toHaveLength(0);
});

test("the button stays enabled with an invalid field and the summary names each problem", async ({ page }) => {
  await stub(page, { units: 403 });
  await page.goto(START);
  await expect(box(page, 1)).toBeChecked();
  await qty(page, 1).fill("abc");
  await expect(create(page)).toBeEnabled();
  await create(page).click();
  await expect(page.getByText("Enter a quantity like 1,234.5.").first()).toBeVisible();
  await expect(page.getByText("Enter a title for the RFQ.").first()).toBeVisible();
  await expect(create(page)).toBeEnabled();
});

test("a server 409 maps the new open figures to the fields and keeps the selection", async ({ page }) => {
  const state = await stub(page, { units: 403 });
  state.mode = "over";
  await page.goto(START);
  await expect(box(page, 1)).toBeChecked();
  await page.getByLabel(/^Title/).fill("Pumps");
  await create(page).click();
  await expect(page.getByText("3 are open on REQ-00418. Amend the requisition to source more").first()).toBeVisible();
  await expect(page.locator("tr", { hasText: "Part 1" }).locator("td[data-label='Open']")).toHaveText("3");
  for (const n of [1, 2, 3]) await expect(box(page, n)).toBeChecked();
  await expect(qty(page, 1)).toHaveValue("10");
});

test("a request already in flight elsewhere keeps the key and says so", async ({ page }) => {
  const state = await stub(page, { units: 403 });
  state.mode = "in_flight";
  await page.goto(START);
  await expect(box(page, 1)).toBeChecked();
  await page.getByLabel(/^Title/).fill("Pumps");
  await create(page).click();
  await expect(page.getByText("This RFQ is still being created. Wait a moment, then try again.").first()).toBeVisible();
  state.mode = "ok";
  await create(page).click();
  await expect(page).toHaveURL(`/sourcing/rfqs/${newRfqId}`);
  expect(state.posts[1]!.key).toBe(state.posts[0]!.key);
});

test("two quick clicks make exactly one POST", async ({ page }) => {
  const state = await stub(page, { units: 403 });
  state.mode = "hold";
  await page.goto(START);
  await expect(box(page, 1)).toBeChecked();
  await page.getByLabel(/^Title/).fill("Pumps");
  await create(page).click();
  await expect(page.getByRole("button", { name: "Creating RFQ Draft" })).toHaveAttribute("aria-busy", "true");
  await create(page).click();
  await page.waitForTimeout(300);
  expect(state.posts).toHaveLength(1);
  state.release();
  await expect(page).toHaveURL(`/sourcing/rfqs/${newRfqId}`);
  expect(state.posts).toHaveLength(1);
});

test("a retry after a lost response reuses the key; an edit gets a new one", async ({ page }) => {
  const state = await stub(page, { units: 403 });
  state.mode = "abort";
  await page.goto(START);
  await expect(box(page, 1)).toBeChecked();
  await page.getByLabel(/^Title/).fill("Pumps");
  await create(page).click();
  await expect(page.getByText(/Creating the RFQ failed/).first()).toBeVisible();
  await create(page).click();
  await expect.poll(() => state.posts.length).toBe(2);
  expect(state.posts[1]!.key).toBe(state.posts[0]!.key);
  await qty(page, 1).fill("6");
  await create(page).click();
  await expect.poll(() => state.posts.length).toBe(3);
  expect(state.posts[2]!.key).not.toBe(state.posts[0]!.key);
});

test.describe("German locale", () => {
  test.use({ locale: "de-DE" });
  test("quantities are typed and shown in the locale and sent canonical", async ({ page }) => {
    const state = await stub(page, { units: 403 });
    await page.goto(START);
    await expect(qty(page, 3)).toHaveValue("2,5");
    await box(page, 2).uncheck();
    await box(page, 3).uncheck();
    await qty(page, 1).fill("1.234,5"); // above open 10: refused
    await page.getByLabel(/^Title/).fill("Pumps");
    await create(page).click();
    await expect(page.getByText("10 are open on REQ-00418. Amend the requisition to source more").first()).toBeVisible();
    await qty(page, 1).fill("7,5");
    await create(page).click();
    await expect(page).toHaveURL(`/sourcing/rfqs/${newRfqId}`);
    expect(state.posts[0]!.body.lines[0]!.quantity).toBe("7.5");
  });
});

test("keyboard only: reach the checkboxes and quantity fields, create", async ({ page }) => {
  const state = await stub(page, { units: 403 });
  await page.goto(START);
  await expect(box(page, 1)).toBeChecked();
  await page.getByLabel(/^Title/).focus();
  await page.keyboard.type("Pumps");
  await box(page, 2).focus();
  await page.keyboard.press("Space");
  await expect(box(page, 2)).not.toBeChecked();
  await page.keyboard.press("Space");
  await expect(box(page, 2)).toBeChecked();
  await page.keyboard.press("Tab");
  await expect(qty(page, 2)).toBeFocused();
  await create(page).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(`/sourcing/rfqs/${newRfqId}`);
  expect(state.posts).toHaveLength(1);
});

for (const theme of ["light", "dark"] as const) {
  test(`/sourcing/rfqs/new passes axe in ${theme} theme`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await stub(page, { units: 403 });
    await page.goto(START);
    await page.locator(".app-shell").waitFor();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(box(page, 1)).toBeChecked();
    await qty(page, 1).fill("11");
    await create(page).click(); // the error state is checked too
    await expect(page.getByText("10 are open on REQ-00418. Amend the requisition to source more").first()).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
