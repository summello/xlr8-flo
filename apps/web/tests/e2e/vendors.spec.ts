import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { stubVendors, vendorId } from "./vendors-fixture";

test("search, switch tabs, open a vendor, open a document and verify it", async ({ page }) => {
  const state = await stubVendors(page);
  await page.goto("/vendors");
  await expect(page.getByRole("link", { name: "Acme Tooling" })).toBeVisible();
  await expect(page.getByRole("tab", { name: /^Needs Attention/ })).toContainText("7");
  await page.getByLabel("Search", { exact: true }).fill("borealis");
  await page.locator(".vendor-list form").getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("link", { name: "Borealis Steel" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Acme Tooling" })).toHaveCount(0);
  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByRole("tab", { name: /^Needs Attention/ }).click();
  await expect(page.getByRole("link", { name: "Borealis Steel" })).toHaveCount(0);
  await page.getByRole("link", { name: "Acme Tooling" }).click();
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  await page.getByRole("button", { name: "Open INS" }).click();
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.verified).toBe(true);
});

test("paging is server-side: limit and cursor are sent and no request asks for everything", async ({ page }) => {
  const state = await stubVendors(page);
  await page.goto("/vendors?tab=all");
  await expect(page.getByRole("link", { name: "Acme Tooling" })).toBeVisible();
  expect(state.requests.length).toBeGreaterThan(0);
  for (const url of state.requests) expect(Number(url.searchParams.get("limit"))).toBeLessThanOrEqual(100);
  expect(state.requests.every(u => u.searchParams.has("limit"))).toBe(true);
  // the counts come from the server: the Needs Attention tab shows the stub's 7, not the 2 attention rows that happen to be loaded
  await expect(page.getByRole("tab", { name: /^Needs Attention/ })).toContainText("7");
});

test("the documents signal looks ahead, says it in words and the status pill keeps its icon", async ({ page }) => {
  await stubVendors(page);
  await page.goto("/vendors");
  const row = page.locator("tr[data-grid-row]").filter({ hasText: "Acme Tooling" });
  await expect(row.getByText("1 Expiring in 12 Days")).toBeVisible();
  await expect(row.getByText("1 Expired")).toBeVisible();
  await expect(row.getByText("Not Eligible")).toBeVisible();
  await expect(row.locator(".status-pill")).toContainText("Active");
  await expect(row.locator(".status-pill svg")).toHaveCount(1); // icon and words: colour never carries status alone
  await expect(page.locator("tr[data-grid-row]").filter({ hasText: "Cobalt Freight" }).getByText("1 Missing")).toBeVisible();
});

test("keyboard only: tabs by arrows, row to profile, document sheet traps focus and returns it on Escape", async ({ page }) => {
  await stubVendors(page);
  await page.goto("/vendors");
  const active = page.getByRole("tab", { name: /^Active/ });
  await active.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /^Pending Approval/ })).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: /^All/ })).toBeFocused();
  await page.keyboard.press("Home");
  await expect(active).toBeFocused();
  await page.goto(`/vendors/${vendorId}`);
  const open = page.getByRole("button", { name: "Open INS" });
  await open.focus();
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog");
  await expect(sheet).toBeVisible();
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press("Tab");
    expect(await sheet.evaluate(node => node.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await expect(open).toBeFocused();
});

test("a missing document opens a sheet that says what it blocks; a failing card leaves the others", async ({ page }) => {
  await stubVendors(page);
  await page.route(`**/api/v1/vendors/${vendorId}/contacts`, route => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Contacts are unavailable." }) }));
  await page.goto(`/vendors/${vendorId}`);
  await expect(page.getByRole("alert").filter({ hasText: "Contacts" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  await page.getByRole("button", { name: "Open TAX" }).click();
  await expect(page.getByRole("dialog")).toContainText("not eligible");
});

for (const theme of ["light", "dark"] as const) {
  for (const route of ["/vendors", `/vendors/${vendorId}`]) {
    test(`${route.replace(vendorId, ":id")} passes axe in ${theme} theme`, async ({ page }) => {
      await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
      await stubVendors(page);
      await page.goto(route);
      await page.locator(".app-shell").waitFor();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.getByText(route === "/vendors" ? "Acme Tooling" : "Pat Payer").first()).toBeVisible();
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });
  }
}
