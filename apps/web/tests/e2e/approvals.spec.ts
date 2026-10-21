import { expect, test, type Page } from "@playwright/test";

import { inboxRow, stubApprovals } from "./approvals-fixture";

const open = async (page: Page, tab = "pending") => {
  await page.goto(`/approvals?tab=${tab}`);
  await expect(page.getByRole("heading", { name: "Approval Inbox" })).toBeVisible();
};
const openPanel = async (page: Page, name: RegExp | string) => {
  await page.getByRole("button", { name }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("button", { name: "Close", exact: true }).first()).toBeFocused();
};

test("tabs carry counts, the tab is in the URL and arrows, Home and End move between them", async ({ page }) => {
  await stubApprovals(page);
  await open(page);
  await expect(page.getByRole("tab", { name: /Pending/ })).toContainText("3");
  await expect(page.getByRole("tab", { name: /Overdue/ })).toContainText("1");
  await expect(page.getByRole("tab", { name: /Completed/ })).toContainText("1");
  await page.getByRole("tab", { name: /Pending/ }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /Overdue/ })).toBeFocused();
  await expect(page.getByRole("tab", { name: /Overdue/ })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/tab=overdue/);
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: /Delegated/ })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Home");
  await expect(page.getByRole("tab", { name: /Pending/ })).toBeFocused();
  await page.goto("/approvals?tab=completed");
  await expect(page.getByRole("tab", { name: /Completed/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("On behalf of owner@example.test")).toBeVisible();
});

test("the total is the server's figure, overdue is in words, and sorting is on the server", async ({ page }) => {
  const state = await stubApprovals(page);
  state.totalAmount = [{ amount: "9999.0000", currency: "USD" }];
  await open(page);
  await expect(page.getByText(/Total waiting/)).toContainText("9,999.00");
  await expect(page.getByText(/Total waiting/)).not.toContainText("601.50");
  await expect(page.getByText("Showing 3 of 3")).toBeVisible();
  await expect(page.getByText(/\d+ days? overdue/)).toBeVisible();
  const amount = page.getByRole("columnheader", { name: /Amount/ });
  await expect(amount).toHaveAttribute("aria-sort", "none");
  await amount.getByRole("button").click();
  await expect(amount).toHaveAttribute("aria-sort", "ascending");
  await amount.getByRole("button").click();
  await expect(amount).toHaveAttribute("aria-sort", "descending");
  expect(state.tasksQueries.at(-1)?.get("sort")).toBe("amount");
  expect(state.tasksQueries.at(-1)?.get("direction")).toBe("desc");
  expect(state.tasksQueries.every((q) => !q.has("cursor") || q.get("cursor") === "page2")).toBe(true);
});

test("loading, empty per tab, no match, error with filters kept, and a partial page", async ({ page }) => {
  const state = await stubApprovals(page);
  state.mode = "loading";
  await page.goto("/approvals");
  await expect(page.getByRole("status", { name: "Loading approvals" })).toBeVisible();
  state.mode = "empty";
  await page.reload();
  await expect(page.getByRole("heading", { name: "All Caught Up" })).toBeVisible();
  for (const [tab, title] of [["overdue", "Nothing Overdue"], ["completed", "No Decisions Yet"], ["delegated", "Nothing Delegated"]] as const) {
    await page.getByRole("tab", { name: new RegExp(tab, "i") }).click();
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
  }
  state.mode = "ok";
  await page.getByRole("tab", { name: /Pending/ }).click();
  await page.getByRole("searchbox", { name: "Search" }).fill("zzz");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("heading", { name: "No Approvals Match" })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).first().click();
  await expect(page.getByText("Press brake 1")).toBeVisible();
  state.mode = "error";
  await page.getByRole("searchbox", { name: "Search" }).fill("REQ");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Could not load your approvals");
  await expect(page.getByRole("alert")).toContainText("ref-1234");
  await expect(page.getByRole("searchbox", { name: "Search" })).toHaveValue("REQ");
  state.mode = "ok";
  state.pageTwo = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Some approvals did not load" })).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
});

test("the panel opens on Close, treats notes as text, and Escape returns focus to the row", async ({ page }) => {
  await stubApprovals(page);
  await open(page);
  await openPanel(page, /REQ-00401/);
  await expect(page.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
  await expect(page.getByText("<b>Needed</b> by Friday")).toBeVisible();
  await expect(page.getByRole("dialog").locator("b")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /Decision Trail/ })).toBeVisible();
  const order = await page.getByRole("dialog").locator("h3, summary").allTextContents();
  expect(order.findIndex((t) => t.includes("Decision Trail"))).toBeLessThan(order.findIndex((t) => t.includes("Why It Reached You")));
  await expect(page.getByText("Why It Reached You")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /REQ-00401/ })).toBeFocused();
});

test("a separation-of-duty exclusion is visible text, not a tooltip, and a closed task offers no decision", async ({ page }) => {
  const state = await stubApprovals(page);
  await open(page);
  await openPanel(page, /REQ-00401/);
  await page.getByText("Why It Reached You").click();
  const fold = page.locator(".appr-fold");
  await expect(fold).toContainText("requester@example.test excluded: Requested this document, so cannot approve it");
  await expect(fold.getByText(/requester@example.test excluded/)).toBeVisible();
  expect(await fold.locator("[title]").count()).toBe(0);
  await page.keyboard.press("Escape");
  state.closedInstance = true;
  await openPanel(page, /REQ-00402/);
  await expect(page.getByRole("dialog").getByText("Already Decided")).toBeVisible();
  for (const name of ["Approve", "Reject", "Return for Changes", "Post Comment"]) await expect(page.getByRole("button", { name })).toHaveCount(0);
});

test("the explanation fails on its own and decisions stay possible", async ({ page }) => {
  const state = await stubApprovals(page);
  state.explanationFails = true;
  await open(page);
  await openPanel(page, /REQ-00401/);
  await expect(page.getByText("This explanation could not load")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toBeEnabled();
  state.explanationFails = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByText("Capital Review applies to requisitions.")).toBeVisible();
});

test("Approve is never disabled, a missing reason and reject mode are refused locally, and reject sends its mode", async ({ page }) => {
  const state = await stubApprovals(page);
  await open(page);
  await openPanel(page, /REQ-00401/);
  for (const name of ["Post Comment", "Return for Changes", "Reject", "Approve"]) await expect(page.getByRole("button", { name })).toBeEnabled();
  await page.getByRole("button", { name: "Return for Changes" }).click();
  await expect(page.getByRole("alert")).toContainText("Not decided. A reason is needed.");
  await expect(page.getByRole("alert")).toBeFocused();
  expect(state.posts).toHaveLength(0);
  await page.getByLabel(/Comment or Reason/).fill("Cost is too high");
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Choose whether to return the request or reject it permanently.")).toBeVisible();
  expect(state.posts).toHaveLength(0);
  await page.getByLabel("Return to the requester to fix").check();
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByRole("status")).toContainText("Rejected");
  expect(state.posts[0]?.body).toMatchObject({ outcome: "reject", reject_mode: "return_to_draft", comment: "Cost is too high", document_version: 1 });
  expect(state.posts[0]?.headers["idempotency-key"]).toBeTruthy();
});

test("a stale version keeps the comment, offers the new version, and a closed task offers no buttons", async ({ page }) => {
  const state = await stubApprovals(page);
  state.decision = "stale";
  await open(page);
  await openPanel(page, /REQ-00401/);
  await page.getByLabel(/Comment or Reason/).fill("Looks fine");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("alert")).toContainText("This requisition changed");
  await expect(page.getByRole("alert")).toBeFocused();
  await expect(page.getByLabel(/Comment or Reason/)).toHaveValue("Looks fine");
  await page.getByRole("button", { name: "Load Version 2" }).click();
  await expect(page.getByText("Loaded version 2")).toBeVisible();
  await expect(page.getByLabel(/Comment or Reason/)).toHaveValue("Looks fine");
  state.decision = "closed";
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("alert")).toContainText("Already Decided");
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reject" })).toHaveCount(0);
});

test("a decision the caller already made shows a visible notice and no decision buttons", async ({ page }) => {
  await stubApprovals(page);
  await open(page, "completed");
  await openPanel(page, /REQ-00404/);
  await expect(page.getByRole("dialog").getByText("You Decided This")).toBeVisible();
  for (const name of ["Approve", "Reject", "Return for Changes", "Post Comment"]) await expect(page.getByRole("button", { name })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Close", exact: true }).first()).toBeVisible();
});

test("keyboard only: tabs, open a row, comment, approve, reach Next", async ({ page }) => {
  const state = await stubApprovals(page);
  await open(page);
  await page.getByRole("tab", { name: /Pending/ }).focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowLeft");
  await page.getByRole("button", { name: /REQ-00401/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Close", exact: true }).first()).toBeFocused();
  await page.getByLabel(/Comment or Reason/).focus();
  await page.keyboard.type("Approved with thanks");
  await page.getByRole("button", { name: "Approve" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Back to Inbox" })).toBeFocused();
  await expect(page.getByRole("status")).toContainText("Recorded: you");
  await expect(page.getByRole("status")).toContainText("Decisions cannot be edited.");
  expect(state.posts[0]?.body).toMatchObject({ outcome: "approve", comment: "Approved with thanks" });
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: /^Next: REQ-00402/ })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog").getByRole("heading", { name: "Press brake 2" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Close", exact: true }).first()).toBeFocused();
});

test("the final step of a requisition offers an optional project and says what happens when empty", async ({ page }) => {
  const state = await stubApprovals(page);
  state.pending = [inboxRow(1, { step: { index: 2, of: 2 } })];
  await page.route("**/api/v1/projects?**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [{ id: "bbbbbbbb-0000-4000-8000-000000000001", number: "PRJ-0001", name: "Plant renewal" }], total: 1, next_cursor: null }) }));
  await open(page);
  await openPanel(page, /REQ-00401/);
  await expect(page.getByText("a new project is created and funded with the approved amount")).toBeVisible();
  await page.getByLabel(/Assign to an existing project/).fill("PRJ-0001 Plant renewal");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("status")).toContainText("Approved");
  expect(state.posts[0]?.body.assign_project_id).toBe("bbbbbbbb-0000-4000-8000-000000000001");
});

test("availability: the summary lists problems, values are kept, a good save lists the period and revoke asks first", async ({ page }) => {
  const state = await stubApprovals(page);
  await page.goto("/approvals/availability");
  await expect(page.getByRole("heading", { name: "Approval Availability" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Organization" })).toHaveCount(0);
  await page.getByLabel(/^Reason/).fill("Conference");
  await page.getByRole("button", { name: "Save Out-of-Office" }).click();
  const summary = page.getByRole("alert").first();
  await expect(summary).toContainText("Not saved. 3 problems.");
  await expect(summary).toBeFocused();
  await expect(page.getByLabel(/^Reason/)).toHaveValue("Conference");
  await summary.getByRole("link", { name: /Choose the first day/ }).click();
  await expect(page.getByLabel(/^Starts/)).toBeFocused();
  await expect(page.getByLabel(/^Starts/)).toHaveAttribute("aria-invalid", "true");
  await page.getByLabel(/^Starts/).fill("2030-02-01");
  await page.getByLabel(/^Ends/).fill("2030-02-05");
  await page.getByLabel(/^Pass Approvals To/).selectOption({ label: "delegate@example.test" });
  await page.getByLabel(/^Reason/).fill("overlap");
  await page.getByRole("button", { name: "Save Out-of-Office" }).click();
  await expect(page.getByRole("alert").first()).toContainText("Overlap");
  await expect(page.getByLabel(/^Starts/)).toHaveValue("2030-02-01");
  await page.getByLabel(/^Reason/).fill("Conference");
  await page.getByRole("button", { name: "Save Out-of-Office" }).click();
  await expect(page.getByRole("status").first()).toContainText("Approvals pass to delegate@example.test");
  expect(String(state.posts.at(-1)?.body.starts_at)).toMatch(/^2030-0[12]-\d\dT/);
  await expect(page.getByRole("row", { name: /delegate@example.test/ })).toContainText("Upcoming");
  await page.getByRole("button", { name: /^Revoke/ }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await expect(page.getByRole("button", { name: "Keep Delegation" })).toBeFocused();
  await expect(page.getByRole("alertdialog")).toContainText("Tasks still undecided come back to you");
  await page.getByRole("button", { name: "Revoke Delegation" }).click();
  await expect(page.getByRole("row", { name: /delegate@example.test/ })).toContainText("Revoked");
  await expect(page.getByRole("row", { name: /delegate@example.test/ })).toContainText("Kept in the record");
});

test("the organization tab appears for administrators", async ({ page }) => {
  await stubApprovals(page, { delegateAdmin: true });
  await page.goto("/approvals/availability");
  await expect(page.getByRole("tab", { name: "Organization" })).toBeVisible();
});

test("eligibility is requested for the whole selection and decisions on my behalf render every state", async ({ page }) => {
  const state = await stubApprovals(page);
  state.behalfFails = true;
  await page.goto("/approvals/availability");
  await expect(page.getByRole("heading", { name: "Decided on Your Behalf" })).toBeVisible();
  await expect(page.getByText("Could not load these decisions")).toBeVisible();
  state.behalfFails = false;
  await page.getByRole("button", { name: "Try again" }).first().click();
  await expect(page.getByText("No one has decided anything on your behalf.")).toBeVisible();
  state.behalf = [{ id: "cccccccc-0000-4000-8000-000000000001", instance_id: "cccccccc-0000-4000-8000-000000000002", task_id: null, node_id: "one", step_label: "Finance", actor_email: "delegate@example.test", on_behalf_of_email: "me@example.test", authority: "delegate", outcome: "approve", reject_mode: null, comment: "Fine by me", document_version: 1, members_at_decision: [], decided_at: "2026-10-12T10:00:00Z" }];
  await page.reload();
  const table = page.getByRole("table", { name: "Decisions made on your behalf" });
  await expect(table).toContainText("delegate@example.test");
  await expect(table).toContainText("Finance");
  await page.getByLabel("Only some types").check();
  await page.getByLabel("Requisitions").check();
  await page.getByLabel("Purchase orders").check();
  await expect.poll(() => state.eligibleQueries.at(-1)?.getAll("document_types").sort()).toEqual(["po", "requisition"]);
  await expect(page.getByText("No one else may approve everything you cover")).toBeVisible();
});

test("a failed revoke keeps the dialog open and says nothing changed", async ({ page }) => {
  const state = await stubApprovals(page);
  await page.goto("/approvals/availability");
  await page.getByLabel(/^Starts/).fill("2030-02-01");
  await page.getByLabel(/^Ends/).fill("2030-02-05");
  await page.getByLabel(/^Pass Approvals To/).selectOption({ label: "delegate@example.test" });
  await page.getByRole("button", { name: "Save Out-of-Office" }).click();
  await page.getByRole("button", { name: /^Revoke/ }).click();
  state.revokeFails = true;
  await page.getByRole("button", { name: "Revoke Delegation" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("Not revoked");
  await expect(page.getByRole("alertdialog")).toContainText("The delegation is unchanged");
  state.revokeFails = false;
  await page.getByRole("button", { name: "Revoke Delegation" }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(page.getByRole("row", { name: /delegate@example.test/ })).toContainText("Revoked");
});

// E11-S10: the shared decision panel at phone width. Self-contained so it merges beside other helper edits.
const footer = (page: Page) => page.locator(".appr-sheet-body .appr-actions");
type Btn = { name: string; x: number; y: number; w: number; h: number; clipped: boolean };
const footerButtons = async (page: Page) => footer(page).locator("button").evaluateAll((els) => els.map((el) => {
  const r = el.getBoundingClientRect();
  return { name: (el.textContent ?? "").trim(), x: r.x, y: r.y, w: r.width, h: r.height, clipped: el.scrollWidth > el.clientWidth };
}));

test("at 375 the footer is three rows in reading order, 44px targets, no scroll, close is not covered", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await stubApprovals(page);
  await open(page);
  await openPanel(page, /REQ-00401/);
  const names = ["Close", "Post Comment", "Return for Changes", "Reject", "Approve"];
  const [close, post, ret, reject, approve] = (await footerButtons(page)) as [Btn, Btn, Btn, Btn, Btn];
  expect([close, post, ret, reject, approve].map((b) => b.name)).toEqual(names);
  expect(close.y).toBe(post.y);
  expect(close.x).toBeLessThan(post.x);
  expect(ret.y).toBe(reject.y);
  expect(ret.x).toBeLessThan(reject.x);
  expect(ret.y).toBeGreaterThan(close.y);
  expect(approve.y).toBeGreaterThan(ret.y);
  for (const b of [close, post, ret, reject, approve]) { expect(b.h).toBeGreaterThanOrEqual(44); expect(b.clipped).toBe(false); }
  // The footer contains its buttons (a fixed height would let them overflow it).
  expect(await footer(page).evaluate((el) => el.scrollHeight <= el.clientHeight)).toBe(true);
  expect(approve.w).toBeGreaterThanOrEqual(Math.max(close.w, post.w, ret.w, reject.w));
  expect(await page.locator(".appr-sheet").evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  // The header close button is the element at its own centre.
  const hit = await page.locator(".appr-close").evaluate((el) => {
    const r = el.getBoundingClientRect();
    return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.closest(".appr-close") === el;
  });
  expect(hit).toBe(true);
  // Tab order equals the visual order.
  await page.getByLabel("Comment or Reason").focus();
  const seen: string[] = [];
  for (let i = 0; i < 12 && seen.length < 5; i += 1) {
    await page.keyboard.press("Tab");
    const label = await page.evaluate(() => document.activeElement?.closest(".appr-actions") ? (document.activeElement.textContent ?? "").trim() : "");
    if (label) seen.push(label);
  }
  expect(seen).toEqual(names);
});

test("at 768 the close button is not covered by the shell", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 900 });
  await stubApprovals(page);
  await open(page);
  await openPanel(page, /REQ-00401/);
  const hit = await page.locator(".appr-close").evaluate((el) => {
    const r = el.getBoundingClientRect();
    return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.closest(".appr-close") === el;
  });
  expect(hit).toBe(true);
});

test("at 375 a task the caller cannot decide shows Close and Post Comment's place only, without a gap", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await stubApprovals(page);
  await open(page, "completed");
  await openPanel(page, /REQ-00404/);
  const buttons = await footerButtons(page);
  expect(buttons.map((b) => b.name)).toEqual(["Close"]);
  expect(buttons[0]!.x).toBeLessThan(40);
});

test("at 1440 the footer is one right-aligned flex row", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await stubApprovals(page);
  await open(page);
  await openPanel(page, /REQ-00401/);
  await expect(footer(page)).toHaveCSS("display", "flex");
  const ys = (await footerButtons(page)).map((b) => Math.round(b.y));
  expect(new Set(ys).size).toBe(1);
});
