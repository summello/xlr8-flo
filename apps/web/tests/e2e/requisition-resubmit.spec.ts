import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { justificationChange, quantityChange, stubChanges, type ChangeSet, type Submit } from "./requisition-changes-fixture";
import { instanceId, lineRow, requisition, reservation, reqId } from "./requisitions-fixture";

const returned = () => requisition({ estimate: "196000.0000", reservation: reservation("196000.0000"), approval: { instance_id: instanceId, status: "returned" }, version: 3 });
const open = async (page: Page, changes: ChangeSet = quantityChange) => {
  const state = await stubChanges(page, { row: returned(), lines: [lineRow(1)], changes });
  await page.goto(`/requisitions/${reqId}/resubmit`);
  await expect(page.getByRole("heading", { name: "Note to the Approver" })).toBeVisible();
  return state;
};
const send = (page: Page) => page.locator(".req-action-bar .form-submit");
async function keyboardFocus(page: Page, locator: Locator) {
  for (let i = 0; i < 200; i++) {
    await page.keyboard.press("Tab");
    if (await locator.evaluate(node => node === document.activeElement)) return;
  }
  throw new Error("Control is unreachable by keyboard");
}

test("a justification-only edit is not material and returns to the same step", async ({ page }) => {
  const state = await open(page, justificationChange);
  await expect(page.getByText("Not a Material Change", { exact: true })).toBeVisible();
  await expect(page.getByText("it returns to the step that sent it back, and the approver sees this diff")).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Version 1" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Version 2" })).toBeVisible();
  await expect(page.locator("del")).toHaveText("End of life");
  await expect(page.locator(".req-diff strong")).toHaveText("End of life, and unsafe");
  await send(page).click();
  const done = page.getByRole("status").filter({ hasText: "Resubmitted as Version 2" });
  await expect(done).toBeFocused();
  await expect(done).toContainText("Now with amara@example.test at Manager review");
  await expect(done.getByRole("link", { name: "Back to Requisition" })).toBeVisible();
  expect(state.calls.find(c => c.method === "POST")?.body).toMatchObject({ to: "approval_pending" });
});

test("a quantity change shows the server's money delta, a material change, restarts and the funding outcome", async ({ page }) => {
  const state = await open(page);
  await expect(page.getByText("Down 3,500.00 USD (2%)")).toBeVisible();
  // The quantity row is a plain number: 10 and 9, never money or a currency.
  const quantity = page.locator(".req-diff tbody tr", { hasText: "Line 1 quantity" });
  await expect(quantity.locator("del")).toHaveText("10");
  await expect(quantity.locator("strong")).toHaveText("9");
  await expect(quantity).not.toContainText("USD");
  await expect(page.locator(".req-diff tbody tr", { hasText: "Estimate" }).locator("del")).toContainText("196,000.00");
  await expect(page.getByText("Material Change", { exact: true })).toBeVisible();
  await expect(page.getByText(/approval restarts from the first step/)).toBeVisible();
  const funding = page.locator("#funding-check");
  await expect(funding).toContainText("Released Once");
  await expect(funding).toContainText("196,000.00");
  await expect(funding).toContainText("192,500.00");
  await expect(funding).toContainText("43,500.00");
  for (const word of ["Changed", "Added"]) await expect(page.locator(".req-diff .req-pill", { hasText: word }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Versions" })).toBeVisible();
  await expect(page.getByText("Version 1 · Submission · Returned")).toBeVisible();
  await expect(page.locator(".req-history li").first()).toContainText("dev@example.test");
  await page.getByRole("textbox", { name: "Note" }).fill("Quantity corrected");
  await send(page).click();
  await expect(page.getByRole("status").filter({ hasText: "Resubmitted as Version 2" })).toBeFocused();
  const post = state.calls.find(c => c.method === "POST")!;
  expect(post.body).toEqual({ to: "approval_pending", note: "Quantity corrected" });
  expect(post.headers["if-match"]).toBe("3");
  expect(post.headers["idempotency-key"]).toBeTruthy();
});

test("the server decides materiality: a quantity change the server calls not material is shown as not material", async ({ page }) => {
  await open(page, { ...quantityChange, material: false, routing: "same_step" });
  await expect(page.getByText("Not a Material Change", { exact: true })).toBeVisible();
  await expect(page.getByText("Material Change", { exact: true })).toHaveCount(0);
});

const alerts: { submit: Submit; text: string | RegExp; link?: string }[] = [
  { submit: "nothing_changed", text: "Not resubmitted. Nothing has changed." },
  { submit: "stale_version", text: "This requisition changed since you opened it. Reload to see the current version." },
  { submit: "invalid", text: "Required field: Explain why it is needed", link: "#req-justification" },
  { submit: "funds", text: /Funds: a shortfall of 62,000\.00 USD/, link: "#funding-check" },
];
for (const a of alerts) {
  test(`${a.submit} renders an alert, focuses it and keeps the typed note`, async ({ page }) => {
    const state = await open(page);
    state.submit = a.submit;
    await page.getByRole("textbox", { name: "Note" }).fill("Please look again");
    await send(page).click();
    const alert = page.getByRole("alert").filter({ hasText: a.text });
    await expect(alert).toBeFocused();
    await expect(page.getByRole("textbox", { name: "Note" })).toHaveValue("Please look again");
    if (a.link) await expect(alert.getByRole("link")).toHaveAttribute("href", `/requisitions/${reqId}/edit${a.link}`);
    if (a.submit === "stale_version") {
      state.submit = "ok";
      await alert.getByRole("button", { name: "Reload" }).click();
      await expect(page.getByRole("textbox", { name: "Note" })).toHaveValue("Please look again");
    }
  });
}

test("Resubmit for Approval is never disabled: in flight it is aria-busy and a second click does not send twice", async ({ page }) => {
  const state = await open(page);
  state.delay = 400;
  await send(page).click();
  await expect(send(page)).toHaveAttribute("aria-busy", "true");
  await expect(send(page)).toBeEnabled();
  await send(page).click({ force: true });
  await expect(page.getByRole("status").filter({ hasText: "Resubmitted as Version 2" })).toBeVisible();
  expect(state.calls.filter(c => c.method === "POST")).toHaveLength(1);
});

test("loading error says what happened, shows a reference, and recovers with Try again", async ({ page }) => {
  const state = await stubChanges(page, { row: returned(), lines: [lineRow(1)] });
  state.changesFail = true;
  await page.goto(`/requisitions/${reqId}/resubmit`);
  await expect(page.getByRole("heading", { name: "Could not load the changes" })).toBeVisible();
  await expect(page.getByText(/Reference ref-4321/)).toBeVisible();
  await expect(send(page)).toBeVisible();
  state.changesFail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "What Changed" })).toBeVisible();
});

test("no rows shows the empty state with a link back to the editor, and Resubmit reports nothing changed", async ({ page }) => {
  const state = await open(page, { ...quantityChange, rows: [], funding: null, nothing_changed: true });
  await expect(page.getByRole("heading", { name: "Nothing has changed yet" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to the editor" }).first()).toHaveAttribute("href", `/requisitions/${reqId}/edit`);
  state.submit = "nothing_changed";
  await send(page).click();
  await expect(page.getByRole("alert").filter({ hasText: "Not resubmitted. Nothing has changed." })).toBeFocused();
});

test("a project available that could not be checked says so while the diff and submit still work", async ({ page }) => {
  await open(page, { ...quantityChange, funding: { ...quantityChange.funding!, project_available_after: null } });
  await expect(page.locator("#funding-check")).toContainText("could not be checked");
  await expect(page.getByRole("heading", { name: "What Changed" })).toBeVisible();
  await expect(send(page)).toBeEnabled();
});

test("keyboard only: returned banner, review, resubmit", async ({ page }) => {
  await stubChanges(page, { row: returned(), lines: [lineRow(1)] });
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.getByRole("heading", { name: "Returned for Changes" })).toBeVisible();
  const review = page.getByRole("link", { name: /Review and Resubmit/ });
  await keyboardFocus(page, review);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "What Changed" })).toBeVisible();
  await keyboardFocus(page, send(page));
  await expect.poll(() => send(page).evaluate(node => getComputedStyle(node).outlineStyle)).not.toBe("none");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status").filter({ hasText: "Resubmitted as Version 2" })).toBeFocused();
});

for (const theme of ["light", "dark"] as const) {
  test(`resubmit passes axe in ${theme} theme`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await open(page);
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
}

test("below 520px of card width each row stacks and keeps its column names", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await open(page);
  const cell = page.locator(".req-diff tbody tr").first().locator("td").first();
  await expect(cell).toHaveAttribute("data-label", "Version 1");
  expect(await cell.evaluate(node => getComputedStyle(node).display)).toBe("block");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
