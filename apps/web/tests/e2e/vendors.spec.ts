import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { ACCOUNT, TAX, stubVendors, vendorId } from "./vendors-fixture";

test("search, switch tabs, open a vendor, open a document and verify it", async ({ page }) => {
  const state = await stubVendors(page);
  await page.goto("/vendors");
  await expect(page.getByRole("link", { name: "Acme Tooling" })).toBeVisible();
  await expect(page.getByRole("tab", { name: /^Needs Attention/ })).toContainText("7");
  await page.getByLabel("Search", { exact: true }).fill("borealis");
  await page.locator(".vendor-list form").getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("link", { name: "Borealis Steel" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Acme Tooling" })).toHaveCount(0);
  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByRole("tab", { name: /^Needs Attention/ }).click();
  await expect(page.getByRole("link", { name: "Borealis Steel" })).toHaveCount(0);
  await page.getByRole("link", { name: "Acme Tooling" }).click();
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  await page.getByRole("button", { name: "Open INS" }).click();
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.verified).toBe(true);
});

test("paging is server-side: limit and cursor are sent and no request asks for everything", async ({ page }) => {
  const state = await stubVendors(page);
  await page.goto("/vendors?tab=all");
  await expect(page.getByRole("link", { name: "Acme Tooling" })).toBeVisible();
  expect(state.requests.length).toBeGreaterThan(0);
  for (const url of state.requests) expect(Number(url.searchParams.get("limit"))).toBeLessThanOrEqual(100);
  expect(state.requests.every(u => u.searchParams.has("limit"))).toBe(true);
  // the counts come from the server: the Needs Attention tab shows the stub's 7, not the 2 attention rows that happen to be loaded
  await expect(page.getByRole("tab", { name: /^Needs Attention/ })).toContainText("7");
});

test("the documents signal looks ahead, says it in words and the status pill keeps its icon", async ({ page }) => {
  await stubVendors(page);
  await page.goto("/vendors");
  const row = page.locator("tr[data-grid-row]").filter({ hasText: "Acme Tooling" });
  await expect(row.getByText("1 Expiring in 12 Days")).toBeVisible();
  await expect(row.getByText("1 Expired")).toBeVisible();
  await expect(row.getByText("Not Eligible")).toBeVisible();
  await expect(row.locator(".status-pill")).toContainText("Active");
  await expect(row.locator(".status-pill svg")).toHaveCount(1); // icon and words: colour never carries status alone
  await expect(page.locator("tr[data-grid-row]").filter({ hasText: "Cobalt Freight" }).getByText("1 Missing")).toBeVisible();
});

test("keyboard only: tabs by arrows, row to profile, document sheet traps focus and returns it on Escape", async ({ page }) => {
  await stubVendors(page);
  await page.goto("/vendors");
  const active = page.getByRole("tab", { name: /^Active/ });
  await active.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /^Pending Approval/ })).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: /^All/ })).toBeFocused();
  await page.keyboard.press("Home");
  await expect(active).toBeFocused();
  await page.goto(`/vendors/${vendorId}`);
  const open = page.getByRole("button", { name: "Open INS" });
  await open.focus();
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog");
  await expect(sheet).toBeVisible();
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press("Tab");
    expect(await sheet.evaluate(node => node.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await expect(open).toBeFocused();
});

test("a missing document opens a sheet that says what it blocks; a failing card leaves the others", async ({ page }) => {
  await stubVendors(page);
  await page.route(`**/api/v1/vendors/${vendorId}/contacts`, route => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Contacts are unavailable." }) }));
  await page.goto(`/vendors/${vendorId}`);
  await expect(page.getByRole("alert").filter({ hasText: "Contacts" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  await page.getByRole("button", { name: "Open TAX" }).click();
  await expect(page.getByRole("dialog")).toContainText("not eligible");
});

for (const theme of ["light", "dark"] as const) {
  for (const route of ["/vendors", `/vendors/${vendorId}`]) {
    test(`${route.replace(vendorId, ":id")} passes axe in ${theme} theme`, async ({ page }) => {
      await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
      await stubVendors(page);
      await page.goto(route);
      await page.locator(".app-shell").waitFor();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.getByText(route === "/vendors" ? "Acme Tooling" : "Pat Payer").first()).toBeVisible();
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });
  }
}

test.describe("Tax and Banking card", () => {
  const html = (page: Page) => page.evaluate(() => document.documentElement.outerHTML);
  const reveal = async (page: Page, reason = "Verify a payment") => {
    await page.getByRole("button", { name: "Reveal Values" }).click();
    if (reason) await page.getByLabel("Reason").selectOption({ label: reason });
    await page.locator(".tax-reveal").getByRole("button", { name: "Reveal Values" }).click();
  };

  test("full values are absent before reveal, present after, absent again after the 60 s re-mask", async ({ page }) => {
    await page.clock.install();
    const bodies: string[] = [];
    page.on("response", async r => { if (r.url().includes("/api/v1/vendors/") && !r.url().endsWith("/reveal")) bodies.push(await r.text().catch(() => "")); });
    await stubVendors(page);
    await page.goto(`/vendors/${vendorId}`);
    await expect(page.getByText("•••• 4821")).toBeVisible();
    for (const secret of [TAX, ACCOUNT]) {
      expect(await html(page)).not.toContain(secret);
      expect(bodies.join("")).not.toContain(secret);
      expect(await page.title()).not.toContain(secret);
    }
    await reveal(page);
    await expect(page.getByText(ACCOUNT)).toBeVisible();
    await expect(page.getByText(/Revealed by you, .+\. Logged\./)).toBeVisible();
    expect(await html(page)).toContain(TAX);
    expect(await page.title()).not.toContain(TAX);
    await page.clock.fastForward(61_000);
    await expect(page.getByText("•••• 4821")).toBeVisible();
    for (const secret of [TAX, ACCOUNT]) expect(await html(page)).not.toContain(secret);
  });

  test("re-masks when focus leaves the card", async ({ page }) => {
    await stubVendors(page);
    await page.goto(`/vendors/${vendorId}`);
    await reveal(page);
    await expect(page.getByText(ACCOUNT)).toBeVisible();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await expect(page.getByText(ACCOUNT)).toHaveCount(0);
    expect(await html(page)).not.toContain(ACCOUNT);
  });

  test("keyboard: Enter opens the reveal form, a missing reason is blocked in the form and nothing is sent", async ({ page }) => {
    const state = await stubVendors(page);
    await page.goto(`/vendors/${vendorId}`);
    await page.getByRole("button", { name: "Reveal Values" }).focus();
    await page.keyboard.press("Enter");
    await page.locator(".tax-reveal").getByRole("button", { name: "Reveal Values" }).click();
    await expect(page.getByRole("alert")).toContainText("Choose a reason");
    expect(state.reveals).toHaveLength(0);
  });

  test("the 422 for figures in the note puts the server message, clean, at the note field", async ({ page }) => {
    await stubVendors(page);
    await page.goto(`/vendors/${vendorId}`);
    await page.getByRole("button", { name: "Reveal Values" }).click();
    await page.getByLabel("Reason").selectOption({ label: "Other" });
    await page.getByLabel("Note (optional)").fill("acct 000123454821");
    await page.locator(".tax-reveal").getByRole("button", { name: "Reveal Values" }).click();
    await expect(page.locator("#reveal-note-error")).toHaveText("Leave account and tax numbers out of the note.");
    await expect(page.getByLabel("Note (optional)")).toHaveValue("acct 000123454821");
  });

  test("a caller who cannot reveal sees the masked values, Restricted and who to ask, and no button", async ({ page }) => {
    await stubVendors(page, { canReveal: false });
    await page.goto(`/vendors/${vendorId}`);
    await expect(page.getByText("•••• 4821")).toBeVisible();
    await expect(page.getByText("Restricted")).toBeVisible();
    await expect(page.getByText("Ask Vendor Manager or Budget/Finance User to reveal these values.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Reveal Values" })).toHaveCount(0);
  });

  test("re-masks when the pointer leaves the card", async ({ page }) => {
    await stubVendors(page);
    await page.goto(`/vendors/${vendorId}`);
    await reveal(page);
    await expect(page.getByText(ACCOUNT)).toBeVisible();
    // The pointer was over the form button that is now gone; put it on the card, then take it away.
    await page.locator(".tax-card").hover();
    await page.mouse.move(2, 2);
    await expect(page.getByText(ACCOUNT)).toHaveCount(0);
    expect(await html(page)).not.toContain(ACCOUNT);
  });

  test("Edit needs a reveal first, pre-fills from the revealed values and saves the whole record with If-Match", async ({ page }) => {
    const state = await stubVendors(page);
    await page.goto(`/vendors/${vendorId}`);
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    expect(await html(page)).not.toContain(TAX);
    await reveal(page);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByLabel("Account number")).toHaveValue(ACCOUNT);
    await page.getByLabel("Routing code").fill("SORT-11-22-33");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByLabel("Routing code")).toHaveCount(0);
    expect(state.puts).toHaveLength(1);
    expect(state.puts[0]!.ifMatch).toBe("1");
    expect(state.puts[0]!.body).toMatchObject({ tax_id: TAX, bank_routing: "SORT-11-22-33" });
    await expect(page.getByText(ACCOUNT)).toHaveCount(0);
  });

  test("a caller without the edit permission gets no Edit button after revealing", async ({ page }) => {
    await stubVendors(page, { canReveal: true, canEdit: false });
    await page.goto(`/vendors/${vendorId}`);
    await reveal(page);
    await expect(page.getByText(ACCOUNT)).toBeVisible();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
  });

  // One state per test: each loads the page once, so a loaded runner stays inside the timeout.
  for (const theme of ["light", "dark"] as const) {
    for (const state of ["masked", "form", "revealed", "editing", "restricted"] as const) {
      test(`axe passes in ${theme} on the ${state} state`, async ({ page }) => {
        await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
        await stubVendors(page, { canReveal: state !== "restricted" });
        await page.goto(`/vendors/${vendorId}`);
        await expect(page.getByText(state === "restricted" ? "Restricted" : "•••• 4821")).toBeVisible();
        if (state === "form") await page.getByRole("button", { name: "Reveal Values" }).click();
        if (state === "revealed" || state === "editing") {
          await reveal(page, "Audit support");
          await expect(page.getByText(ACCOUNT)).toBeVisible();
        }
        if (state === "editing") await page.getByRole("button", { name: "Edit", exact: true }).click();
        expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      });
    }
  }
});
