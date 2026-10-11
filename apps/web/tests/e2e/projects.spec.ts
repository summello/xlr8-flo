import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Locator } from "@playwright/test";
import { firstActor, project, projectId, secondActor, stubProjects, unitId } from "./projects-fixture";

async function createFields(page: Page) {
  for (const [label, value] of [["Business unit", unitId], ["Project name", "Plant renewal"], ["Department", "D"], ["Ledger account", "L"], ["Currency", "USD"], ["Description", "Preserved description"], ["Planned start", "2026-10-01"], ["Planned end", "2026-12-31"]]) await page.getByLabel(new RegExp(`^${label}\\s*\\*?$`)).fill(value!);
}
async function keyboardFocus(page: Page, locator: Locator) {
  for (let i = 0; i < 150; i++) {
    await page.keyboard.press("Tab");
    if (await locator.evaluate(node => node === document.activeElement)) {
      expect(await locator.evaluate(node => getComputedStyle(node).outlineStyle)).not.toBe("none");
      return;
    }
  }
  throw new Error("Control is unreachable by keyboard");
}

test("create, list, submit and approve with a second identity sends version and idempotency headers", async ({ page }) => {
  const state = await stubProjects(page);
  await page.goto("/projects/new"); await createFields(page);
  await page.getByRole("button", { name: "Create project", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Plant renewal");
  await page.goto("/projects");
  await expect(page.getByRole("link", { name: "Plant renewal", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Plant renewal", exact: true }).click();
  await page.getByRole("button", { name: "Approval pending", exact: true }).click();
  await expect(page.locator('.project-record-heading [data-status="approval_pending"]')).toBeVisible();
  await expect(page.getByRole("button", { name: "Active", exact: true })).toBeDisabled();
  await expect(page.getByText("The submitter cannot approve this project. Ask a different approver.")).toBeVisible();
  const own = await page.evaluate(async id => (await fetch(`/api/v1/projects/${id}/transitions`, { method: "POST", headers: { "Content-Type": "application/json", "If-Match": "2", "Idempotency-Key": "test-maker" }, body: JSON.stringify({ to: "active" }) })).status, projectId);
  expect(own).toBe(403);
  state.actor = secondActor; await page.reload();
  await page.getByRole("button", { name: "Active", exact: true }).click();
  await expect(page.locator('.project-record-heading [data-status="active"]')).toBeVisible();
  await page.getByRole("button", { name: "Deferred", exact: true }).click();
  await page.getByLabel(/^Reason\s*\*?$/).fill("Paused for planned redesign");
  await page.getByRole("button", { name: "Confirm transition" }).click();
  await expect(page.locator('.project-record-heading [data-status="deferred"]')).toBeVisible();
  const writes = state.requests.filter(r => r.method === "POST" && r.path.endsWith("/transitions") && r.headers["idempotency-key"] !== "test-maker");
  expect(writes.map(r => r.body?.to)).toEqual(["approval_pending", "active", "deferred"]);
  expect(writes.map(r => r.headers["if-match"])).toEqual(["1", "2", "3"]);
  expect(writes.every(r => !!r.headers["idempotency-key"])).toBe(true);
  expect(writes.at(-1)?.body?.reason).toBe("Paused for planned redesign");
});

test("keyboard-only create, submit, second-user approval and Escape return focus", async ({ page }) => {
  const state = await stubProjects(page);
  await page.goto("/projects/new"); await page.locator(".project-form").waitFor();
  for (const [label, value] of [["Business unit", unitId], ["Project name", "Plant renewal"], ["Department", "D"], ["Ledger account", "L"], ["Currency", "USD"]]) {
    await keyboardFocus(page, page.getByLabel(new RegExp(`^${label}\\s*\\*?$`))); await page.keyboard.type(value!);
  }
  await keyboardFocus(page, page.getByRole("button", { name: "Create project", exact: true })); await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(projectId));
  await keyboardFocus(page, page.getByRole("button", { name: "Approval pending", exact: true })); await page.keyboard.press("Enter");
  await expect(page.locator('.project-record-heading [data-status="approval_pending"]')).toBeVisible();
  state.actor = secondActor; await page.reload();
  await keyboardFocus(page, page.getByRole("button", { name: "Active", exact: true })); await page.keyboard.press("Enter");
  await expect(page.locator('.project-record-heading [data-status="active"]')).toBeVisible();
  const deferred = page.getByRole("button", { name: "Deferred", exact: true });
  await keyboardFocus(page, deferred); await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible(); await page.keyboard.type("Keep this reason");
  await page.keyboard.press("Escape"); await expect(page.getByRole("dialog")).toHaveCount(0); await expect(deferred).toBeFocused();
});

test("422 preserves every create field and focuses the error summary", async ({ page }) => {
  const state = await stubProjects(page); state.failCreate = true;
  await page.goto("/projects/new"); await createFields(page);
  await page.getByLabel(/^Department\s*\*?$/).fill("INACTIVE");
  await page.getByRole("button", { name: "Create project", exact: true }).click();
  await expect(page.locator(".error-summary")).toBeFocused();
  await expect(page.getByLabel(/^Department\s*\*?$/)).toHaveValue("INACTIVE");
  await expect(page.getByLabel(/^Department\s*\*?$/)).toHaveAttribute("aria-invalid", "true");
  for (const [label, value] of [["Business unit", unitId], ["Project name", "Plant renewal"], ["Ledger account", "L"], ["Currency", "USD"], ["Description", "Preserved description"], ["Planned start", "2026-10-01"], ["Planned end", "2026-12-31"]]) await expect(page.getByLabel(new RegExp(`^${label}\\s*\\*?$`))).toHaveValue(value!);
  state.failCreate = false; await page.getByLabel(/^Department\s*\*?$/).fill("D");
  await page.getByRole("button", { name: "Create project", exact: true }).click(); await expect(page).toHaveURL(new RegExp(projectId));
});

test("reachable-only controls show visible disabled reasons and enable closure override", async ({ page }) => {
  const state = await stubProjects(page, { transitions: [
    { to: "draft", reachable: false, allowed: false, reason_required: false, override_available: false, blocked_reasons: ["Invalid transition"] },
    { to: "approval_pending", reachable: true, allowed: false, reason_required: false, override_available: false, blocked_reasons: ["Complete required project fields before submitting."] },
    { to: "abandoned", reachable: true, allowed: false, reason_required: true, override_available: true, blocked_reasons: ["Reserved 40.0000 USD blocks closure."] },
  ] }); state.overrideDenied = true;
  await page.goto(`/projects/${projectId}`);
  await expect(page.getByRole("button", { name: "Approval pending", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Draft", exact: true })).toHaveCount(0);
  await expect(page.getByText("Complete required project fields before submitting.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Abandoned", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Abandoned", exact: true }).click();
  await expect(page.getByRole("dialog").getByText("Reserved 40.0000 USD blocks closure.")).toBeVisible();
  await expect(page.getByText(/at least 20 characters/)).toBeVisible();
  await page.getByLabel(/^Reason\s*\*?$/).fill("short");
  await page.getByLabel("Override closure").check();
  await page.getByRole("button", { name: "Confirm transition" }).click();
  expect(state.requests.filter(r => r.path.endsWith("/transitions"))).toHaveLength(0);
  await page.getByLabel(/^Reason\s*\*?$/).fill("Authorized closure explanation");
  await page.getByRole("button", { name: "Confirm transition" }).click();
  await expect(page.getByRole("alert")).toContainText("You need project.close.override");
  await expect(page.getByLabel(/^Reason\s*\*?$/)).toHaveValue("Authorized closure explanation");
  expect(state.requests.filter(r => r.path.endsWith("/transitions")).at(-1)?.body?.override).toBe(true);
  state.overrideDenied = false; state.stale = true;
  await page.getByRole("button", { name: "Confirm transition" }).click();
  await expect(page.getByRole("button", { name: "Reload project" })).toBeVisible();
  await page.getByRole("button", { name: "Reload project" }).click();
  await expect(page.getByLabel(/^Reason\s*\*?$/)).toHaveValue("Authorized closure explanation");
});

test("grouping labels the first in each run, disables sorting and saves filter state", async ({ page }) => {
  const rows = [{ ...project, status: "active" as const }, { ...project, id: secondActor, number: "PRJ-0002", status: "active" as const, available: null }, { ...project, id: firstActor, number: "PRJ-0003" }];
  const state = await stubProjects(page, { rows });
  await page.goto("/projects"); await page.locator("tr[data-grid-row]").first().waitFor();
  await page.getByLabel(/^Group by\s*\*?$/).selectOption("status");
  await expect(page.locator(".project-group-start")).toHaveCount(2);
  await expect(page.locator(".project-group-start").first()).toHaveText("Group: Active");
  await expect(page.locator("tr[data-grid-row]").nth(1).locator('[data-column-id="group"]')).toBeEmpty();
  await expect(page.locator("th[aria-sort]")).toHaveCount(0);
  await expect(page.getByText("Sorting is disabled while grouped.", { exact: false })).toBeVisible();
  await page.getByLabel(/^Status filter\s*\*?$/).selectOption("active");
  await page.getByLabel(/^Business unit filter\s*\*?$/).selectOption(unitId);
  await page.getByLabel(/^Search projects\s*\*?$/).fill("Plant"); await page.getByRole("button", { name: "Apply filter" }).click();
  await page.getByLabel("View name").fill("Active infrastructure"); await page.getByRole("button", { name: "Save view", exact: true }).click();
  await page.getByLabel(/^Group by\s*\*?$/).selectOption(""); await page.getByLabel(/^Status filter\s*\*?$/).selectOption("");
  await page.getByLabel(/^Business unit filter\s*\*?$/).selectOption("");
  await page.getByLabel(/^Saved view\s*\*?$/).selectOption({ label: "Active infrastructure" });
  await expect(page.getByLabel(/^Group by\s*\*?$/)).toHaveValue("status");
  await expect(page.getByLabel(/^Status filter\s*\*?$/)).toHaveValue("active");
  await expect(page.getByLabel(/^Business unit filter\s*\*?$/)).toHaveValue(unitId);
  await page.reload(); await page.getByLabel(/^Saved view\s*\*?$/).selectOption({ label: "Active infrastructure" });
  await expect(page.getByLabel(/^Search projects\s*\*?$/)).toHaveValue("Plant");
  expect(state.requests.filter(r => r.path === "/api/v1/projects").every(r => r.query.get("page_size") === "50")).toBe(true);
});

test("money cells are exact, signed, iconic, tabular and right aligned; null means no balance access", async ({ page }) => {
  await stubProjects(page, { rows: [project, { ...project, id: secondActor, available: null, allocated: null }] });
  await page.goto("/projects"); await page.locator("tr[data-grid-row]").first().waitFor();
  const cell = page.locator('[data-grid-row="0"] [data-column-id="available"]');
  await expect(cell).toContainText("(-0.01)"); await expect(cell).toContainText("USD"); await expect(cell.locator("svg")).toHaveCount(1);
  expect(await cell.evaluate(n => getComputedStyle(n).textAlign)).toBe("right");
  expect(await cell.evaluate(n => getComputedStyle(n).fontVariantNumeric)).toContain("tabular-nums");
  await expect(cell.locator(".project-money")).toHaveAttribute("title", "-0.0050 USD");
  await expect(page.locator('[data-grid-row="1"] [data-column-id="available"]')).toContainText("No access to balances");
});

test("loadPage adapts forward-only cursors and virtualizes a 10,000-row server fixture", async ({ page }) => {
  const state = await stubProjects(page, { total: 10000 });
  await page.goto("/projects"); await expect(page.getByRole("table", { name: "Projects data grid" })).toHaveAttribute("aria-rowcount", "10000");
  await page.getByRole("button", { name: "Load next 50" }).click();
  await expect(page.getByText("10,000 projects · 100 loaded")).toBeVisible();
  expect(await page.locator("tr[data-grid-row]").count()).toBeLessThan(60);
  await page.locator(".data-grid-scroller").evaluate(n => { n.scrollTop = 500; });
  expect(await page.locator("tr[data-grid-row]").count()).toBeLessThan(60);
  expect(state.requests.filter(r => r.path === "/api/v1/projects").every(r => r.query.get("page_size") === "50")).toBe(true);
});

test("lazy tabs support create/edit schedule and risks, close reasons, partial errors and retries", async ({ page }) => {
  const state = await stubProjects(page);
  await page.goto(`/projects/${projectId}?tab=invalid`);
  await expect(page.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  expect(state.requests.some(r => r.path.endsWith("/phases"))).toBe(false);
  state.fail.add(`/api/v1/projects/${projectId}/milestones`);
  await page.getByRole("tab", { name: "Schedule", exact: true }).click();
  await expect(page.getByText("No phases yet.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry schedule" })).toBeVisible();
  state.fail.clear(); await page.getByRole("button", { name: "Retry schedule" }).click();
  await page.getByRole("button", { name: "Create phase", exact: true }).click();
  await page.getByLabel(/^Phase name\s*\*?$/).fill("Engineering"); await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("button", { name: "Edit Engineering" }).click(); await page.getByLabel(/^Phase name\s*\*?$/).fill("Design"); await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("button", { name: "Edit Design" })).toBeVisible();
  await page.getByRole("button", { name: "Create milestone", exact: true }).click();
  await page.getByLabel(/^Milestone name\s*\*?$/).fill("Design freeze"); await page.getByLabel(/^Due date\s*\*?$/).fill("2026-12-01"); await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("button", { name: "Edit Design freeze" }).click(); await page.getByLabel(/^Completed on\s*\*?$/).fill("2026-11-30"); await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("tab", { name: "Risks", exact: true }).click(); await page.getByRole("button", { name: "Create risk", exact: true }).click();
  await page.getByLabel(/^Risk title\s*\*?$/).fill("Delay"); await page.getByLabel(/^Likelihood\s*\*?$/).fill("3"); await page.getByLabel(/^Impact\s*\*?$/).fill("4"); await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("button", { name: "Edit Delay" }).click(); await page.getByLabel(/^Risk status\s*\*?$/).selectOption("closed"); await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("A closure reason is required");
  expect(state.requests.filter(r => r.method === "PATCH" && r.path.includes("/risks/")).length).toBe(0);
  await page.getByLabel(/^Closure reason\s*\*?$/).fill("Delivery completed"); await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Closed · Score 12", { exact: false })).toBeVisible();
  await page.getByLabel(/^Risk status filter\s*\*?$/).selectOption("closed"); await page.getByLabel(/^Minimum score\s*\*?$/).fill("10");
  await expect.poll(() => state.requests.some(r => r.query.get("min_score") === "10" && r.query.get("status") === "closed")).toBe(true);
  await page.getByRole("tab", { name: "Budget", exact: true }).click(); await expect(page.getByText("48.60%", { exact: true })).toBeVisible();
  // E06-S08 replaces the dashboard placeholder with the real dashboard route.
  const budget = page.getByRole("region", { name: "Project budget" });
  await expect(budget.getByRole("link", { name: "Open project dashboard" })).toHaveAttribute("href", `/projects/${projectId}/dashboard`);
});

for (const theme of ["light", "dark"] as const) {
  for (const tab of ["overview", "schedule", "risks", "budget"] as const) {
    test(`detail ${tab} passes axe in ${theme}`, async ({ page }) => {
      await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
      await stubProjects(page); await page.goto(`/projects/${projectId}?tab=${tab}`);
      await expect(page.getByRole("heading", { name: "Plant renewal", exact: true })).toBeVisible();
      await expect(page.getByRole("tabpanel")).toBeVisible();
      if (tab === "schedule") await expect(page.getByText("No milestones yet.", { exact: false })).toBeVisible();
      if (tab === "risks") await expect(page.getByText("No risks match this view.", { exact: false })).toBeVisible();
      if (tab === "budget") await expect(page.getByText("48.60%", { exact: true })).toBeVisible();
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    });
  }
}

for (const theme of ["light", "dark"] as const) {
  test(`money contrast is measured independently in ${theme}`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await stubProjects(page); await page.goto("/projects");
    await page.locator(".project-money[data-negative]").waitFor();
    const ratios = await page.evaluate(() => {
      const canvas = document.createElement("canvas"); canvas.width = canvas.height = 1;
      const context = canvas.getContext("2d")!;
      const rgb = (color: string) => { context.clearRect(0, 0, 1, 1); context.fillStyle = color; context.fillRect(0, 0, 1, 1); return [...context.getImageData(0, 0, 1, 1).data].slice(0, 3); };
      const luminance = (color: string) => rgb(color).reduce((sum, channel, i) => {
        const value = channel / 255; return sum + (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][i]!;
      }, 0);
      const root = getComputedStyle(document.documentElement);
      const back = luminance(root.getPropertyValue("--surface"));
      return ["--money-outflow", "--fg-muted", "--fg-secondary"].map(token => {
        const probe = document.createElement("span"); probe.style.color = `var(${token})`; document.body.append(probe);
        const front = luminance(getComputedStyle(probe).color); probe.remove();
        return { token, ratio: (Math.max(front, back) + 0.05) / (Math.min(front, back) + 0.05) };
      });
    });
    for (const entry of ratios) expect(entry.ratio, `${theme} ${entry.token}`).toBeGreaterThanOrEqual(4.5);
  });
}

for (const width of [375, 768, 1024, 1440]) {
  test(`project screens fit ${width}px and preserve reduced-motion information`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 }); await page.emulateMedia({ reducedMotion: "reduce" });
    await stubProjects(page); await page.goto("/projects"); await page.locator("tr[data-grid-row]").first().waitFor();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (width < 1024) for (const element of await page.locator('.project-toolbar select, .project-toolbar .form-submit').all()) {
      const box = await element.boundingBox(); expect(box!.height).toBeGreaterThanOrEqual(43.99);
    }
    await page.goto(`/projects/${projectId}`); await page.getByRole("heading", { name: "Plant renewal", exact: true }).waitFor();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(page.getByRole("button", { name: "Approval pending", exact: true })).toBeVisible();
    const duration = await page.getByRole("tab", { name: "Overview", exact: true }).evaluate(n => getComputedStyle(n).transitionDuration);
    expect(Number.parseFloat(duration)).toBeLessThanOrEqual(0.3);
    await page.screenshot({ path: testInfo.outputPath(`project-${width}.png`), fullPage: true });
  });
}

test("a cancelled reason never leaks into a direct transition", async ({ page }) => {
  const state = await stubProjects(page); state.row.status = "active";
  await page.goto(`/projects/${projectId}`);
  await page.getByRole("button", { name: "Deferred", exact: true }).click();
  await page.getByLabel(/^Reason\s*\*?$/).fill("Cancelled deferral explanation");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Completed", exact: true }).click();
  await expect(page.locator('.project-record-heading [data-status="completed"]')).toBeVisible();
  const write = state.requests.filter(r => r.path.endsWith("/transitions")).at(-1);
  expect(write?.body).toEqual({ to: "completed", reason: null, override: false });
});

test("grid Enter reveals a summary sheet and Escape restores row focus", async ({ page }) => {
  await stubProjects(page); await page.goto("/projects");
  const cell = page.locator('[data-grid-row="0"] [data-column-id="number"]');
  await cell.focus(); await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Plant renewal" })).toBeVisible();
  await page.keyboard.press("Escape"); await expect(cell).toBeFocused();
  await page.keyboard.press("Enter"); await page.getByRole("button", { name: "Open project", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(projectId));
});

test("loadPage still rejects an oversized server envelope and retries without losing the view", async ({ page }) => {
  await stubProjects(page); let oversized = true;
  await page.route("**/api/v1/projects?**", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    rows: oversized ? Array.from({ length: 51 }, (_, i) => ({ ...project, id: String(i) })) : [project], total: 51, next_cursor: null,
  }) }));
  await page.goto("/projects?group_by=status");
  await expect(page.getByRole("alert")).toContainText("invalid paginated grid response");
  await expect(page.getByLabel(/^Group by\s*\*?$/)).toHaveValue("status");
  oversized = false; await page.getByRole("button", { name: /Try again|Retry/ }).click();
  await expect(page.locator("tr[data-grid-row]")).toHaveCount(1);
});

test("required create fields fail locally with a focused summary and no POST", async ({ page }) => {
  const state = await stubProjects(page); await page.goto("/projects/new");
  await page.getByRole("button", { name: "Create project", exact: true }).click();
  await expect(page.locator(".error-summary")).toBeFocused();
  await expect(page.locator(".error-summary li")).toHaveCount(5);
  expect(state.requests.filter(r => r.path === "/api/v1/projects" && r.method === "POST")).toHaveLength(0);
});

test("record required and integer guards preserve input without writing invalid risks", async ({ page }) => {
  const state = await stubProjects(page); await page.goto(`/projects/${projectId}?tab=risks`);
  await page.getByRole("button", { name: "Create risk", exact: true }).click();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".error-summary")).toBeFocused();
  await expect(page.getByLabel(/^Risk title\s*\*?$/)).toHaveAttribute("aria-invalid", "true");
  await page.getByLabel(/^Risk title\s*\*?$/).fill("Delay"); await page.getByLabel(/^Impact\s*\*?$/).fill("2");
  for (const value of ["6", "1.5"]) {
    await page.getByLabel(/^Likelihood\s*\*?$/).fill(value); await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Enter a whole number within the stated range");
    await expect(page.getByLabel(/^Likelihood\s*\*?$/)).toHaveValue(value);
  }
  expect(state.requests.filter(r => r.path.endsWith("/risks") && r.method === "POST")).toHaveLength(0);
});

test("initial overview skeleton reserves fact row dimensions without layout shift", async ({ page }) => {
  await stubProjects(page);
  await page.route(`**/api/v1/projects/${projectId}`, async route => {
    await new Promise(resolve => setTimeout(resolve, 300));
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(project) });
  });
  await page.addInitScript(() => {
    (window as unknown as { projectShifts: number[] }).projectShifts = [];
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) if (!(entry as PerformanceEntry & { hadRecentInput: boolean }).hadRecentInput) (window as unknown as { projectShifts: number[] }).projectShifts.push((entry as PerformanceEntry & { value: number }).value);
    }).observe({ type: "layout-shift", buffered: true });
  });
  await page.goto(`/projects/${projectId}`);
  const skeleton = page.locator(".project-loading .project-facts > div");
  await expect(skeleton).toHaveCount(14);
  const height = await skeleton.first().evaluate(n => n.getBoundingClientRect().height);
  await expect(page.getByRole("heading", { name: "Plant renewal", exact: true })).toBeVisible();
  expect(await page.locator('[role="tabpanel"] .project-facts > div').first().evaluate(n => n.getBoundingClientRect().height)).toBe(height);
  expect(await page.evaluate(() => (window as unknown as { projectShifts: number[] }).projectShifts.reduce((sum, value) => sum + value, 0))).toBeLessThan(0.1);
});

test("the adapter honours an initial URL cursor and history within unchanged filters", async ({ page }) => {
  const state = await stubProjects(page, { total: 10000 });
  await page.goto("/projects?cursor=50");
  await expect(page.locator('[data-grid-row="0"] [data-column-id="number"]')).toHaveText("PRJ-00050");
  const loads = state.requests.filter(r => r.path === "/api/v1/projects");
  expect(loads.every(r => r.query.get("cursor") === "50")).toBe(true);
  await page.evaluate(() => {
    window.history.pushState(null, "", "/projects?cursor=100");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page).toHaveURL(/cursor=100/);
  await expect(page.locator('[data-grid-row="0"] [data-column-id="number"]')).toHaveText("PRJ-00100");
  await page.goBack();
  await expect(page).toHaveURL(/cursor=50/);
  await expect(page.locator('[data-grid-row="0"] [data-column-id="number"]')).toHaveText("PRJ-00050");
});

test("business-unit grouping keeps equal display names in distinct id runs", async ({ page }) => {
  await stubProjects(page, { rows: [project,
    { ...project, id: secondActor, number: "PRJ-0002" },
    { ...project, id: firstActor, number: "PRJ-0003", bu_id: "55555555-5555-4555-8555-555555555555", bu_name: project.bu_name },
  ] });
  await page.goto("/projects"); await page.locator("tr[data-grid-row]").first().waitFor();
  await page.getByLabel(/^Group by\s*\*?$/).selectOption("bu");
  await expect(page.locator(".project-group-start")).toHaveCount(2);
  await expect(page.locator('[data-grid-row="1"] [data-column-id="group"]')).toBeEmpty();
  await expect(page.locator('[data-grid-row="2"] [data-column-id="group"]')).toContainText("Group: Infrastructure");
});
