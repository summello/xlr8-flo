import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { poId } from "./po-screens-fixture";
import { stubCancel } from "./po-cancel-fixture";

test.use({ viewport: { width: 1400, height: 1000 } });

const open = async (page: Page, baseURL: string | undefined, name: string) => {
  await page.context().addCookies([{ name: "flo_csrf", value: "test-csrf", url: baseURL ?? "http://localhost" }]);
  await page.goto(`/purchase-orders/${poId}`);
  await page.getByRole("heading", { name: "PO-0007", level: 2 }).waitFor();
  const trigger = page.getByRole("button", { name, exact: true });
  await trigger.click();
  return { trigger, dialog: page.getByRole("dialog") };
};

test("cancel an issued PO: the server's figure, the returned-to switch, the request, and the cancelled page", async ({ page, baseURL }) => {
  const state = await stubCancel(page);
  const { dialog } = await open(page, baseURL, "Cancel or Reduce");
  await expect(dialog.getByRole("button", { name: "Keep PO" })).toBeFocused();
  await expect(dialog).toContainText("Releases 1,234.56 USD once and re-reserves it for requisition REQ-0042 on SUB-7 Sub Seven");
  await dialog.getByLabel("Also cancel these requisition lines").check();
  await expect(dialog).toContainText("returns it to MAIN-3 Main Three. The requisition lines are cancelled.");
  await dialog.getByRole("button", { name: "Cancel PO" }).click();
  await expect(dialog.getByText("Give a reason.")).toBeVisible();
  expect(state.sent).toHaveLength(0);
  await dialog.getByLabel("Reason (required)").fill("Vendor withdrew");
  await dialog.getByRole("button", { name: "Cancel PO" }).click();
  await expect(dialog).toHaveCount(0);
  const [sent] = state.sent;
  expect(sent!.headers["x-csrf-token"]).toBe("test-csrf");
  expect(sent!.headers["idempotency-key"]).toBeTruthy();
  expect(sent!.headers["if-match"]).toBe("3");
  expect(sent!.body).toEqual({ reason: "Vendor withdrew", cancel_requisition_lines: true, notify_vendor: true });
  await expect(page.getByText("Cancelled.")).toBeVisible();
  await expect(page.getByText("Released commitment")).toBeVisible();
  await expect(page.getByText("Status: cancelled")).toBeVisible();
});

test("a draft shows Cancel PO with no mode choice and neither checkbox; no cancel action shows no trigger", async ({ page, baseURL }) => {
  await stubCancel(page, { status: "draft", actions: ["cancel"] });
  const { dialog } = await open(page, baseURL, "Cancel PO");
  await expect(dialog.getByRole("radio")).toHaveCount(0);
  await expect(dialog.getByRole("checkbox")).toHaveCount(0);
  await expect(dialog).toContainText("The vendor never received this PO, so nothing is sent.");
  await page.keyboard.press("Escape");
  await page.unroute("**/api/v1/purchase-orders/**");
  await stubCancel(page, { actions: [] });
  await page.reload();
  await page.getByRole("heading", { name: "PO-0007", level: 2 }).waitFor();
  await expect(page.getByRole("button", { name: /^Cancel/ })).toHaveCount(0);
});

test("reduce: only the changed rows are sent, the result view shows the signed effect, a bad quantity is refused at the field", async ({ page, baseURL }) => {
  const state = await stubCancel(page);
  const { dialog } = await open(page, baseURL, "Cancel or Reduce");
  await dialog.getByLabel("Reduce Quantities").check();
  await dialog.getByLabel("New quantity for line 1").fill("4");
  await dialog.getByLabel("Reason (required)").fill("Over-ordered");
  await dialog.getByRole("button", { name: "Submit Reduction" }).click();
  await expect(dialog.getByText("Enter a quantity between 0 and 4; to drop the line, choose Remove.")).toBeVisible();
  expect(state.sent).toHaveLength(0);
  await dialog.getByLabel("New quantity for line 1").fill("2.5");
  await dialog.getByLabel("Remove line 3").check();
  await dialog.getByRole("button", { name: "Submit Reduction" }).click();
  await expect(dialog.getByRole("heading", { name: "Change Order 2 Submitted" })).toBeVisible();
  expect(state.sent[0]!.body).toEqual({ reason: "Over-ordered", lines: [{ line_no: 1, quantity: "2.5" }, { line_no: 3, remove: true }] });
  await expect(dialog).toContainText("It is waiting for approval.");
  await expect(dialog).toContainText("123.45");
  await expect(dialog).toContainText("OPS");
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeFocused();
});

test("nothing changed is refused; a stale_version refusal keeps the reason and offers Reload", async ({ page, baseURL }) => {
  const state = await stubCancel(page, { fail: { status: 409, body: { detail: "The purchase order changed. Reload it and retry.", correlation_id: "c1", checks: { problem: "stale_version" } } } });
  const { dialog } = await open(page, baseURL, "Cancel or Reduce");
  await dialog.getByLabel("Reduce Quantities").check();
  await dialog.getByLabel("Reason (required)").fill("Vendor withdrew");
  await dialog.getByRole("button", { name: "Submit Reduction" }).click();
  await expect(dialog.getByText("Lower a quantity or remove a line.")).toBeVisible();
  expect(state.sent).toHaveLength(0);
  await dialog.getByLabel("Cancel the Whole PO").check();
  await dialog.getByRole("button", { name: "Cancel PO" }).click();
  await expect(dialog.getByRole("alert").filter({ hasText: "changed" })).toContainText("Your reason is kept.");
  await expect(dialog.getByLabel("Reason (required)")).toHaveValue("Vendor withdrew");
  const first = state.sent[0]!.headers["idempotency-key"];
  await dialog.getByRole("button", { name: "Cancel PO" }).click();
  await expect.poll(() => state.sent.length).toBe(2);
  expect(state.sent[1]!.headers["idempotency-key"]).not.toBe(first); // a 409 is final: a fresh key
  await dialog.getByRole("button", { name: "Reload" }).click();
  await expect(dialog).toHaveCount(0);
});

test("the danger fill is on the confirm button only; Escape returns focus to the trigger; keyboard only", async ({ page, baseURL }) => {
  const state = await stubCancel(page);
  await page.context().addCookies([{ name: "flo_csrf", value: "test-csrf", url: baseURL ?? "http://localhost" }]);
  await page.goto(`/purchase-orders/${poId}`);
  const trigger = page.getByRole("button", { name: "Cancel or Reduce", exact: true });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Keep PO" })).toBeFocused();
  const danger = await page.evaluate(() => { const p = document.createElement("i"); p.style.color = "var(--status-danger)"; document.body.append(p); const c = getComputedStyle(p).color; p.remove(); return c; });
  const fill = (name: string) => dialog.getByRole("button", { name, exact: true }).evaluate(el => getComputedStyle(el).backgroundColor);
  expect(await fill("Cancel PO")).toBe(danger);
  expect(await fill("Keep PO")).not.toBe(danger);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Enter");
  await dialog.getByLabel("Reason (required)").focus();
  await page.keyboard.type("Vendor withdrew");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Cancel PO" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveCount(0);
  expect(state.sent).toHaveLength(1);
});

for (const theme of ["light", "dark"] as const) {
  for (const mode of ["Cancel the Whole PO", "Reduce Quantities"]) {
    test(`${mode} passes axe in ${theme}`, async ({ page, baseURL }) => {
      await page.emulateMedia({ colorScheme: theme });
      await stubCancel(page);
      const { dialog } = await open(page, baseURL, "Cancel or Reduce");
      await dialog.getByLabel(mode).check();
      await expect(dialog).toContainText(mode === "Reduce Quantities" ? "Line" : "Releases 1,234.56 USD");
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });
  }
}
