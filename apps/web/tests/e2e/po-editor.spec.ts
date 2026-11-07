import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { poId, stubEditor } from "./po-editor-fixture";

test.use({ viewport: { width: 1400, height: 1000 } });

const open = async (page: Page, options: Parameters<typeof stubEditor>[1] = {}) => {
  await page.addInitScript(() => { document.cookie = "flo_csrf=test-csrf; path=/"; });
  const state = await stubEditor(page, options);
  await page.goto(`/purchase-orders/${poId}/edit`);
  await expect(page.getByRole("heading", { name: "PO-0007" })).toBeVisible();
  return state;
};
const patches = (s: Awaited<ReturnType<typeof stubEditor>>) => s.calls.filter(c => c.method === "PATCH");

test("save: one tax edit sends If-Match, the CSRF header and only that key; the totals are the server's", async ({ page }) => {
  const s = await open(page);
  await page.getByLabel("Tax, line 1").fill("41.50");
  await expect(page.getByText("Totals update when you save.")).toBeVisible();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  const [patch] = patches(s);
  expect(patch?.headers["if-match"]).toBe("3");
  expect(patch?.headers["x-csrf-token"]).toBe("test-csrf");
  expect(patch?.body).toEqual({ lines: [{ line_no: 1, tax: "41.50" }] });
  const totals = page.getByRole("region", { name: "Totals" });
  await expect(totals).toContainText("913.25"); // the stub's odd total, not 801.00 + 41.50 + 20.00
  await expect(totals).toContainText("79.50");
});

test("freight: Per document sends only the mode and the freight column turns read-only with the server's shares", async ({ page }) => {
  const s = await open(page);
  await page.getByRole("radio", { name: "Per document" }).check();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  expect(patches(s)[0]?.body).toEqual({ freight_mode: "document" });
  await expect(page.getByLabel("Freight, line 1")).toHaveCount(0);
  await expect(page.getByRole("table")).toContainText("7.77");
  await expect(page.getByLabel("Document freight")).toHaveValue("12.3400");
});

test("freight: a figure typed with the switch to Per document goes in the same PATCH", async ({ page }) => {
  const s = await open(page);
  await page.getByRole("radio", { name: "Per document" }).check();
  await page.getByLabel("Document freight").fill("9.99");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  expect(patches(s)[0]?.body).toEqual({ freight_mode: "document", document_freight: "9.99" });
});

test("submit with an unsaved edit saves first (PATCH then POST); a 409 workflow_not_set_up keeps the edit", async ({ page }) => {
  const s = await open(page, { submit: "workflow" });
  await page.getByLabel("Tax, line 2").fill("39");
  await page.getByRole("button", { name: "Submit for Approval" }).click();
  await expect(page.getByText("No purchase order workflow is published. Ask an administrator to set one up. Your draft is kept.")).toBeVisible();
  expect(s.calls.map(c => c.method)).toEqual(["PATCH", "POST"]);
  expect(s.calls[1]?.path).toBe(`/api/v1/purchase-orders/${poId}/submit`);
  await expect(page.getByLabel("Tax, line 2")).toHaveValue("40.0000"); // the stub's saved value: edits are saved, not lost
});

test("submit: a failing Save (422) sends no POST and shows the field error and the summary", async ({ page }) => {
  const s = await open(page, { patch: "invalid" });
  await page.getByLabel("Quantity, line 1").fill("99");
  await page.getByRole("button", { name: "Submit for Approval" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Review the fields below" })).toContainText("Line 1 Quantity: Quantity exceeds the open quantity.");
  await expect(page.getByLabel("Quantity, line 1")).toHaveValue("99");
  await expect(page.getByLabel("Quantity, line 1")).toHaveAttribute("aria-invalid", "true");
  expect(s.calls.filter(c => c.method === "POST")).toHaveLength(0);
});

test("no Approve button; a non-creator sees the sentence instead of Submit", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("button", { name: /approve/i })).toHaveCount(0);
  await page.unroute("**/api/v1/**");
  await stubEditor(page, { actions: ["edit"] });
  await page.reload();
  await expect(page.getByText("Only the buyer who drafted this order can submit it.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit for Approval" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /approve/i })).toHaveCount(0);
});

test("a PO that is not an editable draft gets the gate message", async ({ page }) => {
  await page.addInitScript(() => { document.cookie = "flo_csrf=test-csrf; path=/"; });
  await stubEditor(page, { actions: [], status: "issued" });
  await page.goto(`/purchase-orders/${poId}/edit`);
  await expect(page.getByText("This purchase order can no longer be edited here.")).toBeVisible();
});

test("budget preview shows the server's figures verbatim and only the tax flag that is false", async ({ page }) => {
  await open(page, { taxConsumes: false });
  const card = page.getByRole("region", { name: "Budget Preview" });
  await expect(card).toContainText(/Issuing will commit 17\.03\s*USD on P-100 Plant 4\./);
  await expect(card).toContainText(/Available after issue: 1\.00\s*USD\./);
  await expect(card).toContainText("Tax is shown on this order but does not count against the budget.");
  await expect(card).not.toContainText("Freight is shown on this order");
});

test("notes and files: a Sent to Vendor note posts shareable and lists as such; a checked file says it is not sent yet", async ({ page }) => {
  const s = await open(page);
  await page.getByLabel("Add a note").fill("Ring the dock first");
  await page.getByRole("radio", { name: "Sent to Vendor" }).check();
  await page.getByRole("button", { name: "Add note" }).click();
  await expect(page.getByText("Sent to Vendor", { exact: false }).last()).toBeVisible();
  expect(s.calls.find(c => c.path.endsWith("/notes"))?.body).toEqual({ body: "Ring the dock first", visibility: "shareable" });
  await page.getByRole("checkbox", { name: /For the vendor/ }).click();
  await expect(page.getByText("Not sent until scanning is available")).toBeVisible();
});

test("keyboard: Tab to a tax field, type, Tab to Save, Enter", async ({ page }) => {
  const s = await open(page);
  await page.getByLabel("Tax, line 1").focus();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.type("12");
  await page.getByRole("button", { name: "Save", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Saved.")).toBeVisible();
  expect(patches(s)[0]?.body).toEqual({ lines: [{ line_no: 1, tax: "12" }] });
});

for (const theme of ["light", "dark"] as const) {
  test(`axe: the editor in ${theme}`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await open(page);
    await expect(page.getByRole("region", { name: "Budget Preview" })).toContainText("Issuing will commit");
    await page.waitForFunction(() => document.getAnimations().length === 0);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
