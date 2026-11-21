import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { inboxRow, stubApprovals } from "./approvals-fixture";

const rfq = "dddddddd-0000-4000-8000-000000000001";
const [A, B, C] = ["aaaaaaaa-1111-4000-8000-000000000001", "bbbbbbbb-1111-4000-8000-000000000002", "cccccccc-1111-4000-8000-000000000003"];
const awardRow = inboxRow(1, { document_type: "award", title: "Award of RFQ-00001", number: "AWD-00001", href: `/sourcing/rfqs/${rfq}` });
const evidence = {
  rfq_id: rfq, basis: "manual", currency: "USD", rationale: "Best value on delivery",
  totals_by_vendor: [
    { vendor_id: A, vendor_name: "Alpha Ltd", budget_total: "400.00", total: "430.00" },
    { vendor_id: B, vendor_name: "Beta Ltd", budget_total: "500.00", total: "500.00" },
  ],
  requisitions: [{ requisition_id: "r1", requisition: "REQ-00001", scope: "1000.00", award: "900.00", over_scope_by: "0.00", release: "100.00", return_project: "PRJ-0001" }],
  not_lowest: [{ line_no: 1, vendor_name: "Beta Ltd", justification: "Faster delivery" }],
  tie_overrides: [{ line_no: 2, vendor_name: "Alpha Ltd", tie_override_reason: "Local supplier" }],
  exceptions: [{ vendor_id: A, bid_id: "b1", exceptions: "Net 45 terms" }],
  evaluator_results: { weighted: { [A]: "91.50", [B]: "78.25", [C]: "95.00" } },
  vendor_names: { [A]: "Alpha Ltd", [B]: "Beta Ltd", [C]: "Gamma Ltd" }, // Gamma was scored and not awarded
};
const evaluators = [{ identity_id: "e1", display_name: "Evelyn Evaluator", declaration: "conflict", detail: "Holds shares in Beta", declared_at: null, version: 1, submitted: true }];

async function setup(page: Page, patch: (state: Awaited<ReturnType<typeof stubApprovals>>) => void = () => {}) {
  const state = await stubApprovals(page);
  state.pending = [awardRow, inboxRow(2)];
  state.evidence[awardRow.instance_id] = evidence;
  state.awardRead.checks = [{ requisition_id: "r1", funds: "950.00", over_funds_by: "0.00" }];
  state.evaluationRead.evaluators = evaluators;
  patch(state);
  await page.goto("/approvals");
  await page.getByRole("button", { name: /Award of RFQ-00001/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  return { state, block: page.getByRole("region", { name: "Award Evidence" }) };
}

test("an award task renders every evidence section and approving calls the decision endpoint", async ({ page }) => {
  const { state, block } = await setup(page);
  await expect(block.getByText("Manual Selection")).toBeVisible();
  await expect(block.getByText("Best value on delivery")).toBeVisible();
  await expect(block.getByRole("rowheader", { name: "Alpha Ltd" })).toBeVisible();
  await expect(block.getByRole("rowheader", { name: "REQ-00001" })).toBeVisible();
  await expect(block.getByText("PRJ-0001")).toBeVisible();
  await expect(block.getByRole("cell", { name: /950\.00/ })).toBeVisible(); // funds, from the live award read
  await expect(block.getByText("Faster delivery")).toBeVisible();
  await expect(block.getByText("Local supplier")).toBeVisible();
  await expect(block.getByText("Net 45 terms")).toBeVisible();
  await block.getByText("Evaluators", { exact: true }).click();
  await expect(block.getByText("Conflict declared, Holds shares in Beta")).toBeVisible();
  await expect(block.getByRole("link", { name: "Open the full award" })).toHaveAttribute("href", `/sourcing/rfqs/${rfq}/award`);
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect.poll(() => state.posts.at(-1)?.body.outcome).toBe("approve");
});

test("a vendor whose budget total differs shows both figures; an equal one shows one", async ({ page }) => {
  const { block } = await setup(page);
  const alpha = block.getByRole("row", { name: /Alpha Ltd/ });
  await expect(alpha.getByRole("cell").nth(0)).toContainText("400.00");
  await expect(alpha.getByRole("cell").nth(0)).toContainText("USD");
  await expect(alpha.getByRole("cell").nth(1)).toContainText("430.00");
  await expect(alpha.getByRole("cell").nth(1)).toContainText("USD");
  const beta = block.getByRole("row", { name: /Beta Ltd/ });
  await expect(beta.getByRole("cell").nth(0)).toContainText("500.00");
  await expect(beta.getByRole("cell").nth(1)).not.toContainText("500.00");
  await expect(block.getByRole("columnheader", { name: "Total incl. tax & freight" })).toBeVisible();
});

test("the scores shown are the frozen ones, not the live evaluation read", async ({ page }) => {
  const { block } = await setup(page); // the stubbed read reports 88.00
  await block.getByText("Evaluators", { exact: true }).click();
  await expect(block.getByRole("row", { name: /Alpha Ltd/ }).last()).toContainText("91.50");
  await expect(block).not.toContainText("88.00");
  await expect(block.getByRole("row", { name: /Gamma Ltd/ })).toContainText("95.00"); // an unawarded bidder keeps its name
  await expect(block).not.toContainText("Vendor 95");
});

test("an award read refusal shows the funds fallback and the rest still renders", async ({ page }) => {
  const { block } = await setup(page, (s) => { s.awardRead.status = 403; });
  await expect(block.getByText("Funds are not available to you").first()).toBeVisible();
  await expect(block.getByText("Faster delivery")).toBeVisible();
  await expect(block.getByRole("rowheader", { name: "REQ-00001" })).toBeVisible();
});

test("an evaluation read refusal shows the evaluator fallback and the rest still renders", async ({ page }) => {
  const { block } = await setup(page, (s) => { s.evaluationRead.status = 403; });
  await block.getByText("Evaluators", { exact: true }).click();
  await expect(block.getByText("Evaluator details are not available to you")).toBeVisible();
  await expect(block.getByText("91.50")).toBeVisible();
  await expect(block.getByText("Net 45 terms")).toBeVisible();
});

test("a requisition task is unchanged: no award evidence, the requisition link", async ({ page }) => {
  await setup(page);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Press brake 2/ }).first().click();
  await expect(page.getByRole("region", { name: "Award Evidence" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Open the full requisition/ })).toBeVisible();
});

for (const [type, title, link, href] of [["vendor", "Vendor suspension: Acme", /Open the full vendor/, "/vendors/7"], ["project", "Project: Plant renewal", /Open the full requisition/, "/projects/7"]] as const) {
  test(`a ${type} task is unchanged: no award evidence and its own full-record link`, async ({ page }) => {
    const state = await stubApprovals(page);
    state.pending = [inboxRow(7, { document_type: type, title, href, amount: null })];
    await page.goto("/approvals");
    await page.getByRole("button", { name: new RegExp(title) }).first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("region", { name: "Award Evidence" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: link })).toHaveAttribute("href", href);
  });
}

test("the folds open by keyboard", async ({ page }) => {
  const { block } = await setup(page);
  await block.getByText("Evaluators", { exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(block.getByText("Evelyn Evaluator")).toBeVisible();
  await page.keyboard.press("Space");
  await expect(block.getByText("Evelyn Evaluator")).toBeHidden();
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [1440, 375]) {
    test(`award evidence passes axe at ${width} in ${theme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.setViewportSize({ width, height: 900 });
      const { block } = await setup(page);
      await block.getByText("Evaluators", { exact: true }).click();
      await expect(block.getByText("Evelyn Evaluator")).toBeVisible();
      const overflow = await page.evaluate(() => { const d = document.querySelector("[role=dialog]")!; return d.scrollWidth - d.clientWidth; });
      expect(overflow).toBeLessThanOrEqual(0); // wide tables scroll inside their own region, never the panel
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });
  }
}
