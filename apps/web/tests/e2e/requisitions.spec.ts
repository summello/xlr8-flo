import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { generatedProjectId } from "./requisitions-fixture";
import { instanceId, lineRow, requisition, reservation, reqId, stubRequisitions } from "./requisitions-fixture";
import { projectId } from "./projects-fixture";
import { keyboardFocus } from "./helpers";

async function fillHeader(page: Page, over: Record<string, string> = {}) {
  const values: Record<string, string> = { title: "Press brake", project_id: projectId, department_code: "D", ledger_account_code: "L", needed_by: "2030-01-31", delivery_address_id: "aaaaaaaa-1111-4111-8111-111111111111", ...over };
  await page.locator("#req-title").fill(values.title!);
  await page.locator("#req-project_id").selectOption(values.project_id!);
  await page.locator("#req-department_code").selectOption(values.department_code!);
  await page.locator("#req-ledger_account_code").selectOption(values.ledger_account_code!);
  await page.locator("#req-needed_by").fill(values.needed_by!);
  await page.locator("#req-delivery_address_id").selectOption(values.delivery_address_id!);
  await page.locator("#req-justification").fill("End of life");
}
async function addItem(page: Page, n: number, quantity: string, price: string, description = `Part ${n}`) {
  await page.getByRole("button", { name: "Add item line" }).click();
  await page.getByLabel(`Description, line ${n}`).fill(description);
  await page.getByLabel(`Quantity, line ${n}`).fill(quantity);
  await page.getByLabel(`Unit, line ${n}`).selectOption("ea");
  await page.getByLabel(`Unit price, line ${n}`).fill(price);
}
const heading = (page: Page, text: string | RegExp) => page.getByRole("heading", { name: text });

test("create with an item and a catalogue line, save, submit, then see the detail", async ({ page }) => {
  const state = await stubRequisitions(page);
  await page.goto("/requisitions/new");
  await expect(page.locator("#funding-check")).toContainText("Funds are checked when you save the draft.");
  await expect(page.getByText("Save the draft to attach files")).toBeVisible();
  await fillHeader(page);
  await addItem(page, 1, "3", "0.10");
  await page.getByLabel("Find in the catalogue").fill("drive");
  await page.getByRole("button", { name: "Add Drive motor" }).click();
  await expect(page.getByLabel("Quantity, line 2")).toHaveValue("1");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved. Nothing is reserved until you submit.")).toBeVisible();
  await expect(page.locator(".req-status", { hasText: /^Draft saved/ })).toBeVisible();
  const create = state.calls.find(c => c.method === "POST" && c.path === "/api/v1/requisitions")!;
  expect(create.headers["idempotency-key"]).toBeTruthy();
  const put = state.calls.find(c => c.method === "PUT")!;
  expect(put.headers["if-match"]).toBe("1");
  expect(put.body?.lines).toHaveLength(2);
  expect(page.url()).toContain(`/requisitions/${reqId}/edit`);
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(heading(page, "REQ-00418 Submitted for Approval")).toBeVisible();
  await expect(page.getByText("Routed to amara@example.test, step 1 of 1.")).toBeVisible();
  const submit = state.calls.find(c => c.path.endsWith("/transitions") && c.method === "POST")!;
  expect(submit.body).toEqual({ to: "approval_pending" });
  expect(submit.headers["idempotency-key"]).toBeTruthy();
  await page.getByRole("link", { name: "View requisition" }).click();
  await expect(page.locator('.req-heading [data-status="approval_pending"]')).toBeVisible();
  await expect(page.locator("#reservation-title")).toBeVisible();
  await expect(page.locator(".req-card", { hasText: "Reservation" }).getByText("18,500.30")).toBeVisible();
});

test("an empty submit lists every problem by category, focuses the summary, and each link focuses its control", async ({ page }) => {
  const state = await stubRequisitions(page);
  await page.goto("/requisitions/new");
  await page.getByRole("button", { name: "Add item line" }).click();
  await page.getByRole("button", { name: "Submit for approval" }).click();
  const summary = page.getByRole("alert").filter({ hasText: "Fix 10 things before you submit" });
  await expect(summary).toBeFocused();
  await expect(summary).toContainText("Nothing was submitted. Everything you entered is kept.");
  await expect(summary.getByRole("link", { name: "Required field: Enter a title" })).toBeVisible();
  await expect(summary.getByRole("link", { name: "Lines: Line 1 needs a quantity" })).toBeVisible();
  await expect(page.locator("#req-title-error")).toHaveText("Enter a title");
  await expect(page.locator("#req-title")).toHaveAttribute("aria-invalid", "true");
  await summary.getByRole("link", { name: "Required field: Explain why it is needed" }).click();
  await expect(page.locator("#req-justification")).toBeFocused();
  await summary.getByRole("link", { name: "Lines: Line 1 needs a quantity" }).click();
  await expect(page.locator("#line-1-quantity")).toBeFocused();
  expect(state.calls.filter(c => c.method === "POST")).toHaveLength(0);
  await expect(page.getByRole("button", { name: "Submit for approval" })).toBeEnabled();
});

test("every message the server can send renders under its category", async ({ page }) => {
  const state = await stubRequisitions(page);
  state.submitProblems = [
    { field: "master_data/ledger_account_code", message: "Ledger account 6140 is inactive. Choose an active account" },
    { field: "project/project_id", message: "The chosen project cannot take a requisition" },
    { field: "lines/1/description", message: "Line 1 needs a description" },
    { field: "funds/estimate", message: "Over the available funds by 20,000.00 USD. Reduce the estimate or ask for a budget transfer" },
    { field: "routing/workflow", message: "No approver is set for Infrastructure above 1.00. Ask an administrator" },
    { field: "required/needed_by", message: "The needed-by date is in the past" },
  ];
  await page.goto("/requisitions/new"); await fillHeader(page); await addItem(page, 1, "1", "5");
  await page.getByRole("button", { name: "Submit for approval" }).click();
  const summary = page.getByRole("alert").filter({ hasText: "Fix 6 things before you submit" });
  for (const text of ["Master data: Ledger account 6140 is inactive. Choose an active account", "Project: The chosen project cannot take a requisition", "Lines: Line 1 needs a description",
    "Funds: Over the available funds by 20,000.00 USD. Reduce the estimate or ask for a budget transfer", "Routing: No approver is set for Infrastructure above 1.00. Ask an administrator", "Required field: The needed-by date is in the past"]) {
    await expect(summary.getByRole("link", { name: text })).toBeVisible();
  }
  await expect(page.locator("#req-ledger_account_code-error")).toContainText("inactive");
  await expect(page.locator("#line-1-description-error")).toHaveText("Line 1 needs a description");
  await expect(summary).toBeFocused();
  await expect(page.getByLabel("Description, line 1")).toHaveValue("Part 1");
});

test("a conflict shows the server's words and keeps the input", async ({ page }) => {
  const state = await stubRequisitions(page);
  state.submitDetail = "Not resubmitted. Nothing has changed.";
  await page.goto("/requisitions/new"); await fillHeader(page); await addItem(page, 1, "1", "5");
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Not submitted" })).toContainText("Not resubmitted. Nothing has changed.");
  await expect(page.locator("#req-title")).toHaveValue("Press brake");
});

test("over funds: shortfall in parentheses with the word Exceeds, Submit still posts, no bypass, a smaller quantity clears it", async ({ page }) => {
  const state = await stubRequisitions(page);
  state.available = "40.0000";
  state.submitProblems = [{ field: "funds/estimate", message: "Over the available funds by 60.00 USD. Reduce the estimate or ask for a budget transfer" }];
  await page.goto("/requisitions/new"); await fillHeader(page); await addItem(page, 1, "1", "100");
  await page.getByRole("button", { name: "Save draft" }).click();
  const funding = page.locator("#funding-check");
  await expect(funding).toContainText("Exceeds the available funds");
  await expect(funding.locator(".project-money[data-negative]")).toContainText("(-60.00)");
  await expect(funding).toContainText("A requisition over its funding cannot be submitted.");
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByRole("link", { name: /^Funds: Over the available funds by 60.00 USD/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /anyway|override|bypass/i })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /anyway|override|bypass/i })).toHaveCount(0);
  expect(state.calls.some(c => c.path.endsWith("/transitions"))).toBe(true);
  await page.getByLabel("Quantity, line 1").fill("0.3");
  await expect(funding).toContainText("Within the available funds");
  await expect(funding).not.toContainText("Exceeds");
});

test("a returned draft holding its reservation shows no false shortfall (held 60 of 100, available 40)", async ({ page }) => {
  const state = await stubRequisitions(page, { row: requisition({ estimate: "100.0000", reservation: reservation("60.0000"), approval: { instance_id: instanceId, status: "returned" }, version: 3 }), lines: [lineRow(1, { quantity: "10.0000", unit_price: "10.0000", amount: "100.0000" })] });
  state.available = "40.0000";
  await page.goto(`/requisitions/${reqId}/edit`);
  const funding = page.locator("#funding-check");
  await expect(funding).toContainText("Within the available funds");
  await expect(funding).not.toContainText("Exceeds");
  await expect(funding.locator(".req-funds")).toContainText("100.00");
});

test("a requisition-generated project is listed but disabled with its note", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions/new");
  const option = page.locator(`#req-project_id option[value="${generatedProjectId}"]`);
  await expect(option).toBeDisabled();
  await expect(option).toHaveText("PRJ-0002 Cooling upgrade (created from REQ-00277)");
});

test("cents are exact and the line totals, estimate and Funding Check agree", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions/new"); await fillHeader(page);
  await addItem(page, 1, "3", "0.10"); await addItem(page, 2, "2.5", "19.99");
  await expect(page.locator("tbody tr").nth(0).locator("td").nth(5)).toContainText("0.30");
  await expect(page.locator("tbody tr").nth(1).locator("td").nth(5)).toContainText("49.98");
  await expect(page.locator(".req-total")).toContainText("50.28");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.locator("#funding-check")).toContainText("Within the available funds");
  await expect(page.locator("#funding-check .req-funds > div").nth(1)).toContainText("50.28");
  await expect(page.locator("#funding-check .req-funds > div").nth(2)).toContainText("99,949.72");
});

test("a Project Request names sponsors, shows its note and no funds figure", async ({ page }) => {
  const state = await stubRequisitions(page);
  await page.goto("/requisitions/new");
  await page.getByLabel("A new Project Request").check();
  await expect(page.getByText("A Project Request asks for budget for a new project.")).toBeVisible();
  await expect(page.locator("#funding-check")).toContainText("Funding is assigned when a Project Request is approved. Nothing is reserved until then.");
  await expect(page.locator("#req-bu_id")).toBeVisible();
  await page.locator("#req-title").fill("New plant");
  await page.locator("#req-bu_id").selectOption({ index: 1 });
  await page.locator("#req-sponsor_ids").selectOption(["bbbbbbbb-1111-4111-8111-111111111111", "bbbbbbbb-2222-4222-8222-222222222222"]);
  await page.locator("#req-department_code").selectOption("D");
  await page.locator("#req-ledger_account_code").selectOption("L");
  await page.locator("#req-needed_by").fill("2030-01-31");
  await page.locator("#req-delivery_address_id").selectOption({ index: 1 });
  await page.locator("#req-justification").fill("Capacity");
  await page.getByRole("button", { name: "Add item line" }).click();
  await page.getByLabel("Description, line 1").fill("Crane");
  await page.getByLabel("Quantity, line 1").fill("1");
  await page.getByLabel("Unit, line 1").selectOption("ea");
  await page.getByLabel("Unit price, line 1").fill("10");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved. Nothing is reserved until you submit.")).toBeVisible();
  const create = state.calls.find(c => c.method === "POST" && c.path === "/api/v1/requisitions")!;
  expect(create.body?.sponsor_ids).toHaveLength(2);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a Project Request needs a sponsor before it saves", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions/new");
  await page.getByLabel("A new Project Request").check();
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByText("Name at least one sponsor").first()).toBeVisible();
});

test("the Project Request option is hidden without project_request.create", async ({ page }) => {
  const state = await stubRequisitions(page);
  state.canRequest = false;
  await page.goto("/requisitions/new");
  await expect(page.getByLabel("An existing project")).toBeVisible();
  await expect(page.getByLabel("A new Project Request")).toHaveCount(0);
});

test("keyboard only: the Project Request option and sponsors are reachable", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions/new");
  await page.getByLabel("A new Project Request").focus();
  await page.keyboard.press("Space");
  await keyboardFocus(page, page.locator("#req-sponsor_ids"));
});

test("UX-003: a dirty form asks before in-app navigation; beforeunload exists only while dirty", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions/new");
  const beforeUnloadPrevented = () => page.evaluate(() => { const e = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; });
  expect(await beforeUnloadPrevented()).toBe(false);
  await page.locator("#req-title").fill("Press brake");
  expect(await beforeUnloadPrevented()).toBe(true);
  let prompts = 0;
  page.once("dialog", async dialog => { prompts++; expect(dialog.message()).toContain("unsaved changes"); await dialog.dismiss(); });
  await page.getByRole("link", { name: "Projects", exact: true }).first().click();
  await expect.poll(() => prompts).toBe(1);
  await expect(page).toHaveURL(/\/requisitions\/new$/);
  await expect(page.locator("#req-title")).toHaveValue("Press brake");
  page.once("dialog", dialog => void dialog.accept());
  await page.getByRole("link", { name: "Projects", exact: true }).first().click();
  await expect(page).toHaveURL(/\/projects/);
  await page.goto("/requisitions/new");
  await page.locator("#req-title").fill("x"); await page.locator("#req-title").fill("");
  expect(await beforeUnloadPrevented()).toBe(false);
});

async function editedNewRequisition(page: Page) {
  const state = await stubRequisitions(page);
  state.list = [requisition()];
  await page.goto("/requisitions");
  await page.getByRole("link", { name: "New requisition" }).click();
  await expect(page).toHaveURL(/\/requisitions\/new$/);
  await page.locator("#req-title").fill("Press brake");
}

test("UX-003: browser Back on a dirty form prompts; cancelling stays and keeps the input, and Back asks again", async ({ page }) => {
  await editedNewRequisition(page);
  const prompts: string[] = [];
  page.on("dialog", dialog => { prompts.push(dialog.message()); void dialog.dismiss(); });
  await page.goBack();
  await expect.poll(() => prompts.length).toBe(1);
  expect(prompts[0]).toContain("unsaved changes");
  await expect(page).toHaveURL(/\/requisitions\/new$/);
  await expect(page.locator("#req-title")).toHaveValue("Press brake");
  await page.goBack();
  await expect.poll(() => prompts.length).toBe(2);
  await expect(page).toHaveURL(/\/requisitions\/new$/);
  await expect(page.locator("#req-title")).toHaveValue("Press brake");
});

test("UX-003: browser Back on a dirty form prompts; confirming leaves, and Forward back is also guarded once dirty again", async ({ page }) => {
  await editedNewRequisition(page);
  page.once("dialog", dialog => void dialog.accept());
  await page.goBack();
  await expect(page).toHaveURL(/\/requisitions(\?|$)/); // the grid adds its own query string
  await expect(page.getByRole("tab", { name: /^Mine/ })).toBeVisible();
  // A clean page leaves without a prompt in either direction.
  let prompted = false;
  page.on("dialog", dialog => { prompted = true; void dialog.dismiss(); });
  await page.goForward();
  await expect(page).toHaveURL(/\/requisitions\/new$/);
  expect(prompted).toBe(false);
});

test("UX-003: after the first save the guard restores the edit URL, not the new-requisition URL", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions");
  await page.getByRole("link", { name: "New requisition" }).click();
  await fillHeader(page); await addItem(page, 1, "1", "5");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page).toHaveURL(new RegExp(`/requisitions/${reqId}/edit$`));
  await page.getByLabel("Quantity, line 1").fill("2");
  page.once("dialog", dialog => void dialog.dismiss());
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/requisitions/${reqId}/edit$`));
  await expect(page.getByLabel("Quantity, line 1")).toHaveValue("2");
});

test("UX-003 history: dirty Back, cancel keeps URL and form; Back again and confirm leaves", async ({ page }) => {
  await editedNewRequisition(page);
  let answer: "dismiss" | "accept" = "dismiss"; let prompts = 0;
  page.on("dialog", dialog => { prompts++; void (answer === "accept" ? dialog.accept() : dialog.dismiss()); });
  await page.goBack();
  await expect.poll(() => prompts).toBe(1);
  await expect(page).toHaveURL(/\/requisitions\/new(\?.*)?$/);
  await expect(page.locator("#req-title")).toHaveValue("Press brake");
  answer = "accept";
  await page.goBack();
  await expect.poll(() => prompts).toBe(2);
  await expect(page).toHaveURL(/\/requisitions(\?|$)/);
});

test("UX-003 history: a cancelled Forward keeps the forward entry, so Forward again prompts and a confirm lands on Projects", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions/new");
  await page.getByRole("link", { name: "Projects", exact: true }).first().click();
  await expect(page).toHaveURL(/\/projects/);
  await page.goBack();
  await expect(page).toHaveURL(/\/requisitions\/new(\?.*)?$/);
  await page.locator("#req-title").fill("Press brake");
  let answer: "dismiss" | "accept" = "dismiss"; let prompts = 0;
  page.on("dialog", dialog => { prompts++; void (answer === "accept" ? dialog.accept() : dialog.dismiss()); });
  await page.goForward();
  await expect.poll(() => prompts).toBe(1);
  await expect(page).toHaveURL(/\/requisitions\/new(\?.*)?$/);
  await expect(page.locator("#req-title")).toHaveValue("Press brake");
  answer = "accept";
  await page.goForward(); // the projects grid may append its own query string on the way; fails if the cancel truncated the forward history
  await expect.poll(() => prompts).toBe(2);
  await expect(page).toHaveURL(/\/projects/);
});

test("UX-003 history: clean navigation, Back and Forward never prompt", async ({ page }) => {
  await stubRequisitions(page);
  let prompted = false;
  page.on("dialog", dialog => { prompted = true; void dialog.dismiss(); });
  await page.goto("/requisitions");
  await page.getByRole("link", { name: "New requisition" }).click();
  await expect(page).toHaveURL(/\/requisitions\/new(\?.*)?$/);
  await page.getByRole("link", { name: "Projects", exact: true }).first().click();
  await expect(page).toHaveURL(/\/projects/);
  await page.goBack(); await expect(page).toHaveURL(/\/requisitions\/new(\?.*)?$/);
  await page.goBack(); await expect(page).toHaveURL(/\/requisitions(\?|$)/);
  await page.goForward(); await expect(page).toHaveURL(/\/requisitions\/new(\?.*)?$/);
  expect(prompted).toBe(false);
});

test("saving clears the guard", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions/new"); await fillHeader(page); await addItem(page, 1, "1", "5");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved. Nothing is reserved until you submit.")).toBeVisible();
  expect(await page.evaluate(() => { const e = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; })).toBe(false);
});

test("the Funding Check, catalogue and attachments fail alone", async ({ page }) => {
  const state = await stubRequisitions(page);
  state.catalogueFail = true;
  await page.goto("/requisitions/new"); await fillHeader(page);
  await expect(page.getByText("Could not load suggestions")).toBeVisible();
  await expect(page.getByText("You can still add lines by hand.")).toBeVisible();
  await addItem(page, 1, "1", "5");
  await expect(page.locator(".req-total")).toContainText("5.00");
});

test("a failed submit retried with no edit reuses the idempotency key; an edit starts a new one", async ({ page }) => {
  const state = await stubRequisitions(page);
  await page.goto("/requisitions/new"); await fillHeader(page); await addItem(page, 1, "3", "0.10");
  state.transitionFails = true;
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Not submitted" })).toBeVisible();
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Not submitted" })).toBeVisible();
  const submits = () => state.calls.filter(c => c.path.endsWith("/transitions") && c.method === "POST");
  await expect.poll(() => submits().length).toBe(2);
  expect(submits()[0]!.headers["idempotency-key"]).toBeTruthy();
  expect(submits()[1]!.headers["idempotency-key"]).toBe(submits()[0]!.headers["idempotency-key"]);
  expect(submits()[1]!.headers["if-match"]).toBe(submits()[0]!.headers["if-match"]);
  // A real edit changes what is being submitted, so the key changes with it.
  await page.getByLabel("Quantity, line 1").fill("4");
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect.poll(() => submits().length).toBe(3);
  expect(submits()[2]!.headers["idempotency-key"]).not.toBe(submits()[0]!.headers["idempotency-key"]);
  state.transitionFails = false;
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(heading(page, /Submitted for Approval/)).toBeVisible();
  expect(submits()[3]!.headers["idempotency-key"]).toBe(submits()[2]!.headers["idempotency-key"]);
});

test("each editable line control is required, tagged and has persistent helper text", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions/new");
  await page.getByRole("button", { name: "Add item line" }).click();
  for (const field of ["description", "quantity", "uom_code", "unit_price"]) {
    await expect(page.locator(`#line-1-${field}`)).toHaveAttribute("required", "");
    await expect(page.locator(`#line-1-${field}-help`)).toBeVisible();
  }
  await page.getByLabel("Quantity, line 1").fill("2");
  await expect(page.locator('label[for="line-1-quantity"] .rq-done')).toBeAttached();
  await expect(page.locator('label[for="line-1-quantity"] .rq-need')).toBeHidden();
});

test("the Required tag turns to Done from the control's own state and the action bar says so", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions/new");
  await expect(page.locator(".req-required-state")).toContainText("Required fields are still empty");
  const tag = page.locator('label[for="req-title"] .req-tag');
  await expect(tag.locator(".rq-need")).toBeVisible();
  await page.locator("#req-title").fill("A title");
  await expect(tag.locator(".rq-done")).toBeVisible();
  await expect(tag.locator(".rq-need")).toBeHidden();
  await fillHeader(page);
  await expect(page.locator(".req-required-state")).toContainText("All required fields complete");
});

test("keyboard only: create, add a line, remove it, submit, open the detail and withdraw", async ({ page }) => {
  await stubRequisitions(page);
  await page.goto("/requisitions/new"); await page.locator("#req-title").waitFor();
  await keyboardFocus(page, page.locator("#req-title")); await page.keyboard.type("Press brake");
  await keyboardFocus(page, page.locator("#req-project_id")); await page.keyboard.type("PRJ-0001");
  await keyboardFocus(page, page.locator("#req-department_code")); await page.keyboard.type("D");
  await keyboardFocus(page, page.locator("#req-ledger_account_code")); await page.keyboard.type("L");
  await keyboardFocus(page, page.locator("#req-needed_by")); await page.locator("#req-needed_by").fill("2030-01-31");
  await keyboardFocus(page, page.locator("#req-delivery_address_id")); await page.keyboard.type("North");
  await keyboardFocus(page, page.locator("#req-justification")); await page.keyboard.type("End of life");
  await keyboardFocus(page, page.getByRole("button", { name: "Add item line" })); await page.keyboard.press("Enter");
  await keyboardFocus(page, page.getByLabel("Remove line 1")); await page.keyboard.press("Enter");
  await expect(page.getByText("No Lines Yet")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add item line" })).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByLabel("Description, line 1").focus(); await page.keyboard.type("Part");
  await keyboardFocus(page, page.getByLabel("Quantity, line 1")); await page.keyboard.type("2");
  await keyboardFocus(page, page.getByLabel("Unit, line 1")); await page.keyboard.type("ea");
  await keyboardFocus(page, page.getByLabel("Unit price, line 1")); await page.keyboard.type("9");
  await keyboardFocus(page, page.getByRole("button", { name: "Submit for approval" })); await page.keyboard.press("Enter");
  await expect(heading(page, /Submitted for Approval/)).toBeVisible();
  await keyboardFocus(page, page.getByRole("link", { name: "View requisition" })); await page.keyboard.press("Enter");
  await keyboardFocus(page, page.getByRole("button", { name: "Withdraw requisition" })); await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Keep Requisition" })).toBeFocused();
});

/* ---- Detail ---- */
test("detail: initial load shows every card as a skeleton at its final place, then the cards replace them", async ({ page }) => {
  await stubRequisitions(page, { row: requisition({ estimate: "20.0000" }), lines: [lineRow(1)] });
  let release = () => {};
  const ready = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/api/v1/requisitions/${reqId}`, async route => {
    await ready;
    await route.fallback();
  });
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.locator(".req-card-skeleton")).toHaveCount(6);
  for (const box of await page.locator(".req-card-skeleton").all()) expect((await box.boundingBox())!.height).toBeGreaterThan(100);
  release();
  await expect(page.locator("#request-title")).toBeVisible();
  await expect(page.locator(".req-card-skeleton")).toHaveCount(0);
});

test("detail: a refreshing section keeps its data and says it is refreshing", async ({ page }) => {
  const state = await stubRequisitions(page, pending());
  state.links = [{ key: "ledger:1", kind: "ledger_entry", id: "1", label: "Reservation 1", state: "Reserved", state_tone: "info", href: "/ledger/1", at: "2026-10-02T10:00:00Z" }];
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.getByRole("link", { name: "Reservation 1" })).toBeVisible();
  await page.getByRole("button", { name: "Withdraw requisition" }).click();
  await page.getByRole("button", { name: "Withdraw Requisition" }).click();
  await expect(page.locator('.req-heading [data-status="withdrawn"]')).toBeVisible();
  await expect(page.getByRole("link", { name: "Reservation 1" })).toBeVisible();
});

const STATUSES = ["draft", "approval_pending", "sourcing_in_progress", "sourced", "awarded", "open_for_purchase", "completed", "rejected", "withdrawn", "cancelled", "expired"] as const;
for (const status of STATUSES) {
  test(`detail: exactly one current step for ${status}`, async ({ page }) => {
    await stubRequisitions(page, { row: requisition({ status, estimate: "20.0000" }), lines: [lineRow(1)] });
    await page.goto(`/requisitions/${reqId}`);
    await expect(page.locator(".req-stepper [aria-current='step']")).toHaveCount(1);
    await expect(page.locator(".req-heading")).toContainText("REQ-00418 · Plant renewal · requested by dev@example.test");
  });
}

test("detail: lifecycle words, remaining from the server, split award, linked records, history order", async ({ page }) => {
  const state = await stubRequisitions(page, { row: requisition({ status: "awarded", estimate: "20.0000", reservation: reservation("20.0000") }), lines: [lineRow(1, { requested_qty: "10.0000", awarded_qty: "6.0000", ordered_qty: "4.0000", cancelled_qty: "1.0000", remaining_qty: "5.0000" })] });
  state.events = [
    { at: "2026-10-02T10:00:00Z", from_status: "draft", to_status: "approval_pending", actor_email: "dev@example.test", actor_name: null, system: false, reason: null, funding_round: 1, ledger_entry_id: 1, decision_id: null },
    { at: "2026-10-03T10:00:00Z", from_status: "approval_pending", to_status: "sourcing_in_progress", actor_email: null, actor_name: null, system: true, reason: null, funding_round: 1, ledger_entry_id: null, decision_id: null },
  ];
  state.links = [{ key: "ledger:1", kind: "ledger_entry", id: "1", label: "Reservation 1", state: "Reserved", state_tone: "info", href: "/ledger/1", at: "2026-10-02T10:00:00Z" }];
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Press brake replacement");
  await expect(page.locator(".req-stepper li[aria-current='step']")).toContainText("Awarded, current");
  await expect(page.locator(".req-stepper li").first()).toContainText("Draft, done");
  await expect(page.locator(".req-stepper li").last()).toContainText("Completed, not yet");
  const row = page.locator(".req-rollup tbody tr").first();
  await expect(row.locator("td[data-label='Remaining']")).toHaveText("5");
  await expect(row.locator("td[data-label='Awarded']")).toHaveText("6");
  await expect(row.locator("td[data-label='Ordered']")).toHaveText("4");
  const history = page.locator(".req-history li");
  await expect(history.first()).toContainText("System");
  await expect(history.last()).toContainText("dev@example.test");
  await expect(page.getByRole("link", { name: "Reservation 1" })).toBeVisible();
  await expect(page.locator(".req-card", { hasText: "Reservation" }).first().getByText("Project Available")).toBeVisible();
});

test("detail: terminal states are shown in words", async ({ page }) => {
  await stubRequisitions(page, { row: requisition({ status: "rejected" }) });
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.locator(".req-stepper li[aria-current='step']")).toContainText("Rejected, current");
  await expect(page.getByText("It was rejected.")).toBeVisible();
});

test("detail: nothing linked, and one failing card does not blank the others", async ({ page }) => {
  const state = await stubRequisitions(page, { row: requisition({ estimate: "20.0000" }), lines: [lineRow(1)] });
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.getByText("Nothing Linked Yet")).toBeVisible();
  state.linksFail = true;
  await page.reload();
  await expect(page.getByText("Could not load linked records")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Request" })).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByText("Could not load linked records")).toBeVisible();
});

test("detail: a draft with no reservation says nothing is reserved and discards without a dialog", async ({ page }) => {
  const state = await stubRequisitions(page, { row: requisition({ estimate: "20.0000" }), lines: [lineRow(1)] });
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.getByText("Nothing is reserved yet.")).toBeVisible();
  await page.getByRole("button", { name: "Discard draft" }).click();
  await expect(page).toHaveURL(/\/requisitions$/);
  expect(state.calls.find(c => c.path.endsWith("/transitions"))?.body).toEqual({ to: "withdrawn" });
});

test("returned banner shows the approver's words and Edit and Resubmit opens the edit route", async ({ page }) => {
  const state = await stubRequisitions(page, { row: requisition({ estimate: "20.0000", reservation: reservation("20.0000"), approval: { instance_id: instanceId, status: "returned" } }), lines: [lineRow(1)] });
  state.decisions = [{ id: "dddddddd-0000-4000-8000-000000000001", instance_id: instanceId, task_id: null, node_id: "n1", step_label: "Manager review", actor_email: "amara@example.test", actor_name: null, on_behalf_of_email: null, on_behalf_of_name: null, authority: "member", outcome: "return", reject_mode: null, comment: "Please cut the second line", document_version: 2, members_at_decision: [], decided_at: "2026-10-04T09:00:00Z" }];
  await page.goto(`/requisitions/${reqId}`);
  const banner = page.getByRole("region", { name: "Returned for Changes" });
  await expect(banner).toContainText("amara@example.test");
  await expect(banner).toContainText("Please cut the second line");
  await expect(banner).toContainText("The reservation is held while you amend.");
  await banner.getByRole("link", { name: /Edit and Resubmit/ }).click();
  await expect(page).toHaveURL(new RegExp(`/requisitions/${reqId}/edit$`));
  await expect(page.locator("#req-title")).toHaveValue("Press brake replacement");
});

test("a 409 nothing_changed on resubmit reads Not resubmitted. Nothing has changed.", async ({ page }) => {
  const state = await stubRequisitions(page, { row: requisition({ estimate: "20.0000", reservation: reservation("20.0000"), approval: { instance_id: instanceId, status: "returned" }, version: 3 }), lines: [lineRow(1, { quantity: "2.0000", unit_price: "10.0000" })] });
  state.submitDetail = "Not resubmitted. Nothing has changed.";
  await page.goto(`/requisitions/${reqId}/edit`);
  await page.getByRole("button", { name: "Submit for approval" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Not submitted" })).toContainText("Not resubmitted. Nothing has changed.");
});

/* ---- Withdraw dialog ---- */
const pending = () => ({ row: requisition({ status: "approval_pending", estimate: "196000.0000", reservation: reservation("196000.0000"), approval: { instance_id: instanceId, status: "running" }, version: 4 }), lines: [lineRow(1)] });

test("withdraw dialog: focus on Keep Requisition, Tab trapped, Escape returns focus to the trigger", async ({ page }) => {
  await stubRequisitions(page, pending());
  await page.goto(`/requisitions/${reqId}`);
  const trigger = page.getByRole("button", { name: "Withdraw requisition" });
  await trigger.click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Releases 196,000.00 USD to Plant renewal, exactly once.");
  await expect(dialog).toContainText("The approval and any sourcing work for REQ-00418 stop");
  await expect(dialog).toContainText("Status becomes Withdrawn.");
  await expect(dialog).not.toContainText("cancelled");
  await expect(dialog).toContainText("amara@example.test");
  await expect(page.getByRole("button", { name: "Keep Requisition" })).toBeFocused();
  for (let i = 0; i < 6; i++) { await page.keyboard.press("Tab"); expect(await dialog.evaluate(d => d.contains(document.activeElement))).toBe(true); }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("withdraw dialog: success releases once and the figures agree; the reason is sent", async ({ page }) => {
  const state = await stubRequisitions(page, pending());
  await page.goto(`/requisitions/${reqId}`);
  await page.getByRole("button", { name: "Withdraw requisition" }).click();
  await page.getByLabel("Reason (optional)").fill("Scope changed");
  await page.getByRole("button", { name: "Withdraw Requisition" }).click();
  await expect(page.locator('.req-heading [data-status="withdrawn"]')).toBeVisible();
  const card = page.locator(".req-card", { hasText: "Project Available" });
  await expect(card.locator("dl > div").nth(0)).toContainText("0.00");
  await expect(card.locator("dl > div").nth(1)).toContainText("196,000.00");
  await expect(page.locator("#where-title")).toBeFocused();
  const call = state.calls.find(c => c.path.endsWith("/transitions"))!;
  expect(call.body).toEqual({ to: "withdrawn", reason: "Scope changed" });
  expect(call.headers["if-match"]).toBe("4");
  await expect(page.getByRole("button", { name: "Withdraw requisition" })).toHaveCount(0);
});

test("withdraw dialog: busy state ignores Escape; failure keeps the reason and Try Again reuses the key", async ({ page }) => {
  const state = await stubRequisitions(page, pending());
  state.transitionFails = true; state.transitionDelay = 400;
  await page.goto(`/requisitions/${reqId}`);
  await page.getByRole("button", { name: "Withdraw requisition" }).click();
  await page.getByLabel("Reason (optional)").fill("Scope changed");
  await page.getByRole("button", { name: "Withdraw Requisition" }).click();
  const busy = page.getByRole("button", { name: "Withdrawing…" });
  await expect(busy).toHaveAttribute("aria-busy", "true");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await expect(page.getByRole("alertdialog").getByText("Not withdrawn")).toBeVisible();
  await expect(page.getByRole("alertdialog")).toContainText("Nothing changed.");
  await expect(page.getByLabel("Reason (optional)")).toHaveValue("Scope changed");
  state.transitionFails = false;
  await page.getByRole("button", { name: "Try Again" }).click();
  await expect(page.locator('.req-heading [data-status="withdrawn"]')).toBeVisible();
  const keys = state.calls.filter(c => c.path.endsWith("/transitions")).map(c => c.headers["idempotency-key"]);
  expect(keys).toHaveLength(2); expect(keys[0]).toBe(keys[1]);
});

test("withdraw dialog: already approved releases nothing and offers only Close", async ({ page }) => {
  const state = await stubRequisitions(page, pending());
  state.transitionConflict = true;
  await page.goto(`/requisitions/${reqId}`);
  await page.getByRole("button", { name: "Withdraw requisition" }).click();
  await page.getByRole("button", { name: "Withdraw Requisition" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByText("Already Approved")).toBeVisible();
  await expect(dialog).toContainText("nothing was released");
  await expect(dialog.getByRole("button", { name: "Withdraw Requisition" })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeFocused();
});

test("a returned draft that holds a reservation gets the dialog; a non-requester sees no Withdraw button", async ({ page }) => {
  const state = await stubRequisitions(page, { row: requisition({ estimate: "20.0000", reservation: reservation("20.0000"), approval: { instance_id: instanceId, status: "returned" } }), lines: [lineRow(1)] });
  await page.goto(`/requisitions/${reqId}`);
  await page.getByRole("button", { name: "Withdraw requisition" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("Releases 20.00 USD");
  await page.keyboard.press("Escape");
  state.withdrawAllowed = false;
  await page.reload();
  await expect(page.getByRole("button", { name: /Withdraw|Discard/ })).toHaveCount(0);
  await expect(page.getByText("Withdraw is not available: Only the requester can withdraw.")).toBeVisible();
});

test("an administrator sees Cancel requisition", async ({ page }) => {
  const state = await stubRequisitions(page, pending());
  state.withdrawAllowed = false; state.cancelAllowed = true;
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.getByRole("button", { name: "Withdraw requisition" })).toHaveCount(0);
  await page.getByRole("button", { name: "Cancel requisition" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("An administrator cancels REQ-00418");
  await expect(page.getByRole("alertdialog")).toContainText("Status becomes Cancelled.");
});

/* ---- List ---- */
test("list: tabs, counts, arrow keys, filter, statuses with icon and words, states", async ({ page }) => {
  const state = await stubRequisitions(page);
  state.list = [requisition({ id: "11111111-aaaa-4aaa-8aaa-000000000001", number: "REQ-00001", title: "Alpha", status: "draft", estimate: "10.0000" }), requisition({ id: "11111111-aaaa-4aaa-8aaa-000000000002", number: "REQ-00002", title: "Beta", status: "approval_pending", estimate: "20.0000" }), requisition({ id: "11111111-aaaa-4aaa-8aaa-000000000003", number: "REQ-00003", title: "Gamma", status: "withdrawn", estimate: "30.0000" })];
  await page.goto("/requisitions");
  await expect(page.locator("tr[data-grid-row]")).toHaveCount(3);
  await expect(page.getByRole("tab", { name: /^Mine/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: /^All/ })).toContainText("3");
  await expect(page.locator('tr[data-grid-row] [data-status="withdrawn"]')).toContainText("Withdrawn");
  await expect(page.locator('tr[data-grid-row] [data-status="withdrawn"] svg')).toHaveCount(1);
  await page.getByRole("tab", { name: /^Mine/ }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /^Drafts/ })).toBeFocused();
  await expect(page).toHaveURL(/tab=drafts/);
  await expect(page.locator("tr[data-grid-row]")).toHaveCount(1);
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: /^All/ })).toHaveAttribute("aria-selected", "true");
  await page.getByLabel("Status filter").selectOption("withdrawn");
  await expect(page.locator("tr[data-grid-row]")).toHaveCount(1);
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.locator("tr[data-grid-row]")).toHaveCount(3);
  await page.getByRole("link", { name: "REQ-00001" }).click();
  await expect(page).toHaveURL(/\/requisitions\/11111111/);
});

test("list: cursor pager shows the page range and the filtered total, and sorting starts again from page 1", async ({ page }) => {
  const state = await stubRequisitions(page);
  state.list = Array.from({ length: 12 }, (_, i) => requisition({ id: `22222222-aaaa-4aaa-8aaa-${String(i).padStart(12, "0")}`, number: `REQ-${String(i + 1).padStart(5, "0")}`, title: `Row ${i + 1}`, status: "draft", estimate: "10.0000" }));
  await page.goto("/requisitions?tab=all");
  await expect(page.locator("tr[data-grid-row]")).toHaveCount(10);
  await expect(page.getByText("Showing 1 to 10 of 12")).toBeVisible();
  await expect(page.locator(".req-total-row")).toContainText("120.00");
  await expect(page.getByRole("button", { name: "Previous page" })).toBeDisabled();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText("Showing 11 to 12 of 12")).toBeVisible();
  await expect(page.locator("tr[data-grid-row]")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Next page" })).toBeDisabled();
  expect(state.calls.filter(c => c.path === "/api/v1/requisitions" && c.query.get("page_size") === "10").some(c => c.query.get("cursor") === "10")).toBe(true);
  await page.getByRole("button", { name: "Previous page" }).click();
  await expect(page.getByText("Showing 1 to 10 of 12")).toBeVisible();
  await page.getByLabel("Status filter").selectOption("draft");
  await expect(page.locator(".req-total-row")).toContainText("120.00");
  await expect(page.getByText("Showing 1 to 10 of 12")).toBeVisible();
});

test("list: error keeps the filters and offers Try again", async ({ page }) => {
  const state = await stubRequisitions(page);
  state.listFails = true;
  await page.goto("/requisitions?tab=all");
  await expect(page.getByText(/The server responded|Loading requisitions failed|unavailable/i).first()).toBeVisible();
});

/* ---- Layout ---- */
test("375px: no sideways scroll, lines stack, targets at least 44px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await stubRequisitions(page);
  await page.goto("/requisitions/new"); await fillHeader(page); await addItem(page, 1, "3", "0.10");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.locator(".req-lines-table thead")).toBeHidden();
  await expect(page.locator(".req-lines-table td[data-label='Quantity']")).toBeVisible();
  for (const control of [page.getByRole("button", { name: "Save draft" }), page.getByRole("button", { name: "Submit for approval" }), page.getByLabel("Remove line 1"), page.getByLabel("Quantity, line 1")]) {
    const box = (await control.boundingBox())!;
    expect(box.height).toBeGreaterThanOrEqual(43.99);
  }
});

for (const width of [1024, 1100, 1280]) {
  test(`${width}px: lines never overflow their card`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await stubRequisitions(page);
    await page.goto("/requisitions/new"); await fillHeader(page); await addItem(page, 1, "3", "0.10", "A long description that wraps rather than overflowing the card it sits in");
    const overflow = await page.locator("#lines-card").evaluate(card => card.scrollWidth - card.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
}

test("detail layout: cards in a row share a height and a table card has no hover outline", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const state = await stubRequisitions(page, { row: requisition({ status: "approval_pending", estimate: "20.0000", reservation: reservation("20.0000") }), lines: [lineRow(1)] });
  state.links = [];
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.locator("#request-title")).toBeVisible();
  const heights = await page.locator(".req-grid-detail > *").evaluateAll(nodes => nodes.map(n => ({ top: Math.round(n.getBoundingClientRect().top), height: Math.round(n.getBoundingClientRect().height) })));
  const firstRow = heights.filter(h => h.top === heights[0]!.top);
  expect(new Set(firstRow.map(h => h.height)).size).toBe(1);
  const lines = page.locator(".req-table-card");
  const before = await lines.evaluate(n => getComputedStyle(n).outlineStyle + getComputedStyle(n).boxShadow);
  await lines.hover();
  expect(await lines.evaluate(n => getComputedStyle(n).outlineStyle + getComputedStyle(n).boxShadow)).toBe(before);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("375px detail: no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await stubRequisitions(page, { row: requisition({ status: "approval_pending", estimate: "20.0000", reservation: reservation("20.0000") }), lines: [lineRow(1)] });
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.locator("#request-title")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

/* ---- axe, both themes ---- */
for (const theme of ["light", "dark"] as const) {
  test(`axe is clean on the list, create, detail, and the open withdraw dialog in ${theme}`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    const state = await stubRequisitions(page, pending());
    state.list = [requisition()];
    const scan = async () => expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.goto("/requisitions"); await expect(page.locator("tr[data-grid-row]").first()).toBeVisible(); await scan();
    await page.goto("/requisitions/new"); await page.locator("#req-title").waitFor(); await fillHeader(page); await addItem(page, 1, "3", "0.10"); await scan();
    state.row = requisition({ status: "approval_pending", estimate: "196000.0000", reservation: reservation("196000.0000"), approval: { instance_id: instanceId, status: "running" }, version: 4 });
    await page.goto(`/requisitions/${reqId}`); await expect(page.locator("#request-title")).toBeVisible(); await scan();
    await page.getByRole("button", { name: "Withdraw requisition" }).click();
    await expect(page.getByRole("alertdialog")).toBeVisible(); await scan();
  });
}
