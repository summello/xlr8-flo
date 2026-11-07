import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { stubVendors, vendorId } from "./vendors-fixture";

async function addScore(page: Page, category: string, score: string, order: string) {
  const form = page.locator("form.perf-form");
  await form.getByLabel("Category").selectOption(category);
  await form.getByLabel(/^Score, 1 to 5/).selectOption(score);
  await form.getByLabel("Period").fill("2026-09");
  await form.getByLabel("Source order").fill(order);
  await form.getByLabel("Rationale").fill("Delivered as promised.");
  await form.getByRole("button", { name: "Save Score" }).click();
}

test("add a score, dispute it, resolve upheld: struck original, adjustment, and the card follows the server", async ({ page }) => {
  await stubVendors(page);
  await page.goto(`/vendors/${vendorId}`);
  await expect(page.getByText("No scores yet")).toBeVisible();
  await page.getByRole("button", { name: "Open Scorecard" }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("button", { name: "Add Score" }).click();
  await sheet.getByRole("button", { name: "Save Score" }).click(); // empty: field errors and a summary, nothing sent
  await expect(sheet.getByRole("alert").filter({ hasText: "Review the fields below" })).toBeVisible();
  await addScore(page, "QUALITY", "4", "PO-0001");
  await expect(sheet.getByRole("status")).toHaveText("Score added.");
  await expect(sheet.getByRole("row", { name: /PO-0001/ })).toContainText("4");
  await sheet.getByRole("button", { name: "Dispute PO-0001 QUALITY" }).click();
  await sheet.getByLabel("Reason").fill("Late delivery was not counted.");
  await sheet.getByRole("button", { name: "Raise Dispute" }).click();
  await expect(sheet.getByText("Open, waiting for a decision")).toBeVisible();
  await sheet.getByRole("button", { name: "Resolve dispute on PO-0001 QUALITY" }).click();
  await sheet.getByRole("button", { name: "Resolve", exact: true }).click(); // upheld needs the adjustment
  await expect(sheet.getByRole("alert").filter({ hasText: "Adjusted score: Required." })).toBeVisible();
  await sheet.getByLabel("Note").fill("Confirmed against the receipt.");
  await sheet.getByLabel(/^Adjusted score/).selectOption("2");
  await sheet.getByLabel("Adjusted rationale").fill("Two days late.");
  await sheet.getByRole("button", { name: "Resolve", exact: true }).click();
  await expect(sheet.getByText("Dispute upheld; the adjustment is added.")).toBeVisible();
  const original = sheet.locator("tr").filter({ hasText: "Delivered as promised." });
  await expect(original.getByText("Original score, replaced by adjustment")).toBeAttached(); // text, not only the strike
  await expect(original.locator("s")).toHaveText("4");
  await expect(sheet.locator("tr").filter({ hasText: "Two days late." })).toContainText("Adjustment");
  await page.keyboard.press("Escape");
  await expect(page.locator(".perf-overall strong")).toHaveText("2.0"); // from the server, after the adjustment
});

test("a dismissed dispute changes no figure", async ({ page }) => {
  const state = await stubVendors(page);
  state.scores.push({ id: "bbbbbbbb-0000-4000-8000-000000000001", category_code: "PRICING", score: 3, period: "2026-09-01", source_type: "po", source_ref: "PO-0009", rationale: "On budget.", scored_by_email: "dev@example.test", scored_at: "2026-10-01T00:00:00Z", supersedes: null, superseded_by: null, disputes: [] });
  await page.goto(`/vendors/${vendorId}`);
  await expect(page.locator(".perf-overall strong")).toHaveText("3.0");
  await page.getByRole("button", { name: "Open Scorecard" }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("button", { name: "Dispute PO-0009 PRICING" }).click();
  await sheet.getByLabel("Reason").fill("Should be higher.");
  await sheet.getByRole("button", { name: "Raise Dispute" }).click();
  await sheet.getByRole("button", { name: "Resolve dispute on PO-0009 PRICING" }).click();
  await sheet.getByLabel("Dismissed").check();
  await sheet.getByLabel("Note").fill("Receipt matches.");
  await sheet.getByRole("button", { name: "Resolve", exact: true }).click();
  await expect(sheet.getByText("Dispute dismissed; no figure changed.")).toBeVisible();
  await expect(sheet.locator("s")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.locator(".perf-overall strong")).toHaveText("3.0");
});

test("the card shows the server's average, never one computed from the visible rows", async ({ page }) => {
  const state = await stubVendors(page);
  state.overall = "3.7";
  for (const [i, n] of [5, 5, 5].entries()) state.scores.push({ id: `cccccccc-0000-4000-8000-00000000000${i}`, category_code: "SERVICE", score: n, period: "2026-09-01", source_type: "po", source_ref: `PO-10${i}`, rationale: "Good.", scored_by_email: "dev@example.test", scored_at: "2026-10-01T00:00:00Z", supersedes: null, superseded_by: null, disputes: [] });
  await page.goto(`/vendors/${vendorId}`);
  await expect(page.locator(".perf-overall strong")).toHaveText("3.7");
  await expect(page.getByRole("table", { name: "Average score by category" })).toContainText("SERVICE");
});

test("keyboard only: Open Scorecard, add a score, Escape returns focus to the button", async ({ page }) => {
  await stubVendors(page);
  await page.goto(`/vendors/${vendorId}`);
  const open = page.getByRole("button", { name: "Open Scorecard" });
  await open.focus();
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("button", { name: "Add Score" }).focus();
  await page.keyboard.press("Enter");
  await expect(sheet.getByLabel("Category")).toBeVisible();
  await sheet.getByLabel("Category").focus();
  await page.keyboard.press("ArrowDown");
  await addScore(page, "DELIVERY", "5", "PO-0002");
  await expect(sheet.getByRole("status")).toHaveText("Score added.");
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await expect(open).toBeFocused();
});

for (const theme of ["light", "dark"] as const) {
  test(`profile with the card and the open scorecard pass axe in ${theme} theme`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    const state = await stubVendors(page);
    state.scores.push({ id: "dddddddd-0000-4000-8000-000000000001", category_code: "QUALITY", score: 4, period: "2026-09-01", source_type: "po", source_ref: "PO-0001", rationale: "Fine.", scored_by_email: "dev@example.test", scored_at: "2026-10-01T00:00:00Z", supersedes: null, superseded_by: "dddddddd-0000-4000-8000-000000000002", disputes: [{ id: "eeeeeeee-0000-4000-8000-000000000001", score_id: "dddddddd-0000-4000-8000-000000000001", raised_by_email: "a@example.test", raised_at: "2026-10-01T00:00:00Z", reason: "Low.", resolution: "upheld", resolved_by_email: "b@example.test", resolved_at: "2026-10-02T00:00:00Z", resolution_note: "Agreed." }] },
      { id: "dddddddd-0000-4000-8000-000000000002", category_code: "QUALITY", score: 5, period: "2026-09-01", source_type: "po", source_ref: "PO-0001", rationale: "Adjusted.", scored_by_email: "dev@example.test", scored_at: "2026-10-02T00:00:00Z", supersedes: "dddddddd-0000-4000-8000-000000000001", superseded_by: null, disputes: [] });
    await page.goto(`/vendors/${vendorId}`);
    await page.locator(".app-shell").waitFor();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(page.locator(".perf-overall strong")).toHaveText("5.0");
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.getByRole("button", { name: "Open Scorecard" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Add Score" }).click();
    await expect(page.getByRole("dialog").getByText("Replaced", { exact: true })).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
