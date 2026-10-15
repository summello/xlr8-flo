import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { amendmentRead, justificationChange, quantityChange, stubChanges } from "./requisition-changes-fixture";
import { instanceId, lineRow, requisition, reservation, reqId } from "./requisitions-fixture";

const approved = () => requisition({ status: "sourcing_in_progress", estimate: "20.0000", reservation: reservation("20.0000"), approval: { instance_id: instanceId, status: "approved" }, version: 5 });
const start = async (page: Page, over: Parameters<typeof stubChanges>[1] extends infer T ? Partial<T> : never = {}) => {
  const state = await stubChanges(page, { row: approved(), lines: [lineRow(1)], changes: { ...justificationChange, to: "amendment", routing: "not_applicable" }, ...over });
  return state;
};

test("Amend opens the editor, an edited line is saved with If-Match, a non-material amendment is applied", async ({ page }) => {
  const state = await start(page);
  await page.goto(`/requisitions/${reqId}`);
  await page.getByRole("link", { name: "Amend" }).click();
  await expect(page.getByRole("heading", { name: "Amendment Details" })).toBeVisible();
  expect(state.calls.some(c => c.method === "POST" && c.path.endsWith("/amendments"))).toBe(true);
  await expect(page.getByText("The project cannot change in an amendment")).toBeVisible();
  await page.getByLabel("Quantity, line 1").fill("3");
  await expect(page.getByText("Unsaved changes")).toBeVisible();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Amendment saved.")).toBeVisible();
  const put = state.calls.find(c => c.method === "PUT")!;
  expect(put.headers["if-match"]).toBe("1");
  expect(put.headers["idempotency-key"]).toBeTruthy();
  await page.getByRole("button", { name: "Review Changes" }).click();
  await expect(page).toHaveURL(new RegExp(`/requisitions/${reqId}/amend/review$`));
  await expect(page.getByRole("heading", { name: "What Changed" })).toBeVisible();
  await page.getByRole("button", { name: "Submit Amendment" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Applied. The requisition is now Version 2" })).toBeFocused();
});

test("a material amendment says it was sent for approval and the approved version stays in force", async ({ page }) => {
  const state = await start(page, { amendment: amendmentRead(), changes: { ...quantityChange, to: "amendment", routing: "not_applicable" } });
  state.amendMaterial = true;
  await page.goto(`/requisitions/${reqId}/amend/review`);
  await expect(page.getByText("Material Change", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Note" }).fill("Bigger order");
  await page.getByRole("button", { name: "Submit Amendment" }).click();
  const done = page.getByRole("status").filter({ hasText: "Sent for approval" });
  await expect(done).toBeFocused();
  await expect(done).toContainText("The approved version stays in force until it is approved");
  expect(state.calls.find(c => c.path.endsWith("/submit"))?.body).toEqual({ note: "Bigger order" });
});

test("amendment review compares against the applied version the amendment was opened on", async ({ page }) => {
  const state = await start(page, { amendment: amendmentRead({ revision_no: 4 }), changes: { ...quantityChange, to: "amendment", routing: "not_applicable" } });
  state.revisions = [
    { revision_no: 1, kind: "submission", status: "applied", created_at: "2026-10-02T00:00:00Z", created_by: "bbbbbbbb-0000-4000-8000-000000000001", note: null },
    { revision_no: 2, kind: "amendment", status: "returned", created_at: "2026-10-03T00:00:00Z", created_by: "bbbbbbbb-0000-4000-8000-000000000001", note: null },
    { revision_no: 3, kind: "amendment", status: "applied", created_at: "2026-10-04T00:00:00Z", created_by: "bbbbbbbb-0000-4000-8000-000000000001", note: null },
  ];
  await page.goto(`/requisitions/${reqId}/amend/review`);
  await expect(page.getByRole("heading", { name: "What Changed" })).toBeVisible();
  expect([...new Set(state.calls.filter(c => c.path.endsWith("/changes")).map(c => c.search))]).toEqual(["?against=3"]);
});

test("UX-003: leaving /amend with unsaved edits asks first", async ({ page }) => {
  await start(page, { amendment: amendmentRead() });
  await page.goto(`/requisitions/${reqId}/amend`);
  await page.getByLabel("Quantity, line 1").fill("4");
  const messages: string[] = [];
  page.on("dialog", d => { messages.push(d.message()); void d.dismiss(); });
  await page.getByRole("link", { name: "Back to requisition" }).click();
  expect(messages).toEqual(["You have unsaved changes. Leave this page and discard them?"]);
  await expect(page).toHaveURL(new RegExp(`/amend$`));
});

test("a saved amendment leaves without asking", async ({ page }) => {
  await start(page, { amendment: amendmentRead() });
  await page.goto(`/requisitions/${reqId}/amend`);
  await page.getByLabel("Quantity, line 1").fill("4");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Amendment saved.")).toBeVisible();
  page.on("dialog", d => { void d.dismiss(); throw new Error("asked"); });
  await page.getByRole("link", { name: "Back to requisition" }).click();
  await expect(page).toHaveURL(new RegExp(`/requisitions/${reqId}$`));
});

test("Discard Amendment asks, focus starts on Keep Amendment, and discarding removes the draft", async ({ page }) => {
  const state = await start(page, { amendment: amendmentRead() });
  await page.goto(`/requisitions/${reqId}/amend`);
  await page.getByRole("button", { name: "Discard Amendment" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByRole("button", { name: "Keep Amendment" })).toBeFocused();
  await dialog.getByRole("button", { name: "Discard Amendment" }).click();
  await expect(page).toHaveURL(new RegExp(`/requisitions/${reqId}$`));
  expect(state.amendment).toBeNull();
  await expect(page.getByRole("link", { name: "Amend" })).toBeVisible();
});

test("the detail page offers Continue amendment for a draft and a read-only note for a pending one", async ({ page }) => {
  const state = await start(page, { amendment: amendmentRead() });
  await page.goto(`/requisitions/${reqId}`);
  await expect(page.getByRole("link", { name: "Continue amendment" })).toBeVisible();
  state.amendment = amendmentRead({ status: "pending" });
  state.sync();
  await page.reload();
  await expect(page.getByText("Amendment pending approval")).toBeVisible();
  await expect(page.getByRole("link", { name: /Amend/ })).toHaveCount(0);
});

test("opening an amendment that the server refuses shows the server's reason", async ({ page }) => {
  await start(page);
  await page.route(`**/api/v1/requisitions/${reqId}/amendments`, route => route.request().method() === "POST"
    ? route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "Sourcing has started an RFQ.", status: 409, correlation_id: "ref-9", type: "about:blank", title: "Conflict" }) }) : route.fallback());
  await page.goto(`/requisitions/${reqId}/amend`);
  await expect(page.getByRole("alert")).toContainText("Sourcing has started an RFQ.");
});

for (const theme of ["light", "dark"] as const) {
  for (const route of ["amend", "amend/review"]) {
    test(`${route} passes axe in ${theme} theme`, async ({ page }) => {
      await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
      await start(page, { amendment: amendmentRead(), changes: { ...quantityChange, to: "amendment", routing: "not_applicable" } });
      await page.goto(`/requisitions/${reqId}/${route}`);
      await expect(page.getByRole("heading", { name: route === "amend" ? "Amendment Details" : "What Changed" })).toBeVisible();
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });
  }
}
