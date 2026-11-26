import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { LEAKS, nextId, poId, stubPo } from "./po-screens-fixture";

test.use({ viewport: { width: 1400, height: 1000 } });
const page_ = `/purchase-orders/${poId}/vendor-copy`;
const NEVER = ["Chartfields (project, department, ledger account)", "Budget figures", "Internal notes", "Uploaded files: not sent until scanning is available"];

test("nothing internal reaches the vendor copy page or sheet; only vendor-copy is fetched; shared notes show", async ({ page }) => {
  const state = await stubPo(page, { leaks: true });
  await page.goto(page_);
  const main = page.locator("main");
  await expect(main).toContainText("Deliver to gate 4."); // not over-filtered
  for (const n of NEVER) await expect(main).toContainText(n);
  for (const s of LEAKS) await expect(main).not.toContainText(s);
  const api = [...new Set(state.paths)].filter(p => p.startsWith("/api/v1/") && !p.startsWith("/api/v1/auth/") && p !== "/api/v1/master/currency");
  expect(api).toEqual([`/api/v1/purchase-orders/${poId}/vendor-copy`]);

  await page.goto(`/purchase-orders/${poId}`);
  await page.getByRole("button", { name: "Vendor Copy" }).click();
  const dialog = page.getByRole("dialog", { name: "Vendor Copy" });
  await expect(dialog).toContainText("Deliver to gate 4.");
  for (const s of LEAKS) await expect(dialog).not.toContainText(s);
});

test("issued: Open PDF points at this PO; unissued: sentence, no link; v1 uses its own id", async ({ page }) => {
  await stubPo(page);
  await page.goto(page_);
  await expect(page.getByRole("link", { name: "Open PDF" })).toHaveAttribute("href", `/api/v1/purchase-orders/${poId}/pdf`);
  await expect(page.locator("main")).toContainText("Version v2 as issued on");
  await page.goto(`/purchase-orders/${nextId}/vendor-copy`);
  await expect(page.locator("main")).toContainText("Version v1");
  await expect(page.getByRole("link", { name: "Open PDF" })).toHaveAttribute("href", `/api/v1/purchase-orders/${nextId}/pdf`);
});

test("not issued: no Open PDF", async ({ page }) => {
  await stubPo(page, { status: "approved", vendorCopy: "unissued" });
  await page.goto(page_);
  await expect(page.locator("main")).toContainText("Not issued yet.");
  await expect(page.getByRole("link", { name: "Open PDF" })).toHaveCount(0);
});

test("404 and 500 say what happened; Try again reloads", async ({ page }) => {
  await stubPo(page, { vendorCopy: "404" });
  await page.goto(page_);
  await expect(page.getByRole("alert")).toContainText("This purchase order has no vendor copy: it does not exist, you cannot see it, or it was cancelled before it was issued.");
  await expect(page.getByRole("link", { name: "Back to purchase orders" })).toBeVisible();
});

test("500 then Try again", async ({ page }) => {
  const state = await stubPo(page, { vendorCopy: "500" });
  await page.goto(page_);
  await expect(page.getByRole("alert")).toBeVisible();
  state.copyFails = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.locator("main")).toContainText("Version v2 as issued on");
});

test("detail: sheet opens, Escape returns focus; cancelled shows the 404 sentence; keyboard reaches the full page", async ({ page }) => {
  await stubPo(page, { status: "cancelled", vendorCopy: "404" });
  await page.goto(`/purchase-orders/${poId}`);
  const button = page.getByRole("button", { name: "Vendor Copy" });
  await button.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Vendor Copy" });
  await expect(dialog).toContainText("has no vendor copy");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(button).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("link", { name: "Open full page" }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/purchase-orders/${poId}/vendor-copy$`));
});

for (const theme of ["light", "dark"] as const) for (const sheet of [false, true]) {
  test(`axe: vendor copy ${sheet ? "sheet" : "page"} in ${theme}`, async ({ page }) => {
    await stubPo(page);
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(sheet ? `/purchase-orders/${poId}` : page_);
    if (sheet) await page.getByRole("button", { name: "Vendor Copy" }).click();
    await expect(page.getByText("Never sent to the vendor")).toBeVisible();
    await page.waitForFunction(() => document.getAnimations().length === 0);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
