import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const ID = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const VENDOR = ID(7), UNIT = ID(9);
const NOW = "2026-10-20T09:00:00Z";

/** An in-memory stand-in for the E11 API: the status never changes by itself, only by a decided change. */
async function stub(page: Page, seed: { status: "active" | "suspended" | "pending_approval"; pendingCreate?: boolean; allow?: boolean; disabled?: boolean; viewOnly?: boolean; unitsDenied?: boolean; unitsDelay?: Promise<void>; noUnits?: boolean }) {
  const state = { status: seed.status, disabled: seed.disabled ?? false, changes: (seed.pendingCreate ? [{ id: ID(49), vendor_id: VENDOR, kind: "create", payload: {}, reason: null, status: "pending", requested_by_email: "me@x.test", requested_by_me: true, requested_at: NOW, decided_at: null }] : []) as unknown[], writes: [] as { path: string; body: unknown }[], fail: null as { status: number; detail: string } | null };
  const detail = () => ({ id: VENDOR, number: "VEN-00007", trading_name: "Acme Ltd", legal_name: "Acme Limited", status: state.status, availability: "global", created_by_email: "a@x.test", created_at: NOW, updated_at: NOW, version: 1, units: [], identifiers: {},
    actions: seed.viewOnly ? [] : ["edit", "request_change"], local_disable_units: seed.viewOnly || seed.allow === false ? [] : [UNIT],
    disabled_in: state.disabled ? [{ unit_id: UNIT, unit_code: "P4", by_email: "a@x.test", at: NOW, reason: "Quality" }] : [] });
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    const send = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/auth/sessions" || path === "/api/v1/master/currency") return send([]);
    if (path === "/api/v1/auth/organizations") return send({ items: [], current_org_id: ID(1) });
    if (method !== "GET") {
      state.writes.push({ path, body: req.postDataJSON() });
      if (state.fail) return send({ detail: state.fail.detail, correlation_id: "c1" }, state.fail.status);
      if (path.endsWith("/changes")) {
        const b = req.postDataJSON();
        state.changes.push({ id: ID(50), vendor_id: VENDOR, kind: b.kind, payload: b.payload ?? {}, reason: b.reason, status: "pending", requested_by_email: "me@x.test", requested_by_me: true, requested_at: NOW, decided_at: null });
        return send(state.changes.at(-1), 201);
      }
      state.disabled = path.endsWith("/disable");
      return send(detail());
    }
    if (path === `/api/v1/vendors/${VENDOR}`) return send(detail());
    if (path === "/api/v1/org/units" && seed.unitsDenied && url.searchParams.get("page_size") === "100") return send({ detail: "You do not have permission to do that." }, 403);
    if (path === "/api/v1/org/units" && url.searchParams.get("page_size") === "100") {
      await seed.unitsDelay;
      if (seed.noUnits) return send({ rows: [], next_cursor: null });
    }
    if (path === "/api/v1/org/units") return send({ rows: [{ id: UNIT, code: "P4", name: "Plant 4", kind: "ou", active: true, parent_id: null, created_at: NOW }], next_cursor: null });
    if (path.endsWith("/eligibility")) return send({ eligible: !state.disabled, reasons: state.disabled ? [{ code: "disabled", text: "Disabled in Plant 4" }] : [], warnings: [] });
    if (path.endsWith("/changes")) return send(state.changes);
    if (path.endsWith("/performance")) return send({ categories: [], count: 0, overall: null });
    if (path.endsWith("/scores")) return send({ rows: [], next_cursor: null });
    if (path.endsWith("/onboarding")) return send(null, 404);
    return send([]);
  });
  return state;
}

const open = async (page: Page) => {
  await page.goto(`/vendors/${VENDOR}`);
  await page.getByRole("heading", { name: "Acme Ltd", level: 2 }).waitFor();
};

const CASES = [
  { name: "Suspend", seed: { status: "active" as const }, confirm: "Request Suspension", note: /Suspension requested by you, awaiting approval/, reason: true },
  { name: "Reactivate", seed: { status: "suspended" as const }, confirm: "Request Reactivation", note: /Reactivation requested by you, awaiting approval/, reason: true },
  { name: "Change Availability", seed: { status: "active" as const }, confirm: "Request Change", note: /Availability change requested by you, awaiting approval/, reason: true },
];

for (const c of CASES) {
  test(`${c.name}: safe button focused, empty reason refused, request leaves the status unchanged`, async ({ page }) => {
    const state = await stub(page, c.seed);
    await open(page);
    const pill = page.locator(".vendor-head").getByText(c.seed.status === "active" ? "Active" : "Suspended", { exact: true });
    await expect(pill).toBeVisible();
    await page.getByRole("button", { name: c.name, exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
    await dialog.getByRole("button", { name: c.confirm }).click();
    await expect(dialog.getByText("Give a reason.")).toBeVisible();
    expect(state.writes).toHaveLength(0);
    await dialog.getByLabel("Reason (required)").fill("Audit finding");
    await dialog.getByRole("button", { name: c.confirm }).click();
    await expect(page.getByRole("status").filter({ hasText: c.note })).toBeVisible();
    await expect(dialog).toHaveCount(0);
    await expect(pill).toBeVisible(); // the pill waits for the decision
    expect(state.status).toBe(c.seed.status);
  });
}

test("Disable Here takes effect at once and the eligibility header updates", async ({ page }) => {
  const state = await stub(page, { status: "active" });
  await open(page);
  await expect(page.getByText("Eligible In Plant 4")).toBeVisible();
  await page.getByRole("button", { name: "Disable Here", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await dialog.getByRole("button", { name: "Disable in Plant 4" }).click();
  await expect(dialog.getByText("Give a reason.")).toBeVisible();
  await dialog.getByLabel("Reason (required)").fill("Quality");
  await dialog.getByRole("button", { name: "Disable in Plant 4" }).click();
  await expect(page.getByText("Not Eligible In Plant 4")).toBeVisible();
  expect(state.writes.at(-1)).toMatchObject({ path: `/api/v1/vendors/${VENDOR}/units/${UNIT}/disable`, body: { reason: "Quality" } });
});

test("Enable Here needs no reason and takes effect at once", async ({ page }) => {
  const state = await stub(page, { status: "active", disabled: true });
  await open(page);
  await page.getByRole("button", { name: "Enable Here", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await expect(dialog.getByLabel("Reason (required)")).toHaveCount(0);
  await dialog.getByRole("button", { name: "Enable in Plant 4" }).click();
  await expect(page.getByText("Eligible In Plant 4")).toBeVisible();
  expect(state.disabled).toBe(false);
});

test("Disable Here is absent when local disable is not allowed", async ({ page }) => {
  await stub(page, { status: "active", allow: false });
  await open(page);
  await expect(page.getByRole("button", { name: "Suspend", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Disable Here" })).toHaveCount(0);
});

test("Enable Here is absent when local disable is not allowed", async ({ page }) => {
  await stub(page, { status: "active", allow: false, disabled: true });
  await open(page);
  await expect(page.getByRole("button", { name: "Suspend", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Enable Here" })).toHaveCount(0);
});

test("a view-only user sees no action controls", async ({ page }) => {
  await stub(page, { status: "active", disabled: true, viewOnly: true });
  await open(page);
  await expect(page.getByText("Not Eligible In Plant 4")).toBeVisible();
  for (const name of ["Suspend", "Reactivate", "Change Availability", "Disable Here", "Enable Here"]) await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
});

test("Change Availability shows a failed unit list in words, not an empty one", async ({ page }) => {
  await stub(page, { status: "active", unitsDenied: true });
  await open(page);
  await page.getByRole("button", { name: "Change Availability", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Selected Units").check();
  await expect(dialog.getByRole("alert")).toContainText("permission");
  await expect(dialog.getByRole("button", { name: "Try again" })).toBeVisible();
});

test("Change Availability announces loading, blocks submit until it settles, and clears the alert on retry", async ({ page }) => {
  let release!: () => void;
  const unitsDelay = new Promise<void>(r => { release = r; });
  await stub(page, { status: "active", unitsDelay });
  await open(page);
  await page.getByRole("button", { name: "Change Availability", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Reason (required)").fill("Scope");
  await dialog.getByLabel("Selected Units").check();
  await expect(dialog.getByRole("status").filter({ hasText: "Loading units" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Request Change" })).toBeDisabled();
  release();
  await expect(dialog.getByLabel("Plant 4")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Request Change" })).toBeEnabled();
});

test("Change Availability retry hides the stale alert while loading, then recovers", async ({ page }) => {
  let release!: () => void;
  const unitsDelay = new Promise<void>(r => { release = r; });
  const seed: Parameters<typeof stub>[1] = { status: "active", unitsDenied: true };
  await stub(page, seed);
  await open(page);
  await page.getByRole("button", { name: "Change Availability", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Selected Units").check();
  await expect(dialog.getByRole("alert")).toContainText("permission");
  await expect(dialog.getByRole("button", { name: "Request Change" })).toBeDisabled();
  seed.unitsDenied = false; seed.unitsDelay = unitsDelay;
  await dialog.getByRole("button", { name: "Try again" }).click();
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  await expect(dialog.getByRole("status").filter({ hasText: "Loading units" })).toBeVisible();
  release();
  await expect(dialog.getByLabel("Plant 4")).toBeVisible();
});

test("Change Availability says so when there are no active units", async ({ page }) => {
  await stub(page, { status: "active", noUnits: true });
  await open(page);
  await page.getByRole("button", { name: "Change Availability", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Selected Units").check();
  await expect(dialog.getByRole("status").filter({ hasText: "no active units" })).toBeVisible();
});

test("a new vendor's pending create shows the approval notice and no availability note", async ({ page }) => {
  await stub(page, { status: "pending_approval", pendingCreate: true });
  await open(page);
  await expect(page.getByText("This vendor is waiting for approval")).toBeVisible();
  await expect(page.getByText(/requested by .*awaiting approval/)).toHaveCount(0);
});

test("the Suspend trigger carries its icon beside the verb", async ({ page }) => {
  await stub(page, { status: "active" });
  await open(page);
  await expect(page.getByRole("button", { name: "Suspend", exact: true }).locator("svg")).toHaveCount(1);
});

test("a refusal is shown in the dialog in words and the reason is kept", async ({ page }) => {
  const state = await stub(page, { status: "active" });
  await open(page);
  await page.getByRole("button", { name: "Suspend", exact: true }).click();
  state.fail = { status: 409, detail: "A change for this vendor is already waiting for approval." };
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Reason (required)").fill("Audit finding");
  await dialog.getByRole("button", { name: "Request Suspension" }).click();
  await expect(dialog.getByRole("alert")).toContainText("already waiting for approval");
  await expect(dialog.getByLabel("Reason (required)")).toHaveValue("Audit finding");
});

test("the danger fill is on the Suspend confirm button only", async ({ page }) => {
  await stub(page, { status: "active" });
  await open(page);
  const danger = await page.evaluate(() => { const p = document.createElement("i"); p.style.color = "var(--status-danger)"; document.body.append(p); const c = getComputedStyle(p).color; p.remove(); return c; });
  const fill = (loc: ReturnType<Page["locator"]>) => loc.evaluate(el => getComputedStyle(el).backgroundColor);
  await expect.poll(() => fill(page.getByRole("button", { name: "Suspend", exact: true }))).not.toBe(danger);
  await page.getByRole("button", { name: "Suspend", exact: true }).click();
  const dialog = page.getByRole("dialog");
  expect(await fill(dialog.getByRole("button", { name: "Request Suspension" }))).toBe(danger);
  expect(await fill(dialog.getByRole("button", { name: "Cancel" }))).not.toBe(danger);
});

test("keyboard only: open, trap focus, Escape returns focus to the opener", async ({ page }) => {
  await stub(page, { status: "active" });
  await open(page);
  const opener = page.getByRole("button", { name: "Suspend", exact: true });
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  for (let i = 0; i < 5; i++) { await page.keyboard.press("Tab"); expect(await dialog.evaluate(d => d.contains(document.activeElement))).toBe(true); }
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
});

for (const theme of ["light", "dark"] as const) {
  for (const name of ["Suspend", "Reactivate", "Change Availability", "Disable Here", "Enable Here"]) {
    test(`${name} dialog passes axe in ${theme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await stub(page, { status: name === "Reactivate" ? "suspended" : "active", disabled: name === "Enable Here" });
      await open(page);
      await page.getByRole("button", { name, exact: true }).click();
      await page.getByRole("dialog").waitFor();
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });
  }
}
