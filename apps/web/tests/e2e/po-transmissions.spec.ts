import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { contactIds, poId, stubPo, tx } from "./po-screens-fixture";

test.use({ viewport: { width: 1400, height: 1000 } });

const BOUNCE = "Hard bounce: the address does not accept mail";
const open = async (page: Page, baseURL: string | undefined) => {
  await page.context().addCookies([{ name: "flo_csrf", value: "test-csrf", url: baseURL ?? "http://localhost" }]);
  await page.goto(`/purchase-orders/${poId}`);
  await page.getByRole("heading", { name: "PO-0007", level: 2 }).waitFor();
  await page.getByRole("heading", { name: "Send log" }).waitFor();
};
const actions = (page: Page) => page.locator(".vendor-head-actions");

test("bounced: Change Recipient then Resend; a suppressed contact is marked; sending to another posts contact_ids and adds attempt 2", async ({ page, baseURL }) => {
  const state = await stubPo(page, { transmissions: [tx("bounced", { result_reason: BOUNCE })] });
  await open(page, baseURL);
  const names = await actions(page).getByRole("button", { name: /^(Change Recipient|Resend)$/ }).allTextContents();
  expect(names).toEqual(["Change Recipient", "Resend"]);
  await expect(page.getByText("Sends the same version again. Nothing more is committed.")).toBeVisible();
  await expect(page.getByText(BOUNCE)).toBeVisible();
  await expect(page.getByText("dock@example.test does not accept mail. It is on the suppression list and is never retried silently.")).toBeVisible();
  await expect(page.getByText("An organization administrator can remove an address under Administration, Sending Domain.")).toBeVisible();
  await page.getByRole("button", { name: "Change Recipient" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Suppressed: never sent")).toHaveCount(1);
  await expect(dialog.getByLabel(/Dock/).locator("xpath=..")).toContainText("Suppressed: never sent");
  await dialog.getByLabel(/Office/).check();
  await dialog.getByRole("button", { name: "Send to These Contacts" }).click();
  await expect(dialog).toBeHidden();
  expect(state.resends).toHaveLength(1);
  expect(state.resends[0]!.body).toEqual({ contact_ids: [contactIds.office] });
  expect(state.resends[0]!.headers["idempotency-key"]).toBeTruthy();
  await expect(page.getByRole("table", { name: "Every email sent for this purchase order" }).getByRole("row")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "Details for office@example.test, attempt 2" })).toBeVisible();
  await expect(page.locator(".vendor-head").getByText("Issued", { exact: true })).toBeVisible();
  expect(state.writes).toEqual([`POST /api/v1/purchase-orders/${poId}/transmissions/resend`]);
});

test("Resend posts {} and the log gains attempt 2", async ({ page, baseURL }) => {
  const state = await stubPo(page, { transmissions: [tx("failed")] });
  await open(page, baseURL);
  await actions(page).getByRole("button", { name: "Resend", exact: true }).click();
  await expect(page.getByRole("button", { name: "Details for dock@example.test, attempt 2" })).toBeVisible();
  expect(state.resends[0]!.body).toEqual({});
});

for (const [mode, detail] of [["403", "You may not resend this purchase order."], ["409", "An email for this order is still being sent."]] as const) {
  test(`a ${mode} on Resend renders the server's words and clears the key`, async ({ page, baseURL }) => {
    const state = await stubPo(page, { transmissions: [tx("failed")], resend: mode });
    await open(page, baseURL);
    const button = actions(page).getByRole("button", { name: "Resend", exact: true });
    await button.click();
    await expect(actions(page).getByRole("alert")).toContainText(detail);
    await button.click();
    await expect.poll(() => state.resends.length).toBe(2);
    expect(state.resends[0]!.headers["idempotency-key"]).not.toEqual(state.resends[1]!.headers["idempotency-key"]);
  });
}

test("no vendor.view: Resend only, no Change Recipient and no contacts call", async ({ page, baseURL }) => {
  const state = await stubPo(page, { vendorStatus: null, transmissions: [tx("bounced", { result_reason: BOUNCE })] });
  await open(page, baseURL);
  await expect(actions(page).getByRole("button", { name: "Resend", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Change Recipient" })).toHaveCount(0);
  expect(state.paths.filter(p => p.includes("/contacts"))).toHaveLength(0);
});

test("no vendor.view and a complaint: no action, the words still say it is never retried silently", async ({ page, baseURL }) => {
  await stubPo(page, { vendorStatus: null, transmissions: [tx("complained")] });
  await open(page, baseURL);
  await expect(actions(page).getByRole("button", { name: /Resend|Change Recipient/ })).toHaveCount(0);
  await expect(page.getByText(/reported this email as spam\. It is on the suppression list and is never retried silently/)).toBeVisible();
});

test("a cancelled PO with a bounced cancellation notice offers recovery", async ({ page, baseURL }) => {
  await stubPo(page, { status: "cancelled", transmissions: [tx("delivered"), tx("bounced", { attempt: 2, template_version: "po-cancelled-v1" })] });
  await open(page, baseURL);
  await expect(actions(page).getByRole("button", { name: "Change Recipient" })).toBeVisible();
});

test("a superseded PO with a bounced row offers no recovery", async ({ page, baseURL }) => {
  await stubPo(page, { status: "superseded", transmissions: [tx("bounced")] });
  await open(page, baseURL);
  await expect(actions(page).getByRole("button", { name: /Resend|Change Recipient/ })).toHaveCount(0);
});

test("every result renders an icon and its words in the log", async ({ page, baseURL }) => {
  const words: Record<string, string> = { queued: "Email Queued", sent: "Email Sent", delivered: "Email Delivered", failed: "Email Failed", bounced: "Email Bounced", complained: "Email Complaint", address_suppressed: "Address Suppressed" };
  const results = Object.keys(words);
  await stubPo(page, { transmissions: results.map((r, i) => tx(r, { attempt: 1, id: `r${i}`, recipient_email: `${r}@example.test`, result_reason: r === "failed" ? "The email provider could not deliver the email" : null })) });
  await open(page, baseURL);
  const rows = page.getByRole("table", { name: "Every email sent for this purchase order" }).locator("tbody tr");
  for (const [i, r] of results.entries()) {
    await expect(rows.nth(i)).toContainText(words[r]!);
    await expect(rows.nth(i).locator("svg").first()).toBeVisible();
  }
  await expect(page.getByText("The email provider could not deliver the email")).toBeVisible();
});

test("keyboard: Details opens the sheet, focus stays inside, Escape returns to the button", async ({ page, baseURL }) => {
  await stubPo(page, { transmissions: [tx("bounced", { result_reason: BOUNCE, provider_message_id: "msg-1" })] });
  await open(page, baseURL);
  const details = page.getByRole("button", { name: "Details for dock@example.test, attempt 1" });
  await details.focus();
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog", { name: "Email to dock@example.test" });
  await expect(sheet).toContainText("msg-1");
  await expect(sheet).toContainText("po-issued-v1");
  for (let i = 0; i < 4; i++) { await page.keyboard.press("Tab"); expect(await sheet.evaluate(el => el.contains(document.activeElement))).toBe(true); }
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(details).toBeFocused();
});

for (const theme of ["light", "dark"] as const) {
  test(`the bounced log and its sheet pass axe in ${theme}`, async ({ page, baseURL }) => {
    test.setTimeout(90_000);
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await stubPo(page, { transmissions: [tx("bounced", { result_reason: BOUNCE })] });
    await open(page, baseURL);
    await page.waitForFunction(() => document.getAnimations().length === 0); // the theme's colour transition has finished
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.getByRole("button", { name: /^Details for/ }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.waitForFunction(() => document.getAnimations().length === 0);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
