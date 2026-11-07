import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { inboxRow, stubApprovals } from "./approvals-fixture";

const poId = "dddddddd-0000-4000-8000-000000000009";
const poRow = inboxRow(1, { document_type: "po", title: "Purchase order PO-00001", number: "PO-00001", href: `/purchase-orders/${poId}` });
const clear = { eligible: true, reasons: [], warnings: [] };
const suspended = { eligible: false, reasons: [{ code: "vendor_suspended", text: "Vendor was suspended after the award", document_type: null, date: null }], warnings: [] };
const evidence = (eligibility: unknown) => ({
  award: {
    basis: "manual", rationale: "Best value on delivery",
    not_lowest: [{ line_no: 1, vendor_name: "Beta Ltd", justification: "Faster delivery" }],
  },
  budget_effect: [
    { project_id: "p1", project_number: "PRJ-0001", project_name: "Plant renewal", currency: "USD", amount: "700.0000" },
    { project_id: "p2", project_number: "PRJ-0002", project_name: "Press line", currency: "USD", amount: "200.0000" },
  ],
  vendor_eligibility_now: eligibility,
  recipients: [{ name: "Sam Seller", email: "sam@vendor.example" }],
});

async function setup(page: Page, eligibility: unknown = clear) {
  const state = await stubApprovals(page);
  state.pending = [poRow, inboxRow(2)];
  state.evidence[poRow.instance_id] = evidence(eligibility);
  await page.goto("/approvals");
  await page.getByRole("button", { name: /Purchase order PO-00001/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  return { state, block: page.getByRole("region", { name: "Purchase Order Evidence" }) };
}

test("a PO task renders every evidence section", async ({ page }) => {
  const { block } = await setup(page);
  await expect(block.getByText("Manual Selection")).toBeVisible();
  await expect(block.getByText("Best value on delivery")).toBeVisible();
  await expect(block.getByRole("rowheader", { name: "PRJ-0001 Plant renewal" })).toBeVisible();
  await expect(block.getByRole("row", { name: /PRJ-0002/ })).toContainText("200.00");
  await expect(block.getByText("Eligible Here")).toBeVisible();
  await expect(block.getByText("Faster delivery")).toBeVisible();
  await expect(block.getByText("sam@vendor.example")).toBeVisible();
  await expect(page.getByRole("link", { name: /Open the full purchase order/ })).toHaveAttribute("href", `/purchase-orders/${poId}`);
});

test("a vendor suspended after award shows the signal and Approve stays enabled", async ({ page }) => {
  const { block, state } = await setup(page, suspended);
  await expect(block.getByText("Not Eligible")).toBeVisible();
  await expect(block.getByText("Vendor was suspended after the award")).toBeVisible();
  const approve = page.getByRole("button", { name: "Approve", exact: true });
  await expect(approve).toBeEnabled();
  await approve.click();
  await expect.poll(() => state.posts.at(-1)?.body.outcome).toBe("approve");
});

test("a requisition task is unchanged: no PO evidence, the requisition link", async ({ page }) => {
  await setup(page);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Press brake 2/ }).first().click();
  await expect(page.getByRole("region", { name: "Purchase Order Evidence" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Open the full requisition/ })).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [1440, 375]) {
    test(`PO evidence passes axe at ${width} in ${theme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.setViewportSize({ width, height: 900 });
      const { block } = await setup(page, suspended);
      await expect(block.getByText("sam@vendor.example")).toBeVisible();
      const overflow = await page.evaluate(() => { const d = document.querySelector("[role=dialog]")!; return d.scrollWidth - d.clientWidth; });
      expect(overflow).toBeLessThanOrEqual(0);
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });
  }
}
