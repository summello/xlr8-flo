import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { ACME, BOLT, awardId, stubPoCreate } from "./po-create-fixture";

test.use({ viewport: { width: 1400, height: 1000 } });

const url = `/purchase-orders/new?award=${awardId}`;
const qty = (page: Page, line: number, requisition: string) => page.getByLabel(`Quantity to order, line ${line} of ${requisition}`);
const create = (page: Page) => page.getByRole("button", { name: "Create Draft POs" });
const row = (page: Page, text: string) => page.getByRole("row").filter({ hasText: text });

test("an award with two vendors sends one POST per vendor and requisition and lists three links", async ({ page }) => {
  const stub = await stubPoCreate(page);
  await page.goto(url);
  await expect(page.getByRole("heading", { name: "Acme Tooling" })).toBeVisible();
  await expect(qty(page, 1, "REQ-1")).toHaveValue("10");
  await create(page).click();
  const made = page.getByRole("status", { name: "Created purchase orders" });
  await expect(made.getByRole("link")).toHaveCount(3);
  await expect(made).toContainText("Created PO-0001 for Acme Tooling, REQ-1.");
  expect(stub.posts.map(p => [p.body.vendor_id, p.body.lines.map(l => l.rfq_line_id)])).toEqual([[ACME, ["L1"]], [ACME, ["L2"]], [BOLT, ["L3"]]]);
  expect(new Set(stub.posts.map(p => p.key)).size).toBe(3);
  await expect(made).toBeFocused();
});

test("a partial quantity leaves the rest open, the created group blank, and a second press sends nothing for it", async ({ page }) => {
  const stub = await stubPoCreate(page);
  await page.goto(url);
  await qty(page, 2, "REQ-2").fill("");
  await qty(page, 3, "REQ-1").fill("0");
  await qty(page, 1, "REQ-1").fill("4");
  await create(page).click();
  await expect(page.getByRole("status", { name: "Created purchase orders" }).getByRole("link")).toHaveCount(1);
  expect(stub.posts).toHaveLength(1);
  await expect(row(page, "Pump")).toContainText("6");
  await expect(qty(page, 1, "REQ-1")).toHaveValue("");
  await create(page).click();
  await expect(page.getByText("Enter a quantity on at least one line.").first()).toBeVisible();
  expect(stub.posts).toHaveLength(1);
});

test("above open is a field error with nothing sent and the typed values kept; blank everywhere is refused", async ({ page }) => {
  const stub = await stubPoCreate(page);
  await page.goto(url);
  await qty(page, 1, "REQ-1").fill("11");
  await create(page).click();
  await expect(page.getByText("10 open on this line. Order at most that.").first()).toBeVisible();
  await expect(qty(page, 1, "REQ-1")).toHaveValue("11");
  await expect(qty(page, 2, "REQ-2")).toHaveValue("10");
  expect(stub.posts).toHaveLength(0);
  for (const [n, r] of [[1, "REQ-1"], [2, "REQ-2"], [3, "REQ-1"]] as const) await qty(page, n, r).fill("");
  await create(page).click();
  await expect(page.getByText("Enter a quantity on at least one line.").first()).toBeVisible();
  expect(stub.posts).toHaveLength(0);
});

test("a server race shows the server's figure, still creates the other groups and replays the same key on retry", async ({ page }) => {
  const stub = await stubPoCreate(page, { race: { open: "2" } });
  await page.goto(url);
  await create(page).click();
  await expect(page.getByText("2 open on this line. Order at most that.").first()).toBeVisible();
  await expect(page.getByRole("status", { name: "Created purchase orders" }).getByRole("link")).toHaveCount(2);
  await expect(qty(page, 3, "REQ-1")).toHaveValue("10");
  await create(page).click();
  const bolt = stub.posts.filter(p => p.body.vendor_id === BOLT);
  expect(bolt).toHaveLength(2);
  expect(bolt[1]!.key).toBe(bolt[0]!.key);
});

test("an ineligible vendor: refused without the override right; with it a reason is required and sent", async ({ page }) => {
  const stub = await stubPoCreate(page, { boltEligible: false });
  await page.goto(url);
  await expect(page.getByText("Not eligible: Vendor suspended")).toBeVisible();
  await create(page).click();
  await expect(page.getByText("This vendor is not eligible.").first()).toBeVisible();
  expect(stub.posts).toHaveLength(0);
});

test("with the override right a reason is required and sent as override_reason", async ({ page }) => {
  const stub = await stubPoCreate(page, { boltEligible: false, canOverride: true });
  await page.goto(url);
  await create(page).click();
  await expect(page.getByText("Give a reason for ordering from a vendor who is not eligible.").first()).toBeVisible();
  expect(stub.posts).toHaveLength(0);
  await page.getByLabel("Override Reason").fill("Only stockist");
  await create(page).click();
  await expect(page.getByRole("status", { name: "Created purchase orders" }).getByRole("link")).toHaveCount(3);
  expect(stub.posts.find(p => p.body.vendor_id === BOLT)!.body.override_reason).toBe("Only stockist");
  expect(stub.posts.find(p => p.body.vendor_id === ACME)!.body.override_reason).toBeUndefined();
});

test.describe("German locale", () => {
  test.use({ locale: "de-DE" });
  test("1,5 is sent as 1.5 and the unit price shows its currency", async ({ page }) => {
    const stub = await stubPoCreate(page);
    await page.goto(url);
    await expect(row(page, "Pump")).toContainText("USD");
    for (const [n, r] of [[2, "REQ-2"], [3, "REQ-1"]] as const) await qty(page, n, r).fill("");
    await qty(page, 1, "REQ-1").fill("1,5");
    await create(page).click();
    await expect(page.getByRole("status", { name: "Created purchase orders" })).toBeVisible();
    expect(stub.posts[0]!.body.lines[0]!.quantity).toBe("1.5");
  });
});

test("empty states say why: no award, a superseded award, everything ordered", async ({ page }) => {
  await stubPoCreate(page);
  await page.goto("/purchase-orders/new");
  await expect(page.getByText("Purchase orders start from an approved award.")).toBeVisible();
  await stubPoCreate(page, { status: "superseded" });
  await page.goto(url);
  await expect(page.getByText("Award AWD-0001 is superseded. Only an approved award can be ordered from.")).toBeVisible();
  await expect(create(page)).toHaveCount(0);
  await stubPoCreate(page, { allOrdered: true });
  await page.goto(url);
  await expect(page.getByText("Everything on award AWD-0001 is already ordered.")).toBeVisible();
});

test("keyboard only: Tab reaches every field and the button, Enter submits, focus lands on the created list", async ({ page }) => {
  await stubPoCreate(page, { boltEligible: false, canOverride: true });
  await page.goto(url);
  const reach = async (target: ReturnType<typeof qty>) => {
    for (let i = 0; i < 60; i++) { await page.keyboard.press("Tab"); if (await target.evaluate(n => n === document.activeElement)) return; }
    throw new Error("unreachable by keyboard");
  };
  await page.getByRole("heading", { name: "Create Purchase Orders" }).focus();
  await reach(qty(page, 1, "REQ-1"));
  await reach(qty(page, 2, "REQ-2"));
  await reach(page.getByLabel("Override Reason"));
  await page.keyboard.type("Only stockist");
  await reach(qty(page, 3, "REQ-1"));
  await reach(create(page));
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status", { name: "Created purchase orders" })).toBeFocused();
});

for (const theme of ["light", "dark"] as const) {
  test(`axe: the form and its error state in the ${theme} theme`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await stubPoCreate(page, { boltEligible: false, canOverride: true });
    await page.goto(url);
    await expect(page.getByRole("heading", { name: "Acme Tooling" })).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await create(page).click();
    await expect(page.getByRole("alert").first()).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
