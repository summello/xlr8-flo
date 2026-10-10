import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { projectId, unitId } from "./projects-fixture";
import { formatMoney } from "../../src/lib/money";
import { childId, stubDashboard } from "./dashboard-fixture";

test("seven KPIs, keyboard tree expansion and collapse, filtered ledger and pagination", async ({ page }) => {
  const fixture = await stubDashboard(page);
  await page.goto(`/projects/${projectId}/dashboard`);
  await expect(page.locator(".dashboard-kpi")).toHaveCount(7);
  await expect(page.getByText("Reconciles to the ledger", { exact: true })).toBeVisible();
  const tree = page.getByRole("treegrid"); const root = tree.getByRole("row").first();
  // Reach the roving row solely by Tab.
  for (let i = 0; i < 100 && !(await root.evaluate(n => document.activeElement === n)); i++) await page.keyboard.press("Tab");
  await expect(root).toBeFocused();
  await page.keyboard.press("ArrowRight"); await expect(tree.getByRole("row")).toHaveCount(2);
  await page.keyboard.press("ArrowLeft"); await expect(tree.getByRole("row")).toHaveCount(1);
  await page.keyboard.press("ArrowRight"); await page.keyboard.press("ArrowDown");
  await expect(tree.getByRole("row").last()).toBeFocused();
  await page.keyboard.press("Enter"); await expect(page).toHaveURL(new RegExp(`${childId}/dashboard`));
  await page.goto(`/projects/${projectId}/dashboard`);
  const reservation = page.locator('.dashboard-kpi[href*="bucket=reserved"]');
  for (let i = 0; i < 100 && !(await reservation.evaluate(n => document.activeElement === n)); i++) await page.keyboard.press("Tab");
  await expect(reservation).toBeFocused(); await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Ledger Entries: Reserved" })).toBeVisible();
  expect(fixture.state.ledgerRequests.some(query => query.get("bucket") === "reserved")).toBe(true);
  await page.getByRole("button", { name: "Next ledger page" }).click();
  await expect(page.getByRole("button", { name: "Next ledger page" })).toHaveCount(0);
  expect(fixture.state.ledgerRequests.at(-1)?.get("cursor")).toBe("page-2");
});

test("chart values equal their accessible data table", async ({ page }) => {
  await stubDashboard(page); await page.goto(`/projects/${projectId}/dashboard`);
  await expect(page.getByRole("img", { name: "Child Project Budgets" })).toBeVisible();
  const bars = await page.locator(".chart-value").evaluateAll(nodes => nodes.map(node => ({ amount: node.getAttribute("data-value"), currency: node.getAttribute("data-currency") })));
  const table = await page.locator(".chart-table-value").evaluateAll(nodes => nodes.map(node => ({ amount: node.getAttribute("data-value"), currency: node.getAttribute("data-currency") })));
  expect(bars).toEqual(table); expect(bars).toHaveLength(5);
  const rendered = await page.locator(".chart-value").allTextContents();
  expect(rendered.map(text => text.replaceAll(/\s/g, ""))).toEqual(table.map(row => `${formatMoney(row.amount!, row.currency!)}${row.currency}`));
  await page.getByRole("button", { name: "reserved", exact: true }).click();
  await expect(page.getByRole("button", { name: "reserved", exact: true })).toHaveAttribute("aria-pressed", "false");
});

test("planted drift is stated in words with the nonzero difference", async ({ page }) => {
  await stubDashboard(page, true); await page.goto(`/projects/${projectId}/dashboard`);
  await expect(page.getByText("Does not reconcile to the ledger.")).toBeVisible();
  await expect(page.getByText("Allocated difference:")).toContainText("12.35");
});

for (const theme of ["light", "dark"]) for (const route of [`/projects/${projectId}/dashboard`, "/budget"]) {
  test(`${route} passes axe in ${theme} and preserves reduced motion`, async ({ page }) => {
    await stubDashboard(page); await page.addInitScript(theme => localStorage.setItem("xlr8flo.theme", theme), theme);
    await page.emulateMedia({ reducedMotion: "reduce" }); await page.goto(route);
    await expect(page.locator(".dashboard-kpi").first()).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
for (const width of [375, 768, 1024, 1440]) test(`dashboard responsive ${width}`, async ({ page }, testInfo) => {
  await stubDashboard(page); await page.setViewportSize({ width, height: 900 }); await page.goto(`/projects/${projectId}/dashboard`);
  await expect(page.locator(".dashboard-kpi")).toHaveCount(7);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  if (width < 1024) {
    const sizes = await page.locator(".dashboard button, .dashboard a").evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
    expect(sizes.every(height => height >= 43.99)).toBe(true);
  }
  await page.screenshot({ path: testInfo.outputPath("dashboard.png"), fullPage: true });
});

test("empty and partial dashboards retain recovery controls", async ({ page }) => {
  const fixture = await stubDashboard(page); fixture.state.empty = true;
  await page.goto(`/projects/${projectId}/dashboard`);
  await expect(page.getByText("No budget allocated yet.")).toBeVisible();
  await expect(page.getByText("No child project budgets to compare.")).toBeVisible();
  fixture.state.fail = true; await page.reload();
  await expect(page.getByText("Dashboard partially available.")).toBeVisible();
  await expect(page.locator(".dashboard-kpi")).toHaveCount(7);
  fixture.state.fail = false; await page.getByRole("button", { name: "Retry dashboard" }).click();
  await expect(page.getByRole("treegrid")).toBeVisible();
});


test("large hierarchies virtualize while End and Home remain keyboard reachable", async ({ page }) => {
  const fixture = await stubDashboard(page); fixture.state.manyNodes = true;
  await page.goto(`/projects/${projectId}/dashboard`);
  const tree = page.getByRole("treegrid");
  await tree.getByRole("row").first().focus(); await page.keyboard.press("ArrowRight");
  await expect(tree).toHaveAttribute("aria-rowcount", "101");
  expect(await tree.getByRole("row").count()).toBeLessThan(101);
  await page.keyboard.press("End");
  await expect(tree.getByRole("row").filter({ hasText: "Child 99" })).toBeFocused();
  await page.keyboard.press("Home"); await expect(tree.getByRole("row").first()).toBeFocused();
  await page.keyboard.press("ArrowLeft"); await expect(tree.getByRole("row")).toHaveCount(1);
});


test("loading reserves layout while delayed figures arrive", async ({ page }) => {
  const fixture = await stubDashboard(page); fixture.state.delay = 500;
  await page.addInitScript(() => {
    const state = window as unknown as { dashboardShift: number };
    state.dashboardShift = 0;
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) {
        const shift = entry as PerformanceEntry & { value: number; hadRecentInput: boolean };
        if (!shift.hadRecentInput) state.dashboardShift += shift.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
  await page.goto(`/projects/${projectId}/dashboard`);
  await expect(page.locator(".dashboard-kpi-skeleton")).toHaveCount(7);
  await expect(page.getByRole("img", { name: "Child Project Budgets" })).toBeVisible();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(await page.evaluate(() => (window as unknown as { dashboardShift: number }).dashboardShift)).toBeLessThan(0.1);
});


test("budget keeps currencies separate and drills through the selected unit to projects", async ({ page }) => {
  const fixture = await stubDashboard(page);
  await page.goto("/budget");
  await expect(page.getByRole("region", { name: "USD budget", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "EUR budget", exact: true })).toBeVisible();
  await page.getByLabel("Business / operating unit").selectOption(unitId);
  await expect(page).toHaveURL(new RegExp(`/budget\\?unit_id=${unitId}`));
  await expect.poll(() => fixture.state.summaryRequests.at(-1)?.get("unit_id")).toBe(unitId);
  await page.getByRole("link", { name: "Drill down to projects" }).click();
  await expect(page).toHaveURL(new RegExp(`/projects\\?bu_id=${unitId}`));
});
