import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { poId, priorId, resultId, stubChange } from "./po-change-fixture";

test.use({ viewport: { width: 1400, height: 1000 } });
const screen = `/purchase-orders/${poId}/changes/1`;
const open = async (page: Page, o: Parameters<typeof stubChange>[1] = {}) => {
  const stub = await stubChange(page, o);
  await page.goto(screen);
  await expect(page.getByRole("heading", { name: /Change order 1/ })).toBeVisible();
  return stub;
};
const quantity = (page: Page, n: number) => page.getByLabel(`Quantity, line ${n}`);

test("a date-only edit sends one PATCH, reads as no approval needed, and Apply lands on the new version", async ({ page }) => {
  const stub = await open(page);
  await page.getByLabel("Needed by").fill("2026-12-01");
  await expect.poll(() => stub.patches().length).toBe(1);
  const patch = stub.patches()[0]!;
  expect(patch.headers["if-match"]).toBe("1");
  expect(patch.headers["x-csrf-token"]).toBe("test-csrf");
  expect(patch.body).toMatchObject({ needed_by: "2026-12-01", lines: [] });
  await expect(page.getByText("No approval needed: only the needed-by date changes")).toBeVisible();
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).toHaveURL(`/purchase-orders/${resultId}`);
  expect(stub.calls.at(-1)).toMatchObject({ method: "POST", path: `/api/v1/purchase-orders/${poId}/changes/1/apply` });
  expect(stub.calls.at(-1)!.headers["if-match"]).toBe("2");
  await page.goto(`/purchase-orders/${priorId}`);
  await expect(page.getByRole("main")).toContainText("PO-0007");
});

test("a lower quantity shows the server's signed effect, needs approval, and a missing workflow keeps the typed value", async ({ page }) => {
  const stub = await open(page, { submit: { status: 409, body: { title: "Conflict", status: 409, detail: "x", correlation_id: "c", checks: { problem: "workflow_not_set_up" } } } });
  await quantity(page, 1).fill("2");
  await expect.poll(() => stub.patches().length).toBe(1);
  expect(stub.patches()[0]!.body).toMatchObject({ lines: [{ line_no: 1, quantity: "2" }] });
  await expect(page.getByText("(-17.03)")).toBeVisible();
  await expect(page.getByText(/held again for the requisition/)).toBeVisible();
  await expect(page.getByText("Needs approval: the amount changes")).toBeVisible();
  await expect(page.getByText("18,017.03")).toBeVisible(); // the server's odd total, verbatim
  await page.getByRole("button", { name: "Submit for Approval" }).click();
  await expect(page.getByText("No purchase order change workflow is published.")).toBeVisible();
  await expect(quantity(page, 1)).toHaveValue("2");
});

test("putting a quantity back sends lines: []", async ({ page }) => {
  const stub = await open(page);
  await quantity(page, 1).fill("2");
  await expect.poll(() => stub.patches().length).toBe(1);
  await quantity(page, 1).fill("4.0000");
  await expect.poll(() => stub.patches().length).toBe(2);
  expect(stub.patches()[1]!.body).toMatchObject({ lines: [] });
});

test("typing quickly sends one PATCH; an edit during a PATCH waits for it and carries the new version", async ({ page }) => {
  const stub = await open(page, { patchDelay: 2500 });
  await quantity(page, 1).pressSequentially("123", { delay: 20 });
  await expect.poll(() => stub.patches().length).toBe(1);
  await quantity(page, 2).fill("3"); // while the first is held in flight
  await page.waitForTimeout(1000);
  expect(stub.patches().length).toBe(1);
  await expect.poll(() => stub.patches().length).toBe(2);
  expect(stub.patches()[1]!.headers["if-match"]).toBe("2");
});

test("Apply flushes an unsent edit first: PATCH, then POST", async ({ page }) => {
  const stub = await open(page);
  // Type and press Apply in one synchronous turn, so the 400 ms debounce cannot have sent anything yet.
  await page.evaluate(() => {
    const input = document.getElementById("chg-needed") as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "2026-12-01");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    [...document.querySelectorAll("button")].find(b => b.textContent === "Apply")!.click();
  });
  await expect(page).toHaveURL(`/purchase-orders/${resultId}`);
  expect(stub.calls.map(c => c.method)).toEqual(["PATCH", "POST"]);
  expect(stub.calls[0]!.body).toMatchObject({ needed_by: "2026-12-01" });
});

test("a cleared quantity is sent blank, the server's field error shows under it, and the blank stays", async ({ page }) => {
  const stub = await open(page, { patchInvalid: { field: "lines[0].quantity", message: "Enter a quantity." } });
  await quantity(page, 1).fill("");
  await expect.poll(() => stub.patches().length).toBe(1);
  expect(stub.patches()[0]!.body).toMatchObject({ lines: [{ line_no: 1, quantity: "" }] });
  await expect(page.locator("#chg-l1-quantity-error")).toContainText("Enter a quantity.");
  await expect(quantity(page, 1)).toHaveValue("");
});

test("document freight typed then cleared on a PO with none sends a blank, not nothing", async ({ page }) => {
  const stub = await open(page, { documentMode: true, patchInvalid: { field: "document_freight", message: "Enter freight." } });
  const freight = page.getByLabel("Document freight");
  await freight.fill("30");
  await expect.poll(() => stub.patches().length).toBe(1);
  await freight.fill("");
  await expect.poll(() => stub.patches().length).toBe(2);
  expect(stub.patches()[1]!.body).toMatchObject({ document_freight: "" });
});

test("a 422 on the flushing PATCH stops before the POST", async ({ page }) => {
  const stub = await open(page, { patchInvalid: { field: "needed_by", message: "Pick a later date." } });
  await page.getByLabel("Needed by").fill("2026-12-01");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByRole("link", { name: /Needed by: Pick a later date\./ })).toBeVisible();
  await expect(page.locator("#chg-needed-error")).toContainText("Pick a later date.");
  expect(stub.calls.some(c => c.method === "POST")).toBe(false);
});

test("read-only: approval_pending shows no input and no button; a draft without amend access says so", async ({ page }) => {
  await open(page, { status: "approval_pending", seed: { material: true, diff: [{ line_no: 1, field: "quantity", label: "Quantity", was: "4.0000", now: "2" }] } });
  await expect(page.getByText("Sent for approval. The issued PO is unchanged until the change is approved.")).toBeVisible();
  await expect(page.locator("main input, main textarea")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Apply|Submit for Approval/ })).toHaveCount(0);
  await expect(page.getByText("Needs approval: the amount changes")).toBeVisible();
});

test("read-only: a draft without change_order shows the access sentence", async ({ page }) => {
  await open(page, { actions: [] });
  await expect(page.getByText("You can view this change order. Changing it needs purchase order amend access.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Apply|Submit for Approval/ })).toHaveCount(0);
});

test("detail: Start Change Order only with the action; it POSTs with CSRF and lands on the change; 409 lands on the open one", async ({ page }) => {
  const stub = await stubChange(page);
  await page.goto(`/purchase-orders/${poId}`);
  await page.getByRole("button", { name: "Start Change Order" }).click();
  await expect(page).toHaveURL(screen);
  expect(stub.calls[0]!.headers["x-csrf-token"]).toBe("test-csrf");
  expect(stub.calls[0]!.headers["idempotency-key"]).toBeTruthy();
});

test("detail: 409 change_in_progress lands on the open change; no action, no button; the version list links to it", async ({ page }) => {
  await stubChange(page, { start: { status: 409, body: { title: "Conflict", status: 409, detail: "open", correlation_id: "c", checks: { problem: "change_in_progress" } } } });
  await page.goto(`/purchase-orders/${poId}`);
  await page.getByRole("button", { name: "Start Change Order" }).click();
  await expect(page).toHaveURL(screen);
  await page.goto(`/purchase-orders/${poId}`);
  await page.getByRole("button", { name: /Version v2/ }).click();
  await page.getByRole("link", { name: "Change order 1" }).click();
  await expect(page).toHaveURL(screen);
});

test("detail: no change_order action, no Start Change Order button", async ({ page }) => {
  await stubChange(page, { actions: [] });
  await page.goto(`/purchase-orders/${poId}`);
  await expect(page.getByRole("heading", { name: "PO-0007" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start Change Order" })).toHaveCount(0);
});

test("keyboard only: Tab to the quantity, type, Tab on to Submit for Approval, Enter", async ({ page }) => {
  const stub = await open(page);
  await quantity(page, 1).focus();
  await page.keyboard.press("Control+a");
  await page.keyboard.type("2");
  await expect.poll(() => stub.patches().length).toBe(1);
  await expect(page.getByRole("button", { name: "Submit for Approval" })).toBeVisible();
  const submit = page.getByRole("button", { name: "Submit for Approval" });
  for (let i = 0; i < 60 && !(await submit.evaluate(n => n === document.activeElement)); i++) await page.keyboard.press("Tab");
  await expect(submit).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Sent for approval.")).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
  test(`axe: change screen in ${theme}`, async ({ page }) => {
    await stubChange(page);
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(screen);
    await expect(page.getByRole("heading", { name: /Change order 1/ })).toBeVisible();
    await page.waitForFunction(() => document.getAnimations().length === 0);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
