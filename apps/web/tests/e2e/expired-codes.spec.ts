import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { inboxRow, stubApprovals } from "./approvals-fixture";
import { project, stubProjects } from "./projects-fixture";
import { lineRow, reqId, requisition, reservation, stubRequisitions } from "./requisitions-fixture";

const expired = { department: { code: "D", status: "inactive" as const }, ledger_account: { code: "L", status: "usable" as const } };
const approved = () => requisition({ status: "sourcing_in_progress", estimate: "20.0000", reservation: reservation("20.0000"), version: 3, codes: expired, codes_expired: true, can_substitute_codes: true });

test("an expired code is flagged and a Project Manager substitutes it by keyboard", async ({ page }) => {
  const state = await stubRequisitions(page, { row: approved(), lines: [lineRow(1)] });
  await page.route(`**/api/v1/requisitions/${reqId}/codes`, async route => {
    state.calls.push({ path: new URL(route.request().url()).pathname, method: "POST", body: route.request().postDataJSON(), headers: route.request().headers(), query: new URLSearchParams() });
    state.row = { ...state.row!, department_code: "D2", codes: { ...expired, department: { code: "D2", status: "usable" } }, codes_expired: false };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.row) });
  });
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.getByText("Expired code", { exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  const open = page.getByRole("button", { name: "Substitute codes" });
  await open.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Substitute codes" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel(/Replace department code D \(inactive\)/)).toBeVisible();
  await expect(dialog.getByLabel(/Replace ledger account/)).toHaveCount(0);
  await dialog.getByLabel(/Replace department code/).fill("D2");
  await dialog.getByLabel("Reason").fill("Department retired");
  await dialog.getByRole("button", { name: "Substitute", exact: true }).click();
  await expect(page.getByText("Expired code", { exact: true })).toHaveCount(0);
  const post = state.calls.find(c => c.path.endsWith("/codes"))!;
  expect(post.body).toEqual({ department_code: "D2", reason: "Department retired" });
  expect(post.headers["idempotency-key"]).toBeTruthy();
});

test("a substitution without a reason is refused and nothing is sent", async ({ page }) => {
  const state = await stubRequisitions(page, { row: approved(), lines: [lineRow(1)] });
  await page.goto(`/requisitions/${reqId}`);
  await page.getByRole("button", { name: "Substitute codes" }).click();
  const dialog = page.getByRole("dialog", { name: "Substitute codes" });
  await dialog.getByLabel(/Replace department code/).fill("D2");
  await dialog.getByRole("button", { name: "Substitute", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("give a reason");
  expect(state.calls.some(c => c.path.endsWith("/codes"))).toBe(false);
});

const approverCodes = { department: { code: "D", status: "inactive" as const }, ledger_account: { code: "L", status: "usable" as const } };

test("the decision panel flags an expired code up front and asks only for that code", async ({ page }) => {
  const state = await stubApprovals(page);
  state.pending = [inboxRow(1, { codes: approverCodes, codes_expired: true }), inboxRow(2)];
  await page.goto("/approvals");
  await page.getByRole("button", { name: /REQ-00402/ }).first().click();
  await expect(page.getByRole("dialog").getByText("Expired code", { exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /REQ-00401/ }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Expired code", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("Replacement department code for D")).toBeVisible();
  await expect(dialog.getByLabel(/Replacement ledger account code/)).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // Keyboard path: the replacement field is reachable by Tab and the decision goes out with it.
  const field = dialog.getByLabel("Replacement department code for D");
  for (let i = 0; i < 40 && !(await field.evaluate(n => n === document.activeElement)); i++) await page.keyboard.press("Tab");
  await expect(field).toBeFocused();
  await page.keyboard.type("D2");
  await dialog.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("status")).toContainText("Approved");
  expect(state.posts[0]?.body.substitutions).toEqual({ department_code: "D2" });
});

test("a project with an expired code shows the badge in the list and the substitute action on its page", async ({ page }) => {
  const flagged = { ...project, codes: approverCodes, codes_expired: true, can_substitute_codes: true };
  const fixture = await stubProjects(page, { rows: [flagged] });
  fixture.row = flagged;
  await page.goto("/projects");
  await expect(page.getByRole("table", { name: "Projects data grid" }).getByText("Expired code", { exact: true })).toBeVisible();
  await page.goto(`/projects/${project.id}`);
  await expect(page.getByText("Expired code", { exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const action = page.getByRole("button", { name: "Substitute codes" });
  await action.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Substitute codes" })).toBeVisible();
  await page.keyboard.press("Escape");
  fixture.row = { ...flagged, can_substitute_codes: false };
  await page.reload();
  await expect(page.getByText("Expired code", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Substitute codes" })).toHaveCount(0);
});

test("the project picker marks a project whose code has expired", async ({ page }) => {
  await stubRequisitions(page, { row: undefined });
  await page.route("**/api/v1/requisitions/lookups/project*", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [{ value: project.id, label: "PRJ-0001 Plant renewal", disabled: false, note: null, codes_expired: true }] }) }));
  await page.goto("/requisitions/new");
  await expect(page.locator("#req-project_id option", { hasText: "expired code" })).toHaveCount(1);
});

test("the requisition action is hidden when the server says the user cannot substitute", async ({ page }) => {
  await stubRequisitions(page, { row: { ...approved(), can_substitute_codes: false }, lines: [lineRow(1)] });
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.getByText("Expired code", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Substitute codes" })).toHaveCount(0);
});
