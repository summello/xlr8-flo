import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { poId } from "./po-screens-fixture";
import { stubCancel, type Sent } from "./po-cancel-fixture";

test.use({ viewport: { width: 1400, height: 1000 } });

/** `stubCancel` serves the PO (its `state.status` and the `actions` array are read on every GET); the transitions route is layered on top, registered later so it wins. */
async function stubClose(page: Page, { actions = ["close"], fail }: { actions?: string[]; fail?: { status: number; body: Record<string, unknown> } } = {}) {
  const sent: Sent[] = [], base = await stubCancel(page, { actions });
  await page.route(`**/api/v1/purchase-orders/${poId}/transitions`, route => {
    const req = route.request();
    sent.push({ method: "POST", path: new URL(req.url()).pathname, headers: req.headers(), body: req.postDataJSON() });
    if (fail) return route.fulfill({ status: fail.status, contentType: "application/json", body: JSON.stringify(fail.body) });
    base.status = "closed"; actions.length = 0;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "closed" }) });
  });
  return { sent };
}

const open = async (page: Page, baseURL: string | undefined) => {
  await page.context().addCookies([{ name: "flo_csrf", value: "test-csrf", url: baseURL ?? "http://localhost" }]);
  await page.goto(`/purchase-orders/${poId}`);
  await page.getByRole("heading", { name: "PO-0007", level: 2 }).waitFor();
  return page.getByRole("button", { name: "Close Purchase Order", exact: true });
};

test("close: empty reason refused in words, then the request carries the three headers and the page shows Closed", async ({ page, baseURL }) => {
  const state = await stubClose(page);
  await (await open(page, baseURL)).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Keep Open" })).toBeFocused();
  await expect(dialog).toContainText("Closing ends the order. It cannot be reopened.");
  await dialog.getByRole("button", { name: "Close Order" }).click();
  await expect(dialog.getByText("Give a reason.")).toBeVisible();
  expect(state.sent).toHaveLength(0);
  await dialog.getByLabel("Reason (required)").fill("Delivered and paid");
  await dialog.getByRole("button", { name: "Close Order" }).click();
  await expect(dialog).toHaveCount(0);
  const [sent] = state.sent;
  expect(sent!.headers["x-csrf-token"]).toBe("test-csrf");
  expect(sent!.headers["idempotency-key"]).toBeTruthy();
  expect(sent!.headers["if-match"]).toBe("3");
  expect(sent!.body).toEqual({ action: "close", reason: "Delivered and paid" });
  await expect(page.getByRole("button", { name: "Close Purchase Order" })).toHaveCount(0);
  await expect(page.getByText("Closed", { exact: true }).first()).toBeVisible();
});

test("without close in actions the control is absent", async ({ page, baseURL }) => {
  await stubClose(page, { actions: ["cancel"] });
  await open(page, baseURL);
  await expect(page.getByRole("button", { name: "Close Purchase Order" })).toHaveCount(0);
});

test("a stale 409 keeps the reason and offers Reload", async ({ page, baseURL }) => {
  const state = await stubClose(page, { fail: { status: 409, body: { detail: "changed", checks: { problem: "stale_version" } } } });
  await (await open(page, baseURL)).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Reason (required)").fill("Delivered and paid");
  await dialog.getByRole("button", { name: "Close Order" }).click();
  await expect(dialog.getByRole("alert")).toContainText("This order changed since you opened it. Your reason is kept.");
  await expect(dialog.getByLabel("Reason (required)")).toHaveValue("Delivered and paid");
  expect(state.sent).toHaveLength(1);
  await expect(dialog.getByRole("button", { name: "Reload" })).toBeVisible();
});

test("keyboard only: reach, open, type, submit; Escape returns focus to the trigger", async ({ page, baseURL }) => {
  const state = await stubClose(page);
  const trigger = await open(page, baseURL);
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Enter");
  await dialog.getByLabel("Reason (required)").focus();
  await page.keyboard.type("Delivered and paid");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Close Order" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveCount(0);
  expect(state.sent).toHaveLength(1);
});

for (const theme of ["light", "dark"] as const) {
  test(`close dialog passes axe in ${theme}`, async ({ page, baseURL }) => {
    await page.emulateMedia({ colorScheme: theme });
    await stubClose(page);
    await (await open(page, baseURL)).click();
    await expect(page.getByRole("dialog")).toContainText("Closing ends the order");
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
