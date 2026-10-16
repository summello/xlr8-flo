import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { stubWorkflows } from "../e2e/workflow-list-fixture";
import { stubQuestionnaires } from "../e2e/vendor-questionnaires-fixture";
import { stubApprovals } from "../e2e/approvals-fixture";
import { instanceId, requisition, reqId, reservation, stubRequisitions } from "../e2e/requisitions-fixture";
import { amendmentRead, quantityChange, stubChanges } from "../e2e/requisition-changes-fixture";
import { stubDashboard } from "../e2e/dashboard-fixture";
import { projectId } from "../e2e/projects-fixture";
import { ROUTE_PATHS } from "../../src/routes/route-paths";

expect(ROUTE_PATHS, "MISSING: forms-kit route in the shared route manifest").toContain(
  "/_dev/forms",
);

const summarize = (violations: { id: string; nodes: { target: unknown; failureSummary?: string }[] }[]) =>
  violations.map(v => `${v.id}: ${v.nodes.map(n => `${JSON.stringify(n.target)} ${n.failureSummary?.split("\n").slice(1).join(" ")}`).join(" | ")}`);

for (const theme of ["light", "dark"] as const) {
  for (const route of [...ROUTE_PATHS, `/projects/${projectId}/dashboard`]) {
    test(`${route} passes axe in ${theme} theme`, async ({ page }) => {
      await page.addInitScript((selectedTheme) => {
        localStorage.setItem("xlr8flo.theme", selectedTheme);
      }, theme);
      if (route === "/admin/approval-workflows") await stubWorkflows(page);
      if (route === "/admin/vendor-questionnaires") await stubQuestionnaires(page);
      if (route.startsWith("/approvals")) await stubApprovals(page);
      if (route.startsWith("/requisitions")) await stubRequisitions(page);
      await page.goto(route);
      await page.locator(".app-shell").waitFor();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      if (route === "/projects") await expect(page.locator("tr[data-grid-row]").first()).toBeVisible();
      if (route === "/projects/new") await expect(page.locator(".project-form")).toBeVisible();
      if (route === "/requisitions/new") await expect(page.locator("#req-title")).toBeVisible();
      if (route === "/requisitions") await expect(page.getByRole("tab", { name: /^Mine/ })).toBeVisible();
      if (route === "/_dev/grid") {
        await expect(page.locator("tr[data-grid-row]").first()).toBeVisible();
      }

      if (route === "/budget" || route.endsWith("/dashboard")) await expect(page.locator(".dashboard-kpi").first()).toBeVisible();
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });
  }
}

for (const theme of ["light", "dark"] as const) {
  for (const route of ["resubmit", "amend", "amend/review"]) {
    test(`/requisitions/<id>/${route} passes axe in ${theme} theme`, async ({ page }) => {
      await page.addInitScript((selectedTheme) => { localStorage.setItem("xlr8flo.theme", selectedTheme); }, theme);
      const resubmit = route === "resubmit";
      await stubChanges(page, {
        row: requisition(resubmit
          ? { estimate: "196000.0000", reservation: reservation("196000.0000"), approval: { instance_id: instanceId, status: "returned" }, version: 3 }
          : { status: "sourcing_in_progress", estimate: "20.0000", reservation: reservation("20.0000"), version: 5 }),
        changes: resubmit ? quantityChange : { ...quantityChange, to: "amendment", routing: "not_applicable" },
        amendment: resubmit ? null : amendmentRead(),
      });
      await page.goto(`/requisitions/${reqId}/${route}`);
      await page.locator(".app-shell").waitFor();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.getByRole("heading", { name: route === "amend" ? "Amendment Details" : "What Changed" })).toBeVisible();
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });
  }
}

test("reduced motion preserves the form's information and complete keyboard outcome", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/_dev/forms");
  await page.locator(".app-shell").waitFor();

  expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(
    true,
  );
  await expect(page.locator(".field-help")).toHaveCount(5);
  await expect(page.getByText("Enter the full amount in USD with up to four decimal places.")).toBeVisible();
  expect(
    Number.parseFloat(
      await page
        .locator(".form-submit")
        .evaluate((button) => getComputedStyle(button).transitionDuration),
    ),
  ).toBeLessThanOrEqual(0.00001);

  await page.getByLabel("Email").focus();
  await page.keyboard.type("active@example.com");
  await page.keyboard.press("Tab");
  await page.keyboard.type("correct horse battery staple");
  await page.keyboard.press("Tab");
  await page.keyboard.press("s");
  await page.keyboard.press("Tab");
  await page.keyboard.type("Avery Chen");
  await page.keyboard.press("Tab");
  await page.keyboard.type("125000.0000");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");

  await expect(page.getByRole("status")).toContainText("Form submitted");
});

// These pre-auth-story fixtures represent a signed-in operator.
test.beforeEach(async ({ page }) => {
  await stubDashboard(page);
  await page.route("**/api/v1/auth/sessions", route => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
});

for (const theme of ["light", "dark"] as const) {
  test(`workflow builder route passes axe in ${theme}`, async ({ page }) => {
    const { stubBuilder, workflowId } = await import("../e2e/workflow-builder-fixture");
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await stubBuilder(page);
    await page.goto(`/admin/approval-workflows/${workflowId}`);
    await page.getByRole("button", { name: "Add Condition" }).waitFor();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}

for (const theme of ["light", "dark"] as const) {
  for (const tab of ["pending", "overdue", "completed", "delegated"] as const) {
    test(`approval inbox ${tab} tab with the decision panel open passes axe in ${theme}`, async ({ page }) => {
      await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
      await stubApprovals(page);
      await page.goto(`/approvals?tab=${tab}`);
      await page.locator(".appr-table tbody tr").first().waitFor();
      expect(summarize((await new AxeBuilder({ page }).analyze()).violations)).toEqual([]);
      await page.locator(".appr-doc").first().click();
      await expect(page.getByText("Why It Reached You")).toBeVisible();
      await expect(page.getByText("Decision Trail").first()).toBeVisible();
      expect(summarize((await new AxeBuilder({ page }).analyze()).violations)).toEqual([]);
    });
  }

  test(`approval availability with errors and the revoke dialog passes axe in ${theme}`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await stubApprovals(page, { delegateAdmin: true });
    await page.goto("/approvals/availability");
    await page.getByRole("button", { name: "Save Out-of-Office" }).click();
    await expect(page.getByRole("alert").first()).toContainText("Not saved");
    expect(summarize((await new AxeBuilder({ page }).analyze()).violations)).toEqual([]);
    await page.getByLabel(/^Starts/).fill("2030-02-01");
    await page.getByLabel(/^Ends/).fill("2030-02-05");
    await page.getByLabel(/^Pass Approvals To/).selectOption({ label: "delegate@example.test" });
    await page.getByRole("button", { name: "Save Out-of-Office" }).click();
    await page.getByRole("button", { name: /^Revoke/ }).click();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    expect(summarize((await new AxeBuilder({ page }).analyze()).violations)).toEqual([]);
  });
}
