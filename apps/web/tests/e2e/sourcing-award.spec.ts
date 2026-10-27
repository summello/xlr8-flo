import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { B, C, award, check, comparison, problem, rfqId, stubAward } from "./award-fixture";
import { keyboardFocus } from "./helpers";

const url = `/sourcing/rfqs/${rfqId}/award?basis=lowest_cost_supplier`;
test.use({ viewport: { width: 1600, height: 1000 } });

const qty = (page: Page, vendor: string, line: number) => page.getByLabel(`Quantity for ${vendor}, line ${line}`);
const noApproveAndAward = async (page: Page) => { await expect(page.getByRole("button", { name: /Approve and Award/ })).toHaveCount(0); await expect(page.getByText("Approve and Award")).toHaveCount(0); };

test("start from the comparison, split lines, justify the non-lowest ones, submit: approval pending", async ({ page }) => {
  const stub = await stubAward(page);
  await page.goto(`/sourcing/rfqs/${rfqId}/comparison`);
  await page.getByRole("button", { name: "Start Award" }).click();
  await expect(page).toHaveURL(/\/award\?basis=lowest_cost_supplier$/);
  await expect(page.getByRole("heading", { name: "Award AWD-0001" })).toBeVisible();
  expect(stub.creates).toHaveLength(1);
  expect(stub.creates[0]!.key).toBeTruthy();
  await page.getByRole("button", { name: "Set All to Lowest" }).click();
  await expect(qty(page, "Cirrus Works", 1)).toHaveValue("4.0000");
  await expect(qty(page, "Bolt Supply", 2)).toHaveValue("2.0000");
  await qty(page, "Cirrus Works", 1).fill("2"); await qty(page, "Acme Tooling", 1).fill("2"); // split line 1 by quantity
  await qty(page, "Bolt Supply", 2).fill("1"); await qty(page, "Acme Tooling", 2).fill("1");
  await page.getByLabel(/^Justification.*Acme Tooling, line 1/).fill("Only vendor with stock");
  await page.getByLabel(/^Justification.*Acme Tooling, line 2/).fill("Existing contract");
  await page.getByLabel(/Award Rationale/).fill("Best value across both lines");
  await page.getByRole("button", { name: "Submit for Approval" }).click();
  await expect(page.getByText(/Approval Pending/)).toBeVisible();
  expect(stub.submits).toBe(1);
  expect(stub.puts[0]!.ifMatch).toBe("1");
  expect(stub.puts[0]!.lines).toHaveLength(4);
  expect(stub.puts[0]!.lines.filter(l => l.justification)).toHaveLength(2);
  expect(stub.patches[0]!.rationale).toBe("Best value across both lines");
  await noApproveAndAward(page);
});

test("load or create: an existing award is read only; without a basis there is an empty state; a lost race re-reads", async ({ page }) => {
  const stub = await stubAward(page, { existing: award() });
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "Award AWD-0001" })).toBeVisible();
  expect(stub.creates).toHaveLength(0);
});

test("no award and no basis: empty state with a way back", async ({ page }) => {
  const stub = await stubAward(page);
  await page.goto(`/sourcing/rfqs/${rfqId}/award`);
  await expect(page.getByRole("heading", { name: "No Award Yet" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to the comparison" }).first()).toBeVisible();
  expect(stub.creates).toHaveLength(0);
});

test("a 409 award_exists from the create re-reads the award", async ({ page }) => {
  const stub = await stubAward(page, { race: true });
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "Award AWD-0001" })).toBeVisible();
  expect(stub.creates).toHaveLength(1);
  expect(stub.gets).toBeGreaterThanOrEqual(2); // the 404 first, then the re-read (twice over in dev strict mode)
});

test("over scope: the refusal names the figure and the ways out, focus moves to it, selections stay", async ({ page }) => {
  const over = check({ award: "120.00", over_scope_by: "20.00", over_funds_by: "20.00", release: "0.00" });
  await stubAward(page, { existing: award(), checks: [over], submit: { status: 409, body: problem(409, "The award is over.", { checks: { problem: "over_scope", requisition: "REQ-1", scope: "100.00", funds: "100.00", award: "120.00", over_scope_by: "20.00", over_funds_by: "20.00" } }) } });
  await page.goto(url);
  await qty(page, "Cirrus Works", 1).fill("4");
  await page.getByLabel(/Award Rationale/).fill("Cheapest");
  await page.getByRole("button", { name: "Submit for Approval" }).click();
  const summary = page.getByRole("alert").filter({ hasText: "over its limit" });
  await expect(summary).toBeFocused();
  await expect(summary).toContainText(/Over scope by .*20\.00 USD on REQ-1/);
  await expect(summary).toContainText("Change the selection above, or amend the requisition");
  await expect(qty(page, "Cirrus Works", 1)).toHaveValue("4");
  await expect(page.getByRole("button", { name: "Submit for Approval" })).toBeEnabled();
});

test("no published workflow: the copy, the draft kept, and no Approve and Award anywhere", async ({ page }) => {
  await stubAward(page, { existing: award(), submit: { status: 409, body: problem(409, "No workflow.", { checks: { problem: "workflow_not_set_up" } }) } });
  await page.goto(url);
  await page.getByLabel(/Award Rationale/).fill("Cheapest");
  await page.getByRole("button", { name: "Submit for Approval" }).click();
  await expect(page.getByText("No award workflow is published. Ask an administrator to set one up. Your draft is kept.")).toBeVisible();
  await expect(page.getByLabel(/Award Rationale/)).toHaveValue("Cheapest");
  await expect(page.getByRole("button", { name: "Submit for Approval" })).toBeVisible();
  await noApproveAndAward(page);
});

test("the verdict is the server's, beside the total and in the pinned bar, even where the lines disagree", async ({ page }) => {
  // award 50.00 is under the scope of 100.00 yet the server says 9.00 over funds: the page repeats the server
  await stubAward(page, { existing: award({ total: "50.00", checks: [check({ award: "50.00", over_funds_by: "9.00" })] }) });
  await page.goto(url);
  const standing = page.locator(".aw-standing");
  await expect(standing).toHaveCount(2);
  for (const s of await standing.all()) { await expect(s).toContainText(/Over reserved funds by\s*9\.00\s*USD\s*on REQ-1/); await expect(s).toContainText("50.00"); }
  await expect(page.locator(".cmp-award .aw-standing")).toHaveCount(1);
  await expect(page.locator("p").filter({ hasText: /On approval,\s*20\.00\s*USD\s*will be released and returned to PRJ-1/ })).toBeVisible();
});

test("submit 422: blank rationale and a tie without a reason focus their fields from the summary", async ({ page }) => {
  const tied = comparison();
  (tied.lines[0]!.cells[1] as Record<string, unknown>).landed = "40000.0000"; // Bolt ties Cirrus on line 1
  const errors = [{ field: "rationale", message: "Give the rationale." }, { field: `lines/l1/${B}`, message: "Give the reason for overriding the tie order." }];
  await stubAward(page, { existing: award(), comparisonBody: tied, submit: { status: 422, body: problem(422, "The award cannot be submitted yet.", { errors, checks: { problem: "cannot_submit" } }) } });
  await page.goto(url);
  await qty(page, "Bolt Supply", 1).fill("4");
  await expect(page.getByLabel(/^Tie Override Reason.*Required/)).toBeVisible();
  await page.getByRole("button", { name: "Submit for Approval" }).click();
  const summary = page.getByRole("alert").filter({ hasText: "not submitted" });
  await expect(summary).toBeFocused();
  await summary.getByRole("link", { name: /Award Rationale/ }).click();
  await expect(page.locator("#aw-rationale")).toBeFocused();
  await summary.getByRole("link", { name: /Line 1 · Bolt Supply/ }).click();
  await expect(page.getByLabel(/^Tie Override Reason/)).toBeFocused();
});

test("a 403 shows its problem inline and keeps the draft", async ({ page }) => {
  await stubAward(page, { existing: award(), submit: { status: 403, body: problem(403, "You cannot approve awards.") } });
  await page.goto(url);
  await qty(page, "Cirrus Works", 1).fill("4");
  await page.getByRole("button", { name: "Submit for Approval" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "You cannot approve awards." })).toBeVisible();
  await expect(qty(page, "Cirrus Works", 1)).toHaveValue("4");
});

test("Set All to Lowest fills a partial lowest quote, not the RFQ line quantity", async ({ page }) => {
  const partial = comparison();
  (partial.lines[0]!.cells[2] as Record<string, unknown>).quantity = "2.0000"; // Cirrus quotes 2 of the 4 asked
  await stubAward(page, { existing: award(), comparisonBody: partial });
  await page.goto(url);
  await page.getByRole("button", { name: "Set All to Lowest" }).click();
  await expect(qty(page, "Cirrus Works", 1)).toHaveValue("2.0000");
  await expect(qty(page, "Bolt Supply", 2)).toHaveValue("2.0000");
});

test("keyboard only: Set All to Lowest is reachable and works", async ({ page }) => {
  await stubAward(page, { existing: award() });
  await page.goto(url);
  const button = page.getByRole("button", { name: "Set All to Lowest" });
  await keyboardFocus(page, button);
  await page.keyboard.press("Enter");
  await expect(qty(page, "Cirrus Works", 1)).toHaveValue("4.0000");
  await keyboardFocus(page, page.getByRole("button", { name: "Submit for Approval" }));
});

for (const theme of ["light", "dark"] as const) {
  test(`axe: the award draft in the ${theme} theme`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await stubAward(page, { existing: award({ total: "100.00", checks: [check({ over_scope_by: "5.00" })], lines: [] }) });
    await page.goto(url);
    await qty(page, "Acme Tooling", 1).fill("2");
    await expect(page.getByLabel(/^Justification.*Acme Tooling, line 1/)).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations.map(v => `${v.id}: ${v.nodes.map(n => JSON.stringify(n.target)).join(" ")}`)).toEqual([]);
  });
}
