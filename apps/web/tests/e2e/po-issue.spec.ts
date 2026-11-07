import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { poId } from "./po-screens-fixture";
import { contactIds, stubIssue } from "./po-issue-fixture";

test.use({ viewport: { width: 1400, height: 1000 } });

const reduce = (page: Page) => page.emulateMedia({ reducedMotion: "reduce" });
const openDetail = async (page: Page, baseURL: string | undefined) => {
  await page.context().addCookies([{ name: "flo_csrf", value: "test-csrf", url: baseURL ?? "http://localhost" }]);
  await page.goto(`/purchase-orders/${poId}`);
  await page.getByRole("heading", { name: "PO-0007", level: 2 }).waitFor();
};
const openDialog = async (page: Page, baseURL: string | undefined) => {
  await openDetail(page, baseURL);
  const trigger = page.getByRole("button", { name: "Issue", exact: true });
  await trigger.click();
  return { trigger, dialog: page.getByRole("dialog") };
};
/** Issue and land on the moment. */
const issueNow = async (page: Page, baseURL: string | undefined) => {
  const { dialog } = await openDialog(page, baseURL);
  await dialog.getByRole("button", { name: "Issue", exact: true }).click();
  // The dev server transforms the moment's modules on first visit; allow for a loaded host.
  await expect(page).toHaveURL(new RegExp(`/purchase-orders/${poId}/issue$`), { timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "Result" })).toBeVisible({ timeout: 20_000 });
};

test("the dialog lists recipients, version, templates, the vendor copy, what stays here and the commitment; Escape returns focus", async ({ page, baseURL }) => {
  await stubIssue(page);
  const { trigger, dialog } = await openDialog(page, baseURL);
  await expect(dialog).toContainText("Dock");
  await expect(dialog).toContainText("dock@example.test");
  await expect(dialog).toContainText("v2");
  await expect(dialog).toContainText("po-issued-v1");
  await expect(dialog).toContainText("po-v1");
  await expect(dialog.getByRole("cell", { name: "Pump 1" })).toBeVisible();
  await expect(dialog).toContainText("Deliver to dock 4");
  for (const word of ["Chartfields", "Budget figures", "Internal notes"]) await expect(dialog).toContainText(word);
  await expect(dialog).toContainText("Issuing commits");
  await expect(dialog).toContainText("P-100 Plant 4");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("no recipient set: the dialog says where the email goes", async ({ page, baseURL }) => {
  await stubIssue(page, { recipients: [] });
  const { dialog } = await openDialog(page, baseURL);
  await expect(dialog).toContainText("No recipient is set. Issuing sends to the vendor's document contacts and stops if it has none.");
});

test("a budget 403 reads exactly the operator's sentence and Issue still issues", async ({ page, baseURL }) => {
  const state = await stubIssue(page, { budget: "forbidden" });
  const { dialog } = await openDialog(page, baseURL);
  await expect(dialog).toContainText("Budget figures are not visible to you.");
  await dialog.getByRole("button", { name: "Issue", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/issue$`));
  expect(state.issuePosts).toHaveLength(1);
});

test("a double click sends one POST with CSRF, If-Match and an Idempotency-Key; the moment reads Email Queued", async ({ page, baseURL }) => {
  const state = await stubIssue(page);
  const { dialog } = await openDialog(page, baseURL);
  await dialog.getByRole("button", { name: "Issue", exact: true }).dblclick();
  await expect(page).toHaveURL(new RegExp(`/purchase-orders/${poId}/issue$`));
  await expect(page.getByRole("heading", { name: "Result" })).toBeVisible();
  expect(state.issuePosts).toHaveLength(1);
  const [sent] = state.issuePosts;
  expect(sent!.headers["x-csrf-token"]).toBe("test-csrf");
  expect(sent!.headers["if-match"]).toBe("3");
  expect(sent!.headers["idempotency-key"]).toBeTruthy();
  await expect(page.getByRole("status").filter({ hasText: "on its way" })).toBeVisible();
  await expect(page.locator(".po-result")).toContainText("Email Queued");
  await expect(page.getByRole("table", { name: "Frames" })).toBeVisible();
});

test("a retry after a network failure sends the same Idempotency-Key", async ({ page, baseURL }) => {
  const state = await stubIssue(page, { abortFirst: true });
  const { dialog } = await openDialog(page, baseURL);
  const go = dialog.getByRole("button", { name: "Issue", exact: true });
  await go.click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await go.click();
  await expect(page).toHaveURL(/\/issue$/);
  expect(state.issuePosts).toHaveLength(2);
  expect(state.issuePosts[1]!.headers["idempotency-key"]).toBe(state.issuePosts[0]!.headers["idempotency-key"]);
});

test("409 stale_version: an alert with Reload, the URL unchanged", async ({ page, baseURL }) => {
  await stubIssue(page, { stale: true });
  const { dialog } = await openDialog(page, baseURL);
  await dialog.getByRole("button", { name: "Issue", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("This order changed since you opened it.");
  await expect(dialog.getByRole("button", { name: "Reload" })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/purchase-orders/${poId}$`));
});

test("bounced, vendor visible: Change Recipient is focused and first, Resend second; a chosen contact is sent", async ({ page, baseURL }) => {
  await reduce(page);
  const state = await stubIssue(page, { sequence: [["bounced"]] });
  await issueNow(page, baseURL);
  const change = page.getByRole("button", { name: "Change Recipient" });
  await expect(change).toBeFocused();
  await expect(page.locator(".po-actions button").first()).toHaveText("Change Recipient");
  await expect(page.getByRole("button", { name: "Resend", exact: true })).toBeVisible();
  await change.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("The next send goes only to the contacts you choose.");
  await dialog.getByLabel(/Office/).check();
  await dialog.getByRole("button", { name: "Send to These Contacts" }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.resends[0]!.body).toEqual({ contact_ids: [contactIds.office] });
  expect(state.resends[0]!.headers["idempotency-key"]).toBeTruthy();
  await expect(page.locator(".po-result")).toContainText("Email Queued");
});

test("bounced, vendor hidden: no Change Recipient, Resend focused, and no contacts request", async ({ page, baseURL }) => {
  await reduce(page);
  const state = await stubIssue(page, { vendorStatus: null, sequence: [["bounced"]] });
  await issueNow(page, baseURL);
  await expect(page.getByRole("button", { name: "Resend", exact: true })).toBeFocused();
  await expect(page.getByRole("button", { name: "Change Recipient" })).toHaveCount(0);
  expect(state.contactGets).toBe(0);
});

test("complained, vendor visible: Change Recipient only; vendor hidden: no action, the message is focused", async ({ page, baseURL }) => {
  await reduce(page);
  await stubIssue(page, { sequence: [["complained"]] });
  await issueNow(page, baseURL);
  await expect(page.getByRole("button", { name: "Change Recipient" })).toBeFocused();
  await expect(page.getByRole("button", { name: "Resend", exact: true })).toHaveCount(0);
});

test("complained, vendor hidden: no action and the failure message takes focus", async ({ page, baseURL }) => {
  await reduce(page);
  await stubIssue(page, { vendorStatus: null, sequence: [["complained"]] });
  await issueNow(page, baseURL);
  const message = page.locator(".po-recovery [tabindex='-1']");
  await expect(message).toBeFocused();
  await expect(message).toContainText("Someone who can view this vendor's contacts can change the recipient.");
  await expect(page.locator(".po-actions button")).toHaveCount(0);
});

for (const mode of ["ok", "empty", "error"] as const) {
  test(`Change Recipient (${mode} contacts): focus is inside the dialog on open${mode === "ok" ? ", then on the first contact" : ""}`, async ({ page, baseURL }) => {
    await reduce(page);
    await stubIssue(page, { sequence: [["bounced"]], contacts: mode });
    await issueNow(page, baseURL);
    await page.getByRole("button", { name: "Change Recipient" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    if (mode === "ok") await expect(dialog.getByLabel(/Dock/)).toBeFocused();
    else await expect(dialog.getByRole("button", { name: mode === "error" ? "Try again" : "Cancel" })).toBeVisible();
    await expect.poll(() => page.evaluate(() => !!document.activeElement?.closest("[role='dialog']"))).toBe(true);
  });
}

test("failed: Resend is focused and its body is {}", async ({ page, baseURL }) => {
  await reduce(page);
  const state = await stubIssue(page, { sequence: [["failed"]] });
  await issueNow(page, baseURL);
  const resend = page.getByRole("button", { name: "Resend", exact: true });
  await expect(resend).toBeFocused();
  await expect(page.getByText("Sends the same version again. Nothing more is committed.")).toBeVisible();
  await resend.click();
  await expect(page.locator(".po-result")).toContainText("Email Queued");
  expect(state.resends).toHaveLength(1);
  expect(state.resends[0]!.body).toEqual({});
});

test("polling continues while any recipient is queued and stops once every row is terminal", async ({ page, baseURL }) => {
  await reduce(page);
  await page.clock.install();
  const state = await stubIssue(page, { recipients: ["Dock", "Office"], sequence: [["delivered", "queued"], ["delivered", "queued"], ["delivered", "delivered"]] });
  await openDetail(page, baseURL);
  await page.getByRole("button", { name: "Issue", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Issue", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Result" })).toBeVisible();
  const gets = () => state.transmissionGets;
  const before = gets();
  await page.clock.fastForward(5_100);
  await expect.poll(gets).toBeGreaterThan(before);
  // Advance the fake clock until the poll after it has landed; each tick is one 5 s poll.
  await expect(async () => { await page.clock.fastForward(5_100); await expect(page.getByRole("status").filter({ hasText: "The email was delivered." })).toBeVisible({ timeout: 1_000 }); }).toPass({ timeout: 20_000 });
  await expect(page.getByRole("status").filter({ hasText: "The email was delivered." })).toBeVisible();
  const settled = gets();
  await page.clock.fastForward(30_000);
  await page.waitForTimeout(200);
  expect(gets()).toBe(settled);
});

test("rows of another revision or the cancellation email are not part of the current send", async ({ page, baseURL }) => {
  await reduce(page);
  await stubIssue(page, { sequence: [["delivered"]], noise: true });
  await issueNow(page, baseURL);
  await expect(page.locator(".po-result")).toContainText("Email Delivered");
  await expect(page.locator(".po-recovery")).toHaveCount(0);
  await expect(page.locator(".po-result tbody tr")).toHaveCount(1);
});

test("reduced motion: no animation is created, the finished picture, its note and the Frames table show", async ({ page, baseURL }) => {
  await reduce(page);
  await stubIssue(page);
  await issueNow(page, baseURL);
  // Polled: a shell transition from the route change may still be settling; the stage creates none.
  await expect.poll(() => page.evaluate(() => document.getAnimations().length)).toBe(0);
  await expect(page.locator(".po-stage").evaluate(el => el.getAnimations({ subtree: true }).length)).resolves.toBe(0);
  await expect(page.getByText("Animation is off: this is the finished picture.")).toBeVisible();
  await expect(page.locator(".po-frames tr[aria-current='step']")).toHaveCount(1);
  await expect(page.locator(".po-frames tbody tr")).toHaveCount(3);
});

test("motion on: each frame animates from its old look to its new one, the frames show in order, and the result waits for the last", async ({ page, baseURL }) => {
  await stubIssue(page);
  const { dialog } = await openDialog(page, baseURL);
  await page.evaluate(() => {
    const w = window as unknown as { __frames: string[]; __keyframes: number[] };
    w.__frames = []; w.__keyframes = [];
    // Three frames at 260ms finish before the URL wait and poll below; slow them so "not before the last frame" is observable.
    document.documentElement.style.setProperty("--dur-slow", "2000ms");
    const note = () => {
      const row = document.querySelector(".po-frames tr[aria-current='step'] td");
      if (row && w.__frames.at(-1) !== row.textContent) w.__frames.push(row.textContent ?? "");
      for (const a of document.getAnimations()) w.__keyframes.push((a.effect as KeyframeEffect).getKeyframes().length);
    };
    new MutationObserver(note).observe(document.body, { subtree: true, attributes: true, childList: true });
    const tick = () => { note(); requestAnimationFrame(tick); };
    tick();
  });
  await dialog.getByRole("button", { name: "Issue", exact: true }).click();
  await expect(page).toHaveURL(/\/issue$/, { timeout: 20_000 });
  await expect.poll(() => page.evaluate(() => document.getAnimations().length)).toBeGreaterThan(0);
  await expect(page.getByRole("heading", { name: "Result" })).toHaveCount(0); // not before the last frame ends
  await expect(page.getByRole("heading", { name: "Result" })).toBeVisible({ timeout: 20_000 });
  const seen = await page.evaluate(() => { const w = window as unknown as { __frames: string[]; __keyframes: number[] }; return { __frames: w.__frames, __keyframes: w.__keyframes }; });
  expect(seen.__frames).toEqual(["1", "2", "3"]);
  expect(seen.__keyframes.length).toBeGreaterThan(0);
  expect(seen.__keyframes.every(n => n === 2)).toBe(true);
});

test("the moment plays once per session: a second visit shows the finished picture", async ({ page, baseURL }) => {
  await stubIssue(page);
  await issueNow(page, baseURL);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Result" })).toBeVisible();
  expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
});

test("an unissued PO shows no stage", async ({ page, baseURL }) => {
  await stubIssue(page);
  await page.context().addCookies([{ name: "flo_csrf", value: "test-csrf", url: baseURL ?? "http://localhost" }]);
  await page.goto(`/purchase-orders/${poId}/issue`);
  await expect(page.getByText("This purchase order has not been issued.")).toBeVisible();
  await expect(page.locator(".po-stage")).toHaveCount(0);
});

test("keyboard only: Issue, confirm, then recovery by Enter", async ({ page, baseURL }) => {
  await reduce(page);
  const state = await stubIssue(page, { sequence: [["failed"]] });
  await openDetail(page, baseURL);
  await page.getByRole("button", { name: "Issue", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Not Yet" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("dialog").getByRole("button", { name: "Issue", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Resend", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator(".po-result")).toContainText("Email Queued");
  expect(state.resends).toHaveLength(1);
});

for (const theme of ["light", "dark"] as const) {
  test(`dialog, moment and Change Recipient pass axe in ${theme}`, async ({ page, baseURL }) => {
    test.setTimeout(90_000); // three axe passes on a loaded host
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await stubIssue(page, { sequence: [["bounced"]] });
    const { dialog } = await openDialog(page, baseURL);
    await expect(dialog).toContainText("Deliver to dock 4");
    await expect(dialog).toContainText("Issuing commits");
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await dialog.getByRole("button", { name: "Issue", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Result" })).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.getByRole("button", { name: "Change Recipient" }).click();
    await expect(page.getByRole("dialog").getByLabel(/Dock/)).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
