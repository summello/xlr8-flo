import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { existingId, stubRequest } from "./vendor-request-fixture";

// The fleet runs several suites on one host; a form that fills a dozen fields needs more than 30 s at load 20+.
test.describe.configure({ timeout: 90_000 });

const fill = async (page: Page) => {
  await page.getByLabel("Trading name").fill("Acme Tooling");
  await page.getByLabel("Legal name").fill("Acme Tooling Incorporated");
  for (const [type, who] of [["billing", "Bea"], ["service_delivery", "Sam"]] as const) {
    await page.locator(`#vr-${type}-name`).fill(who);
    await page.locator(`#vr-${type}-email`).fill(`${who.toLowerCase()}@example.test`);
  }
};
const submit = (page: Page) => page.getByRole("button", { name: "Submit for Approval" });

test("fill, save draft, reload keeps the values, submit shows the submitted state", async ({ page }) => {
  const state = await stubRequest(page);
  await page.goto("/vendors/new");
  await page.getByLabel("Trading name").fill("Acme Tooling");
  await page.getByLabel("Legal name").fill("Acme Tooling Incorporated");
  await page.getByLabel("Tax id", { exact: true }).fill("12-3456789");
  await page.getByLabel("Tax id country").fill("us");
  await page.getByRole("button", { name: "Save Draft" }).click();
  await expect(page.getByText("Draft saved.")).toBeVisible();
  await expect(page).toHaveURL(/\/vendors\/[0-9a-f-]{36}\/edit$/);
  const draftId = /\/vendors\/([0-9a-f-]{36})\/edit$/.exec(page.url())![1];
  // correcting only the country is a change too: the second save carries it
  await page.getByLabel("Tax id country").fill("gb");
  await page.getByRole("button", { name: "Save Draft" }).click();
  await expect.poll(() => state.sensitiveBodies.length).toBe(2);
  expect(state.sensitiveBodies[1]).toMatchObject({ tax_id: "12-3456789", tax_id_country: "GB" });
  await fill(page);
  await page.locator("#vr-q-size").fill("40");
  await page.getByRole("button", { name: "Save Draft" }).click();
  await expect(page.getByText("Draft saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Trading name")).toHaveValue("Acme Tooling");
  await expect(page.locator("#vr-billing-email")).toHaveValue("bea@example.test");
  await expect(page.locator("#vr-q-size")).toHaveValue("40");
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible(); // the required types come back with the draft
  await expect(page.getByText("INS", { exact: true })).toBeVisible();
  // write-only: the saved tax id is never put back into the form, the URL or the title
  await expect(page.getByLabel("Tax id", { exact: true })).toHaveValue("");
  await expect(page.getByText("A tax id is saved and is not shown again")).toBeVisible();
  expect(page.url()).not.toContain("3456789");
  expect(await page.title()).not.toContain("3456789");
  await submit(page).click();
  await expect(page.getByRole("heading", { name: "VEN-0100 Submitted for Approval" })).toBeVisible();
  expect(state.submitted).toBe(true);
  // a submitted request reopens read-only: no Save Draft, no Submit, a way to the vendor
  await page.goto(`/vendors/${draftId}/edit`);
  await expect(page.getByText("was submitted for approval and can no longer be edited here")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save Draft" })).toHaveCount(0);
  await expect(submit(page)).toHaveCount(0);
  await expect(page.getByRole("link", { name: "View vendor" })).toBeVisible();
});

test("a duplicate shows at the field; Use Existing Vendor navigates; a requester has no override", async ({ page }) => {
  const state = await stubRequest(page, { submit: "duplicate" });
  await page.goto("/vendors/new");
  const tax = page.getByLabel("Tax id", { exact: true });
  await tax.pressSequentially("99-999");
  expect(state.checks).toHaveLength(0); // no request per keystroke
  await tax.blur();
  const signal = page.getByRole("status").filter({ hasText: "Already used by Old Co (VEN-0009)." });
  await expect(signal).toBeVisible();
  expect(state.checks).toHaveLength(1);
  await expect(page.getByRole("button", { name: "Override with a Reason" })).toHaveCount(0);
  page.once("dialog", d => void d.accept()); // leaving typed, unsaved input asks first
  await page.getByRole("link", { name: "Use Existing Vendor" }).click();
  await expect(page).toHaveURL(new RegExp(`/vendors/${existingId}$`));
});

test("a vendor manager overrides a duplicate with a reason and the submit succeeds", async ({ page }) => {
  const state = await stubRequest(page, { approver: true, submit: "duplicate" });
  await page.goto("/vendors/new");
  await fill(page);
  await page.getByLabel("Tax id", { exact: true }).fill("99-999");
  await page.getByLabel("Tax id country").fill("US");
  await page.getByLabel("Tax id country").blur();
  await page.getByRole("button", { name: "Save Draft" }).click();
  await page.locator("#vr-q-size").fill("40");
  await page.getByRole("button", { name: "Override with a Reason" }).click();
  await page.getByLabel("Reason for overriding").fill("Same firm, new site");
  await submit(page).click();
  await expect(page.getByRole("heading", { name: "VEN-0100 Submitted for Approval" })).toBeVisible();
  expect(state.submitBody).toEqual({ override_reason: "Same firm, new site" });
});

test("submit is never disabled; an empty submit gives a focused summary whose links focus the fields", async ({ page }) => {
  await stubRequest(page);
  await page.goto("/vendors/new");
  await expect(submit(page)).toBeEnabled();
  await expect(page.getByRole("button", { name: "Save Draft" })).toBeEnabled();
  await expect(page.getByText("Required fields are still empty")).toBeVisible();
  await page.getByLabel("Trading name").fill("Typed and kept");
  await submit(page).click();
  const summary = page.getByRole("alert").filter({ hasText: "before you submit" });
  await expect(summary).toBeFocused();
  await expect(submit(page)).toBeEnabled();
  await summary.getByRole("link", { name: /Contacts: Enter the Billing contact name/ }).click();
  await expect(page.locator("#vr-billing-name")).toBeFocused();
  await summary.getByRole("link", { name: /Company: Enter the legal name/ }).click();
  await expect(page.getByLabel("Legal name")).toBeFocused();
  await expect(page.getByLabel("Trading name")).toHaveValue("Typed and kept");
});

test("server 422s land on their fields and 409 workflow_not_set_up reads in words", async ({ page }) => {
  await stubRequest(page, { submit: "workflow" });
  await page.goto("/vendors/new");
  await fill(page);
  await page.getByRole("button", { name: "Save Draft" }).click();
  await page.locator("#vr-q-size").fill("many");
  await page.getByRole("button", { name: "Save Draft" }).click();
  const summary = page.getByRole("alert").filter({ hasText: "Fix 1 thing" });
  await summary.getByRole("link", { name: /Questionnaire: Enter a number/ }).click();
  await expect(page.locator("#vr-q-size")).toBeFocused();
  await expect(page.locator("#vr-q-size")).toHaveValue("many");
  await page.locator("#vr-q-size").fill("12");
  await submit(page).click();
  await expect(page.getByRole("alert")).toContainText("Vendors cannot be submitted yet: no approval workflow is published. Your draft is saved. Ask an administrator to set one up.");
});

test("the rendered request form carries no weight or points value", async ({ page }) => {
  await stubRequest(page);
  await page.goto("/vendors/new");
  await page.getByLabel("Trading name").fill("A");
  await page.getByLabel("Legal name").fill("A");
  await page.getByRole("button", { name: "Save Draft" }).click();
  await expect(page.locator("#vr-q-size")).toBeVisible();
  const html = await page.locator(".vendor-request").innerHTML();
  expect(html).not.toMatch(/weight|points/i);
  expect(await page.locator(".vendor-request").innerText()).not.toMatch(/\b40\b/);
});

test("a requester choosing Selected units sees the unit options", async ({ page }) => {
  await stubRequest(page);
  await page.goto("/vendors/new");
  await page.getByLabel("Selected units").check();
  await expect(page.getByRole("option", { name: "Plant 4" })).toBeAttached();
});

test("a units load failure is a field error with Try again, never an empty list", async ({ page }) => {
  const state = await stubRequest(page, { unitsFail: true });
  await page.goto("/vendors/new");
  await page.getByLabel("Selected units").check();
  await expect(page.getByText(/Loading the units failed|Forbidden/)).toBeVisible();
  await expect(page.getByRole("option")).toHaveCount(0); // no silent empty list
  state.unitsHealed = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("option", { name: "Plant 4" })).toBeAttached();
});

test("the keyboard reaches every card and the action bar in order", async ({ page }) => {
  await stubRequest(page);
  await page.goto("/vendors/new");
  await page.getByLabel("Trading name").focus();
  const seen: string[] = [];
  for (let i = 0; i < 40; i++) {
    seen.push(await page.evaluate(() => document.activeElement?.id || document.activeElement?.textContent?.trim() || ""));
    if (seen.at(-1)?.includes("Submit for Approval")) break;
    await page.keyboard.press("Tab");
  }
  for (const id of ["vr-trading_name", "vr-legal_name", "vr-tax_id", "vr-billing-name", "vr-service_delivery-email"]) expect(seen).toContain(id);
  expect(seen.at(-1)).toContain("Submit for Approval");
  expect(seen.some(s => s.includes("Save Draft"))).toBe(true);
});

for (const theme of ["light", "dark"] as const) {
  test(`/vendors/new passes axe in ${theme} theme`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await stubRequest(page);
    await page.goto("/vendors/new");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(page.getByLabel("Trading name")).toBeVisible();
    await page.getByLabel("Trading name").fill("A");
    await page.getByLabel("Legal name").fill("A");
    await page.getByRole("button", { name: "Save Draft" }).click();
    await expect(page.locator("#vr-q-size")).toBeVisible();
    await submit(page).click(); // the error summary is part of the route
    await expect(page.getByRole("alert").filter({ hasText: "before you submit" })).toBeFocused();
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
}
