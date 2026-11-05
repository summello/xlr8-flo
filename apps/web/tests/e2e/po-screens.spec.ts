import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { counts, nextId, poId, stubPo } from "./po-screens-fixture";

test.use({ viewport: { width: 1400, height: 1000 } });

test("list: six tabs with the server's counts, version, project, sent and total columns", async ({ page }) => {
  await stubPo(page);
  await page.goto("/purchase-orders");
  const tabs = page.getByRole("tablist", { name: "Purchase order views" });
  for (const [name, key] of [["Drafts", "draft"], ["Awaiting Approval", "awaiting"], ["Ready to Issue", "ready"], ["Issued", "issued"], ["Needs Attention", "attention"], ["All", "all"]] as const) {
    await expect(tabs.getByRole("tab", { name: new RegExp(`^${name}\\s*${counts[key]}$`) })).toBeVisible();
  }
  const grid = page.getByRole("table", { name: "Purchase orders data grid" });
  await expect(grid.getByRole("link", { name: "PO-0007" })).toBeVisible();
  await expect(grid).toContainText("v2");
  await expect(grid).toContainText("P-100");
  await expect(grid).toContainText("2 projects");
  await expect(grid).toContainText("Bounced"); // words, not colour alone
  await expect(grid).toContainText("Delivered");
  await expect(grid).toContainText("18,000.00");
  await expect(grid).toContainText("USD");
  await expect(page.getByRole("link", { name: "New PO" })).toHaveCount(0); // hidden until the editor exists
});

test("list: arrow keys move between tabs; an empty tab says so and offers All", async ({ page }) => {
  await stubPo(page);
  await page.goto("/purchase-orders");
  const all = page.getByRole("tab", { name: /^All/ });
  await all.focus();
  await page.keyboard.press("Home");
  await expect(page.getByRole("tab", { name: /^Drafts/ })).toBeFocused();
  await expect(page.getByText("No purchase orders here")).toBeVisible();
  await page.keyboard.press("End");
  await expect(all).toBeFocused();
  await expect(page.getByRole("link", { name: "PO-0007" })).toBeVisible();
});

test("detail: Budget Summary shows the server's figures as sent, never its own sum", async ({ page }) => {
  await stubPo(page);
  await page.goto(`/purchase-orders/${poId}`);
  await expect(page.getByRole("heading", { name: "PO-0007" })).toBeVisible();
  const card = page.getByRole("region", { name: "Budget Summary" });
  // committed 5,000 + this PO 18,000 does not make 1.00; the page must not repair the deliberately odd figure
  await expect(card.getByText("Available After Issue").locator("..")).toContainText("1.00");
  await expect(card.getByText("Available After Issue").locator("..")).not.toContainText("23,000.00");
  await expect(card).toContainText("Committed");
  await expect(card).toContainText("This PO");
  await expect(card).toContainText("This PO holds a commitment.");
});

test("detail: a superseded revision hides the figures, says why and links the current revision", async ({ page }) => {
  await stubPo(page, { status: "superseded", budget: "superseded" });
  await page.goto(`/purchase-orders/${poId}`);
  const card = page.getByRole("region", { name: "Budget Summary" });
  await expect(card).toContainText("This revision was replaced. Budget figures are on the current revision.");
  await expect(card.getByRole("link", { name: "Open the current revision" })).toHaveAttribute("href", `/purchase-orders/${nextId}`);
  await expect(card).not.toContainText("18,000.00");
  await expect(page.getByRole("region", { name: "Lines" })).toBeVisible(); // every other card still works
  await expect(page.getByRole("region", { name: "History" })).toContainText("Issue");
});

test("detail: without ledger access the card is unavailable and shows no partial figure", async ({ page }) => {
  await stubPo(page, { budget: "forbidden" });
  await page.goto(`/purchase-orders/${poId}`);
  const card = page.getByRole("region", { name: "Budget Summary" });
  await expect(card).toContainText("unavailable");
  await expect(card).not.toContainText(/\d,\d{3}/);
});

test("detail: a suspended vendor after issue says the issued PO is unaffected", async ({ page }) => {
  await stubPo(page, { vendorStatus: "suspended" });
  await page.goto(`/purchase-orders/${poId}`);
  await expect(page.getByText("The issued PO is unaffected.")).toBeVisible();
  await expect(page.getByRole("list", { name: "Purchase order lifecycle" }).getByRole("listitem").filter({ hasText: "Issued" })).toHaveAttribute("aria-current", "step");
});

test("detail: the version control opens on Enter, lists versions then change orders, and Escape returns focus", async ({ page }) => {
  await stubPo(page);
  await page.goto(`/purchase-orders/${poId}`);
  const trigger = page.getByRole("button", { name: /^Version v2/ });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const panel = page.getByRole("group", { name: "Versions and change orders" });
  await expect(panel).toContainText("Change order 1");
  await expect(panel.getByRole("link", { name: "v1" })).toHaveAttribute("href", `/purchase-orders/${nextId}`);
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("detail: two columns at a wide container, stacked below 1100px, 40 lines stack at 375px", async ({ page }) => {
  await page.setViewportSize({ width: 1700, height: 1000 }); // the container is the main column, narrower than the window
  await stubPo(page);
  await page.goto(`/purchase-orders/${poId}`);
  const left = page.getByRole("region", { name: "Lines" }), right = page.getByRole("region", { name: "Budget Summary" });
  const wide = await Promise.all([left.boundingBox(), right.boundingBox()]);
  expect(wide[1]!.x).toBeGreaterThan(wide[0]!.x + wide[0]!.width - 1);
  expect(wide[0]!.width / (wide[0]!.width + wide[1]!.width)).toBeGreaterThan(0.6); // 70 / 30
  await page.setViewportSize({ width: 1000, height: 900 });
  const narrow = await Promise.all([left.boundingBox(), right.boundingBox()]);
  expect(narrow[1]!.y).toBeGreaterThan(narrow[0]!.y + narrow[0]!.height - 1);
  await page.setViewportSize({ width: 375, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.locator(".po-lines tbody tr")).toHaveCount(40);
});

for (const theme of ["light", "dark"] as const) for (const route of ["/purchase-orders", `/purchase-orders/${poId}`]) {
  test(`axe: ${route.replace(poId, ":id")} in ${theme}`, async ({ page }) => {
    await stubPo(page);
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(route);
    await expect(page.getByRole("main")).toContainText(/PO-0007/);
    await page.waitForFunction(() => document.getAnimations().length === 0); // the theme's colour transition has finished
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
