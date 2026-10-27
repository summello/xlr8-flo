import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { keyboardFocus } from "./helpers";
import { comparison, rfqId, stubComparison } from "./comparison-fixture";

const url = `/sourcing/rfqs/${rfqId}/comparison`;
const MONEY = /\d[\d,]*\.\d{2}\b/;
test.use({ viewport: { width: 1600, height: 1000 } });

test("every visible figure is the server's: a deliberately inconsistent total renders as sent", async ({ page }) => {
  const body = comparison();
  (body.vendors[1] as Record<string, unknown>).landed_total = "1.0000"; // price 85,000 + tax 8,500 + freight 3,000 is not 1.00; the page must not repair it
  await stubComparison(page, { body });
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "RFQ-0001 · Centrifugal pumps" })).toBeVisible();
  const total = page.getByRole("row", { name: /^Landed Total/ });
  await expect(total).toContainText("103,000.00");
  await expect(total.getByRole("cell").nth(1)).toContainText("1.00");
  await expect(total.getByRole("cell").nth(1)).not.toContainText("96,500.00");
  await expect(page.getByText(/Award draft: Lowest Cost Supplier · 2 vendors · 93,000\.00/)).toBeVisible();
  await expect(page.getByText(/Within approved scope,\s*107,000\.00\s*USD\s*to spare/)).toBeVisible();
  await page.getByRole("radio", { name: /Best Average Cost/ }).check();
  await expect(page.getByText(/Award draft: Best Average Cost · 1 vendor · 96,500\.00/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Start Award" })).toHaveCount(0); // joins when E12-S16 exists
});

test("matrix: markers in words, Show Only Differences says how many it hides, partial and late are marked", async ({ page }) => {
  await stubComparison(page);
  await page.goto(url);
  const matrix = page.locator(".cmp-matrix");
  await expect(matrix.getByRole("row", { name: /^Landed Total/ })).toContainText("Lowest");
  await expect(matrix.getByRole("row", { name: /^Lead Time/ })).toContainText("Fastest");
  await expect(matrix.getByRole("row", { name: /^Weighted Score/ })).toContainText("Highest Score");
  await expect(matrix.getByRole("row", { name: /^Difference From Lowest/ })).toContainText("Not comparable");
  await expect(matrix.getByText("Partial").first()).toBeVisible();
  await expect(matrix.getByText("Late, Accepted By Override").first()).toBeVisible();
  await expect(matrix.getByText("1 identical row hidden")).toBeVisible();
  await matrix.getByLabel("Show Only Differences").uncheck();
  await expect(matrix.getByText("identical row hidden")).toHaveCount(0);
  await matrix.getByRole("button", { name: /Terms/ }).click();
  await expect(matrix.getByRole("row", { name: /^Exceptions/ })).toBeVisible();
  const group = matrix.getByRole("button", { name: /Price, Tax and Freight/ });
  await expect(group).toHaveAttribute("aria-expanded", "false");
  await group.click();
  await expect(group).toHaveAttribute("aria-expanded", "true");
  await expect(matrix.getByRole("row", { name: /^Freight/ })).toBeVisible();
});

test("sealed: no figure on the page, only how many bids were received", async ({ page }) => {
  await stubComparison(page, { body: { sealed: true } });
  await page.goto(url);
  await expect(page.getByText("3 Received, sealed")).toBeVisible();
  await expect(page.locator(".cmp-table, .cmp-verdicts, .cmp-award, .cmp-basis")).toHaveCount(0);
  expect(await page.locator("main").innerText()).not.toMatch(MONEY);
});

test("no bids received offers Extend Deadline and Edit Invitations", async ({ page }) => {
  await stubComparison(page, { body: comparison({ vendors: [] }), bidsReceived: 0 });
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "No Bids Received" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Extend Deadline/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "Edit Invitations" })).toBeVisible();
});

test("Chart and Table tabs hold the same values (A11Y-009)", async ({ page }) => {
  await stubComparison(page);
  await page.goto(url);
  await page.getByRole("tab", { name: "Cost Breakdown" }).click();
  const chart = await page.locator(".cmp-bar-total").allInnerTexts();
  expect(chart).toHaveLength(3);
  await expect(page.getByRole("img", { name: /Bolt Supply: price 85000.0000, tax and duty 8500.0000, freight 3000.0000, total 96500.0000 USD/ })).toBeVisible();
  await page.getByRole("tab", { name: "Table" }).click();
  const table = page.getByRole("table", { name: /Cost breakdown/ });
  const totals = await table.locator("tbody tr").evaluateAll(rows => rows.map(r => (r.lastElementChild as HTMLElement).innerText));
  expect(totals.map(t => t.replace(/\s/g, ""))).toEqual(chart.map(t => t.replace(/\s/g, "")));
  await expect(table.getByRole("row", { name: /^Bolt Supply/ })).toContainText("85,000.00");
});

test("By Line adds up to the basis total; Scoring shows the server's weighted score and declarations only to editors", async ({ page }) => {
  await stubComparison(page);
  await page.goto(url);
  await page.getByRole("tab", { name: "By Line" }).click();
  await expect(page.getByRole("row", { name: /^In This Selection/ })).toContainText("53,000.00");
  await expect(page.getByRole("row", { name: /^1\. Centrifugal pump/ })).toContainText("Lowest");
  await expect(page.getByRole("row", { name: /^2\. Spare seal kit/ })).toContainText("Not quoted");
  // Unit cost is the server's converted figure, shown as stubbed: the stub is deliberately inconsistent with landed / quantity (58,000 / 4 = 14,500), so a page that divides or omits it fails.
  const pump = page.getByRole("row", { name: /^1\. Centrifugal pump/ });
  await expect(pump.getByRole("cell").nth(1)).toContainText(/Unit Cost\s*12,345\.67\s*USD/);
  await expect(pump).not.toContainText("14,500.00");
  await expect(pump).not.toContainText("EUR"); // the keyed bid-currency unit price is the bid sheet's, not By Line's
  await expect(page.getByRole("row", { name: /^2\. Spare seal kit/ }).getByRole("cell").nth(2)).not.toContainText("Unit Cost"); // not quoted: no unit cost
  await page.getByRole("tab", { name: "Scoring" }).click();
  await expect(page.getByRole("row", { name: /^Acme Tooling/ })).toContainText("82.5000");
  const quality = page.getByRole("row", { name: /^Quality/ }).last();
  await expect(quality.getByRole("cell").nth(0)).toHaveText("8, 9"); // the server's entries, not an average
  await expect(quality.getByRole("cell").nth(1)).toHaveText("Not available");
  await expect(page.getByText("Eve Evaluator: No conflict")).toBeVisible();
});

test("scoring without declarations or scores: sections are omitted or read Not available", async ({ page }) => {
  await stubComparison(page, { canEdit: false, scored: false });
  await page.goto(url);
  await page.getByRole("tab", { name: "Scoring" }).click();
  await expect(page.getByRole("row", { name: /^Acme Tooling/ })).toContainText("Not available");
  await expect(page.getByRole("heading", { name: "Evaluators" })).toHaveCount(0);
});

test("stale rate: Refresh Rate is for editors and posts with an Idempotency-Key", async ({ page }) => {
  const state = await stubComparison(page, { body: comparison({ stale_rate: true }) });
  await page.goto(url);
  await page.getByRole("button", { name: "Refresh Rate" }).click();
  await expect(page.getByRole("button", { name: "Refresh Rate" })).toHaveCount(0);
  expect(state.refreshes).toHaveLength(1);
  expect(state.refreshes[0]!.key).toBeTruthy();
});

test("stale rate hides Refresh Rate from a user without rfq.edit", async ({ page }) => {
  await stubComparison(page, { body: comparison({ stale_rate: true }), canEdit: false });
  await page.goto(url);
  await expect(page.getByText(/older than 7 days/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh Rate" })).toHaveCount(0);
});

test("over scope: the scope pill says by how much, in words", async ({ page }) => {
  await stubComparison(page, { body: comparison({ lowest_cost_supplier: { ...comparison().lowest_cost_supplier, within_scope: false, over_by: "3012.1300", to_spare: null } }) });
  await page.goto(url);
  await expect(page.getByText(/Exceeds approved scope by\s*3,012\.13\s*USD/)).toBeVisible();
});

for (const width of [768, 375]) {
  test(`narrow at ${width}: two vendors and a picker that cannot choose one twice`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await stubComparison(page);
    await page.goto(url);
    const first = page.getByLabel("First Vendor"), second = page.getByLabel("Second Vendor");
    await expect(first).toBeVisible();
    await expect(page.locator(".cmp-table thead th")).toHaveCount(3); // measure + two vendors
    await expect(second.locator("option", { hasText: "Acme Tooling" })).toHaveCount(0); // the first pick is not offered again
    await second.selectOption({ label: "Cirrus Works" });
    await expect(first.locator("option", { hasText: "Cirrus Works" })).toHaveCount(0);
    await expect(page.locator(".cmp-table thead").getByRole("button", { name: "Cirrus Works" })).toBeVisible();
    if (width === 375) expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

test("bid sheet: opens on Close, keeps Tab inside, Escape returns focus to the header button; terms come from the bid read", async ({ page }) => {
  await stubComparison(page);
  await page.goto(url);
  const opener = page.locator(".cmp-table thead").getByRole("button", { name: "Bolt Supply" });
  await opener.click();
  const sheet = page.getByRole("dialog", { name: "Bolt Supply" });
  await expect(sheet.getByRole("button", { name: "Close" })).toBeFocused();
  await expect(sheet).toContainText("Converted from EUR at 1.0850 (Central bank, effective");
  await expect(sheet).toContainText("DAP Kuwait");
  await expect(sheet.getByText("ISO 9001 certified: Met")).toBeVisible();
  await expect(sheet.getByText("Two year warranty: Not Met, Certificate pending")).toBeVisible();
  for (let i = 0; i < 12; i++) { await page.keyboard.press("Tab"); expect(await sheet.evaluate(n => n.contains(document.activeElement))).toBe(true); }
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test("bid sheet omits the keyed terms when the bid read is refused", async ({ page }) => {
  await stubComparison(page, { bidRefused: true });
  await page.goto(url);
  await page.locator(".cmp-table thead").getByRole("button", { name: "Bolt Supply" }).click();
  const sheet = page.getByRole("dialog", { name: "Bolt Supply" });
  await expect(sheet).toContainText("96,500.00");
  await expect(sheet.getByText("Delivery terms")).toHaveCount(0);
  await expect(sheet.getByRole("heading", { name: "Requirements" })).toHaveCount(0);
  await expect(sheet.getByRole("heading", { name: "Exceptions" })).toHaveCount(0);
});

test("keyboard-only path: tabs by arrows, a closed group by Enter, the sheet by Enter and Escape", async ({ page }) => {
  await stubComparison(page);
  await page.goto(url);
  const overview = page.getByRole("tab", { name: "Overview" });
  await keyboardFocus(page, overview);
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "By Line", selected: true })).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "Scoring", selected: true })).toBeFocused();
  await page.keyboard.press("Home");
  await expect(overview).toBeFocused();
  const group = page.locator(".cmp-matrix").getByRole("button", { name: /Price, Tax and Freight/ });
  await keyboardFocus(page, group);
  await page.keyboard.press("Enter");
  await expect(group).toHaveAttribute("aria-expanded", "true");
  const opener = page.locator(".cmp-table thead").getByRole("button", { name: "Acme Tooling" });
  await keyboardFocus(page, opener);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Acme Tooling" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(opener).toBeFocused();
});

test("reduced motion: the bars do not animate", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await stubComparison(page);
  await page.goto(url);
  await page.getByRole("tab", { name: "Cost Breakdown" }).click();
  expect(await page.locator(".cmp-seg").first().evaluate(n => getComputedStyle(n).transitionDuration)).toMatch(/^0s$/);
});

for (const theme of ["light", "dark"] as const) {
  test(`axe: comparison tabs, matrix and sheet in the ${theme} theme`, async ({ page }) => {
    test.setTimeout(120_000); // five axe scans
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await stubComparison(page, { body: comparison({ stale_rate: true }) });
    await page.goto(url);
    await expect(page.locator(".cmp-table")).toBeVisible();
    await page.locator(".cmp-matrix").getByRole("button", { name: /Price, Tax and Freight/ }).click();
    const scan = async () => expect((await new AxeBuilder({ page }).analyze()).violations.map(v => `${v.id}: ${v.nodes.map(n => JSON.stringify(n.target)).join(" ")}`)).toEqual([]);
    await scan();
    for (const tab of ["By Line", "Cost Breakdown", "Scoring"]) { await page.getByRole("tab", { name: tab, exact: true }).click(); await expect(page.locator("#cmp-panel")).toBeVisible(); await scan(); }
    await page.getByRole("tab", { name: "Overview" }).click();
    await page.locator(".cmp-table thead").getByRole("button", { name: "Bolt Supply" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await scan();
  });
}

test("plant guard: the page never shows a price before the bids open", async ({ page }) => {
  await stubComparison(page, { body: { sealed: true } });
  await page.goto(url);
  await expect(page.getByText("3 Received, sealed")).toBeVisible();
  expect(await page.locator("body").innerText()).not.toMatch(MONEY);
});
