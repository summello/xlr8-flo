import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

const userId = "11111111-1111-4111-8111-111111111111";
const activeGrantId = "22222222-2222-4222-8222-222222222222";
const pendingGrantId = "33333333-3333-4333-8333-333333333333";
const orgId = "44444444-4444-4444-8444-444444444444";
const path = `/admin/users/${userId}`;

const permissionRows = [
  {
    access: "allowed",
    code: "project.view",
    explanation: `Inherited from org:${orgId}.`,
    id: `project.view|allowed|project-reader|org|${orgId}`,
    source: `project-reader at org:${orgId}`,
  },
  {
    access: "denied",
    code: "requisition.approve",
    explanation: "The Future approver grant becomes active on 1 January 2099.",
    id: "requisition.approve|denied",
    source: "No active source",
  },
  {
    access: "denied",
    code: "budget.transfer",
    explanation: "No active role grant includes this permission in the organization.",
    id: "budget.transfer|denied",
    source: "No active source",
  },
] as const;

type MockOptions = {
  failInitial?: boolean;
  stepUpRequired?: boolean;
};

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    body: JSON.stringify(body),
    contentType: status === 200 ? "application/json" : "application/problem+json",
    status,
  });
}

async function mockAccess(page: Page, options: MockOptions = {}) {
  let active = true;
  // Not a request count: the app runs under StrictMode, so an initial load fires one or two
  // GETs depending on timing, and counting failures made the retry assertion flaky 1 run in 3.
  // The service is down until the test says it recovered.
  let unavailable = options.failInitial === true;
  let activeGrants = [activeGrantId];
  const methods: string[] = [];

  await page.route("**/api/v1/admin/users/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    methods.push(request.method());

    if (request.method() !== "GET") {
      if (options.stepUpRequired) {
        await route.fulfill({
          body: JSON.stringify({ detail: "Recent authentication is required." }),
          contentType: "application/problem+json",
          headers: { "WWW-Authenticate": "step-up" },
          status: 403,
        });
        return;
      }
      if (request.method() === "POST") active = false;
      if (request.method() === "DELETE") {
        activeGrants = activeGrants.filter((grant) => !url.pathname.endsWith(grant));
      }
      await route.fulfill({ status: 204 });
      return;
    }

    if (url.pathname.endsWith("/permissions")) {
      const needle = (url.searchParams.get("filter") ?? "").toLocaleLowerCase();
      const rows = permissionRows.filter((row) =>
        Object.values(row).some((value) => value.toLocaleLowerCase().includes(needle)),
      );
      await fulfillJson(route, {
        lastCursor: null,
        nextCursor: null,
        previousCursor: null,
        rows,
        startIndex: 0,
        total: rows.length,
      });
      return;
    }

    if (unavailable) {
      await fulfillJson(route, { detail: "The access service is temporarily unavailable." }, 503);
      return;
    }

    await fulfillJson(route, {
      denied_examples: permissionRows
        .filter((row) => row.access === "denied")
        .map((row) => ({ code: row.code, reason: row.explanation })),
      grants: activeGrants.map(() => ({
        effective_from: null,
        granted_at: "2026-08-25T09:00:00Z",
        granted_by: "admin@example.test",
        id: activeGrantId,
        role: "project-reader",
        role_name: "Project reader",
        scope_id: orgId,
        scope_name: "Organization",
        scope_type: "org",
      })),
      pending_grants: [{
        effective_from: "2099-01-01T09:00:00Z",
        granted_at: "2026-08-25T09:30:00Z",
        granted_by: "admin@example.test",
        id: pendingGrantId,
        role: "future-approver",
        role_name: "Future approver",
        scope_id: orgId,
        scope_name: "Organization",
        scope_type: "org",
      }],
      permissions: permissionRows
        .filter((row) => row.access === "allowed")
        .map((row) => ({ allowed: true, code: row.code, source: {} })),
      user: { email: "avery@example.test", id: userId, status: active ? "active" : "deactivated" },
    });
  });

  return {
    methods,
    recover() {
      unavailable = false;
    },
  };
}

async function openExplorer(page: Page) {
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 2, name: "avery@example.test" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Effective permissions data grid" })).toHaveAttribute(
    "aria-rowcount",
    "3",
  );
}

test("explorer shows active and pending provenance and server-searches permission sources", async ({
  page,
}) => {
  await mockAccess(page);
  await openExplorer(page);

  await expect(page.locator('.status-pill[data-doc-type="user"]')).toHaveAttribute(
    "data-status",
    "active",
  );
  await expect(page.getByRole("heading", { level: 2, name: "Active grants" })).toBeVisible();
  await expect(page.getByText("Project reader", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Pending grants" })).toBeVisible();
  await expect(page.getByText("Future approver", { exact: true })).toBeVisible();
  await expect(page.getByText(`project-reader at org:${orgId}`)).toBeVisible();
  await expect(page.getByText(`Inherited from org:${orgId}.`)).toBeVisible();

  const firstCell = page.locator('[data-grid-row="0"] [data-column-id="code"]');
  await firstCell.focus();
  await page.keyboard.press("/");
  await expect(page.getByLabel("Search permissions")).toBeFocused();
  await page.keyboard.type("approve");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/filter=approve/);
  await expect(page.locator("tr[data-grid-row]")).toHaveCount(1);
  await expect(page.getByText("requisition.approve")).toBeVisible();

  await page.locator('[data-grid-row="0"] [data-column-id="code"]').focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText(/requisition\.approve: The Future approver grant/)).toHaveCount(1);
});

test("revoke and deactivate are keyboard actions and refresh the retained user", async ({ page }) => {
  const observed = await mockAccess(page);
  await openExplorer(page);

  const revoke = page.getByRole("button", { name: "Revoke Project reader at Organization" });
  await revoke.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Project reader", { exact: true })).toHaveCount(0);

  const deactivate = page.getByRole("button", { name: "Deactivate user" });
  await deactivate.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator('.status-pill[data-doc-type="user"]')).toHaveAttribute(
    "data-status",
    "deactivated",
  );
  await expect(page.getByRole("heading", { level: 2, name: "avery@example.test" })).toBeVisible();
  expect(observed.methods).toContain("DELETE");
  expect(observed.methods).toContain("POST");
});

test("step-up failure preserves access data and explains recovery", async ({ page }) => {
  await mockAccess(page, { stepUpRequired: true });
  await openExplorer(page);

  await page.getByRole("button", { name: "Deactivate user" }).press("Enter");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("Recent authentication is required before this change.");
  await expect(alert).toContainText("No access data was changed");
  await expect(page.locator('.status-pill[data-status="active"]')).toBeVisible();
  await expect(page.getByText("Project reader", { exact: true })).toBeVisible();
});

test("load failure states cause, preservation, recovery, and retries successfully", async ({ page }) => {
  const access = await mockAccess(page, { failInitial: true });
  await page.goto(path);
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("Cause: The access service is temporarily unavailable.");
  await expect(alert).toContainText("Your place in Administration is preserved");
  access.recover();
  await page.getByRole("button", { name: "Retry" }).press("Enter");
  await expect(page.getByRole("heading", { level: 2, name: "avery@example.test" })).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
  test(`effective-access explorer passes axe and remains unambiguous in greyscale ${theme}`, async ({
    page,
  }) => {
    await page.addInitScript((selectedTheme) => {
      localStorage.setItem("xlr8flo.theme", selectedTheme);
    }, theme);
    await mockAccess(page);
    await openExplorer(page);
    await page.locator("html").evaluate((root) => {
      root.style.filter = "grayscale(1)";
    });
    await expect(page.getByText("Allowed", { exact: true })).toBeVisible();
    await expect(page.getByText("Denied", { exact: true }).first()).toBeVisible();
    await expect(page.locator('.status-pill[data-status="active"] svg[aria-hidden="true"]')).toHaveCount(1);
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
}

for (const width of [375, 768, 1024, 1440]) {
  test(`effective-access explorer has no page overflow and keeps touch actions at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockAccess(page);
    await openExplorer(page);
    const pageWidths = await page.evaluate(() => ({
      client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
    }));
    expect(pageWidths.scroll).toBeLessThanOrEqual(pageWidths.client);
    if (width < 1024) {
      const action = page.getByRole("button", { name: "Deactivate user" });
      expect(await action.evaluate((button) => button.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
    }
  });
}
