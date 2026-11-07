import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { stubSendingDomain } from "./sending-domain-fixture";

const open = async (page: Page) => {
  await page.goto("/admin/sending-domain");
  await page.getByRole("heading", { name: "Sending Domain", level: 2 }).waitFor();
};

test("failing DKIM, no DMARC signal, no allowance; Check Now flips to passing", async ({ page }) => {
  const state = await stubSendingDomain(page);
  await open(page);
  await expect(page.getByText("DKIM Fails")).toBeVisible();
  await expect(page.getByText("DKIM TXT k._domainkey: failed")).toBeVisible();
  await expect(page.getByText("Purchase Order Email Is Held")).toBeVisible();
  await expect(page.getByText("DMARC is published and verified by hand")).toBeVisible();
  await expect(page.getByText(/allowance|sent today/i)).toHaveCount(0);
  await page.getByRole("button", { name: "Check Now" }).click();
  await expect(page.getByText("Check queued. The result appears here within two minutes.")).toBeVisible();
  expect(state.writes[0]!.headers["idempotency-key"]).toBeTruthy();
  expect(state.writes[0]!.headers["x-csrf-token"]).toBeDefined();
  await expect(page.getByText("DKIM Passes")).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("Checked just now.")).toBeVisible();
});

test("a check inside a minute shows the try-again line", async ({ page }) => {
  await stubSendingDomain(page, { recent: true });
  await open(page);
  await page.getByRole("button", { name: "Check Now" }).click();
  await expect(page.getByText("Checked less than a minute ago. Try again in a minute.")).toBeVisible();
});

test("search and Load More go to the server; Remove needs a reason", async ({ page }) => {
  const state = await stubSendingDomain(page, { count: 60 });
  await open(page);
  await expect(page.getByRole("button", { name: /^Remove .* from the suppression list$/ })).toHaveCount(50);
  await page.getByRole("button", { name: "Load More" }).click();
  await expect(page.getByRole("button", { name: /^Remove .* from the suppression list$/ })).toHaveCount(60);
  expect(state.queries.some(q => q.includes("cursor="))).toBe(true);
  await page.getByLabel("Search by email").fill("user01");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("button", { name: /^Remove .* from the suppression list$/ })).toHaveCount(1);
  expect(state.queries.some(q => q.includes("q=user01"))).toBe(true);

  await page.getByRole("button", { name: "Remove user01@x.com from the suppression list" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByRole("button", { name: "Keep Address" })).toBeFocused();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await dialog.getByRole("button", { name: "Remove Address" }).click();
  await expect(dialog.getByText("Give a reason.")).toBeVisible();
  expect(state.writes.filter(w => w.method === "DELETE")).toHaveLength(0);
  await dialog.getByLabel("Reason").fill("Mailbox fixed");
  await dialog.getByRole("button", { name: "Remove Address" }).click();
  await expect(page.getByText("Removed user01@x.com. Future emails to it are sent again.")).toBeVisible();
  expect(state.writes.at(-1)!.body).toEqual({ reason: "Mailbox fixed" });
  await expect(page.getByRole("button", { name: /^Remove .* from the suppression list$/ })).toHaveCount(0);
});

test("keyboard-only: check, search, remove, Escape returns focus, confirm", async ({ page }) => {
  const state = await stubSendingDomain(page);
  await open(page);
  await page.getByRole("button", { name: "Check Now" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Check queued.")).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Search by email")).toBeFocused();
  await page.keyboard.type("user0");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: /^Remove .* from the suppression list$/ })).toHaveCount(3);
  const remove = page.getByRole("button", { name: "Remove user00@x.com from the suppression list" });
  await remove.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Keep Address" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(remove).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByLabel("Reason").focus();
  await page.keyboard.type("Fixed");
  await page.getByRole("button", { name: "Remove Address" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Removed user00@x.com.")).toBeVisible();
  expect(state.writes.at(-1)!.method).toBe("DELETE");
});
