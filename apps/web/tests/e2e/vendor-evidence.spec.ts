import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { inboxRow, stubApprovals } from "./approvals-fixture";

const vendorRow = (n: number, title: string) => inboxRow(n, { document_type: "vendor", title, number: `VEN-${n}`, href: `/vendors/${n}`, amount: null });
const create = {
  kind: "create", reason: null, contacts: [],
  payload: {
    score: 62, threshold: 70, breakdown: [{ prompt: "Insurance in force", weight: 40, points: 3, contribution: "24" }],
    duplicates: ["tax_id"], override_reason: "Same group, new entity",
  },
  duplicate_matches: [{ field: "tax_id", vendor_id: "bbbbbbbb-0000-4000-8000-000000000001", vendor_number: "VEN-00007", trading_name: "Acme Ltd", masked_value: "•••• 6789" }],
  document_state: [{ type_code: "insurance", required: true, state: "missing" }, { type_code: "w9", required: true, state: "ok", verification: "verified" }],
};

async function setup(page: Page) {
  const state = await stubApprovals(page);
  state.pending = [vendorRow(1, "New vendor: Acme"), vendorRow(2, "Vendor availability change: Acme"), vendorRow(3, "Vendor suspension: Acme"), vendorRow(4, "Vendor reactivation: Acme"), inboxRow(5)];
  state.evidence[state.pending[0]!.instance_id] = create;
  state.evidence[state.pending[1]!.instance_id] = { kind: "availability", reason: "Only here", payload: { availability: "restricted" }, current_availability: "global", current_units: [], new_units: ["BU1"], open_rfqs: 0, issued_purchase_orders: 0 };
  state.evidence[state.pending[2]!.instance_id] = { kind: "suspend", reason: "Audit finding", payload: {}, open_rfqs: 2, issued_purchase_orders: 1 };
  state.evidence[state.pending[3]!.instance_id] = { kind: "reactivate", reason: "Cleared", payload: {}, open_rfqs: 0, issued_purchase_orders: 0 };
  await page.goto("/approvals");
  return state;
}
const open = async (page: Page, name: string) => {
  await page.getByRole("button", { name: new RegExp(name) }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  return page.getByRole("region", { name: /Evidence/ });
};

test("a below-threshold score is a fact; Approve stays enabled and decides", async ({ page }) => {
  const state = await setup(page);
  const evidence = await open(page, "New vendor");
  await expect(evidence.getByText("Score 62 of 100, below the threshold of 70")).toBeVisible();
  const approve = page.getByRole("button", { name: "Approve", exact: true });
  await expect(approve).toBeEnabled(); // plant: disable below the threshold
  await approve.click();
  await expect.poll(() => state.posts.at(-1)?.body.outcome).toBe("approve");
});

test("the duplicate shows its reason and link, and the identifier is masked", async ({ page }) => {
  await setup(page);
  const evidence = await open(page, "New vendor");
  await expect(evidence.getByRole("link", { name: "VEN-00007 Acme Ltd" })).toHaveAttribute("href", /\/vendors\/bbbbbbbb/);
  await expect(evidence.getByText("•••• 6789")).toBeVisible(); // plant: show the full tax id
  await expect(evidence.getByText("Same group, new entity")).toBeVisible();
  await expect(evidence).not.toContainText(/\d{3}-?\d{2}-?\d{4}/);
  await expect(evidence.getByText("Missing, required")).toBeVisible();
});

test("the score breakdown fold opens by keyboard", async ({ page }) => {
  await setup(page);
  const evidence = await open(page, "New vendor");
  const summary = evidence.getByText("Score Breakdown");
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(evidence.getByRole("cell", { name: "40" })).toBeVisible();
  await page.keyboard.press("Space");
  await expect(evidence.getByRole("cell", { name: "40" })).toBeHidden();
});

test("availability, suspend and reactivate render their evidence", async ({ page }) => {
  await setup(page);
  let evidence = await open(page, "availability change");
  await expect(evidence).toContainText("global, units: none");
  await expect(evidence).toContainText("restricted, units: BU1");
  await expect(evidence).toContainText("Only here");
  await page.keyboard.press("Escape");
  evidence = await open(page, "suspension");
  await expect(evidence.getByText("Open RFQs").locator("..")).toContainText("2");
  await expect(evidence.getByText("Issued purchase orders").locator("..")).toContainText("1");
  await page.keyboard.press("Escape");
  evidence = await open(page, "reactivation");
  await expect(evidence).toContainText("Cleared");
});

test("a requisition task renders no evidence region", async ({ page }) => {
  await setup(page);
  await page.getByRole("button", { name: /Press brake 5/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("region", { name: /Evidence/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Open the full requisition/ })).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [1440, 375]) {
    test(`vendor evidence passes axe at ${width} in ${theme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.setViewportSize({ width, height: 900 });
      await setup(page);
      const evidence = await open(page, "New vendor");
      await evidence.getByText("Score Breakdown").click();
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });
  }
}
