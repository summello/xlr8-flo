import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { stubWorkflows, workflowId } from "./workflow-list-fixture";
test("keyboard list history draft route preview, errors preserve estimate and path is numbered", async ({ page }) => {
    await stubWorkflows(page);
    await page.goto("/admin/approval-workflows");
    const edited = page.locator("tbody time").first();
    await expect(edited).toBeVisible();
    expect(await edited.textContent()).not.toBe(await edited.getAttribute("dateTime"));
    await page.getByRole("button", { name: "Versions for Capital Review" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Close Version History" })).toBeFocused();
    await expect(page.getByRole("heading", { name: "Draft 2 Compared With Published 1" })).toBeVisible();
    const lineageDate = page.getByRole("dialog").locator("ol time").first();
    await expect(lineageDate).toBeVisible();
    expect(await lineageDate.textContent()).not.toBe(await lineageDate.getAttribute("dateTime"));
    await expect(page.getByRole("table", { name: "Workflow changes" })).toContainText("Removed step");
    await expect(page.getByRole("table", { name: "Workflow changes" })).toContainText("Finance · Approvers");
    await expect(page.getByRole("table", { name: "Workflow changes" })).toContainText("Added step");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Versions for Capital Review" })).toBeFocused();
    await page.getByRole("button", { name: "Capital Review", exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Route Preview", exact: true }).waitFor();
    await page.getByRole("button", { name: "Route Preview", exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.getByLabel(/^Estimate/).focus();
    await page.keyboard.type("bad");
    await page.getByRole("button", { name: "Run Preview" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("alert").first()).toBeFocused();
    await expect(page.getByLabel(/^Estimate/)).toHaveValue("bad");
    await expect(page.getByLabel(/^Estimate/)).toHaveAttribute("aria-invalid", "true");
    await page.getByLabel(/^Estimate/).focus();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.type("196000");
    await page.getByRole("button", { name: "Run Preview" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Estimate is over 50,000.00 USD").first()).toBeVisible();
    await expect(page.getByRole("dialog").locator("ol strong")).toHaveText(["1. Finance", "2. Added step"]);
    await expect(page.getByText("Separation of duty is applied when a request is submitted.")).toBeVisible();
    await expect(page.getByText("excluded@example.test · Excluded: not eligible in scope")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Route Preview", exact: true })).toBeFocused();
    await expect(page.locator('[data-node="review"] strong')).toHaveText("1. Finance");
    await expect(page.locator(".wf-lines g").nth(1)).toHaveAttribute("stroke-width", "var(--space-1)");
    await expect(page.locator(".wf-lines g").nth(2)).toHaveAttribute("stroke-width", "1");
    const widths = await page.locator(".wf-lines g > path").evaluateAll(paths => paths.map(path => parseFloat(getComputedStyle(path).strokeWidth)));
    expect(widths[1]).toBeGreaterThan(widths[2]!);
    await page.locator('[data-node="review"]').focus(); await page.keyboard.press("ArrowRight");
    await expect(page.locator('[data-node="review"] strong')).toHaveText("Finance");
});
for (const theme of ["light", "dark"])
    test(`list history preview axe and token contrast in ${theme}`, async ({ page }, info) => {
        await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
        await stubWorkflows(page);
        await page.goto("/admin/approval-workflows");
        await page.getByRole("button", { name: "Capital Review", exact: true }).waitFor();
        const ratios = await page.evaluate(() => {
            const context = document.createElement("canvas").getContext("2d")!, root = getComputedStyle(document.documentElement);
            const lum = (token: string) => { context.fillStyle = root.getPropertyValue(token).trim(); context.fillRect(0, 0, 1, 1); return [...context.getImageData(0, 0, 1, 1).data].slice(0, 3).reduce((sum, c, i) => { const v = c / 255; return sum + (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][i]!; }, 0); };
            return [["--fg", "--surface", 4.5], ["--fg-secondary", "--surface", 4.5], ["--status-neutral", "--status-neutral-tint", 4.5], ["--status-info", "--status-info-tint", 4.5], ["--ring", "--surface", 3], ["--border-control", "--surface", 3]].map(([a, b, min]) => { const x = lum(a as string), y = lum(b as string); return { token: a, ratio: (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05), min: Number(min) }; });
        });
        for (const r of ratios)
            expect(r.ratio, `${theme} ${r.token}`).toBeGreaterThanOrEqual(r.min);
        expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
        await page.getByRole("button", { name: "Versions for Capital Review" }).click();
        await page.getByRole("table", { name: "Workflow changes" }).waitFor();
        expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
        await page.screenshot({ path: info.outputPath(`history-${theme}.png`), fullPage: true });
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: "Capital Review", exact: true }).click();
        await page.getByRole("button", { name: "Route Preview", exact: true }).click();
        await page.getByLabel(/^Estimate/).fill("196000");
        await page.getByRole("button", { name: "Run Preview" }).click();
        await page.getByText("Estimate is over 50,000.00 USD").first().waitFor();
        expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
        await page.screenshot({ path: info.outputPath(`preview-${theme}.png`), fullPage: true });
    });
test("server search pages empty partial and recoverable errors", async ({ page }) => {
    const state = await stubWorkflows(page);
    state.mode = "partial";
    await page.goto("/admin/approval-workflows");
    await expect(page.getByText("In-flight counts delayed")).toBeVisible();
    await expect(page.getByText("Not available")).toBeVisible();
    await page.getByLabel("Search workflows").fill("missing");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.getByRole("heading", { name: "No Matching Workflows" })).toBeVisible();
    expect(state.queries.at(-1)?.searchParams.get("search")).toBe("missing");
    state.mode = "error";
    await page.getByRole("button", { name: "Clear search" }).click();
    await expect(page.getByRole("alert")).toContainText("Your search is preserved");
    state.mode = "empty";
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByRole("heading", { name: "No Workflows Yet" })).toBeVisible();
    state.mode = "paged";
    await page.getByRole("button", { name: "Search", exact: true }).click(); // same filter: reload to request state
    await page.reload();
    await page.getByRole("button", { name: "Next page" }).click();
    await expect.poll(() => state.queries.at(-1)?.searchParams.get("cursor")).toBe("next");
    await page.getByRole("button", { name: "Previous page" }).click();
    await expect(page.getByRole("button", { name: "Next page" })).toBeVisible();
});
test("new workflow creates a definition and first draft then opens builder", async ({ page }) => { const state=await stubWorkflows(page); await page.goto("/admin/approval-workflows"); await page.getByRole("button", { name: "New Workflow", exact: true }).click(); await page.getByLabel(/^Name/).fill("Capital Review"); await page.getByRole("button", { name: "Create Workflow" }).click(); await expect(page).toHaveURL(new RegExp(workflowId)); expect(state.writes.find(w=>w.path==="/api/v1/workflows")?.headers["idempotency-key"]).toBeTruthy(); expect(state.writes.find(w=>w.path.endsWith("/versions"))?.body.graph).toBeTruthy(); await expect(page.getByRole("button", { name: "Route Preview", exact: true })).toBeVisible(); });
for (const width of [375, 768, 1024, 1440])
    test(`workflow list and sheets fit ${width} with reduced motion`, async ({ page }) => { await page.setViewportSize({ width, height: 900 }); await page.emulateMedia({ reducedMotion: "reduce" }); await stubWorkflows(page); await page.goto("/admin/approval-workflows"); await page.getByRole("button", { name: "Versions for Capital Review" }).click(); await page.getByRole("table", { name: "Workflow changes" }).waitFor(); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); if (width < 1024)
        expect((await page.getByRole("button", { name: "Close Version History" }).boundingBox())!.height).toBeGreaterThanOrEqual(43.99); });

test("history and scope failures recover independently and loading state reserves the list",async({page})=>{
 const state=await stubWorkflows(page);state.mode="loading";await page.goto("/admin/approval-workflows");await expect(page.getByRole("status").filter({hasText:"Loading workflows"})).toBeVisible();await page.getByRole("button",{name:"Versions for Capital Review"}).waitFor();state.failHistory=true;await page.getByRole("button",{name:"Versions for Capital Review"}).click();await expect(page.getByRole("alert")).toContainText("Workflow versions are preserved");state.failHistory=false;await page.getByRole("button",{name:"Try again",exact:true}).click();await expect(page.getByRole("table",{name:"Workflow changes"})).toBeVisible();await page.keyboard.press("Escape");await page.getByRole("button",{name:"Capital Review",exact:true}).click();state.failUnits=true;await page.getByRole("button",{name:"Route Preview",exact:true}).click();await expect(page.getByRole("alert")).toContainText("Your bound scope is kept");await page.getByLabel(/^Estimate/).fill("196000");await page.getByRole("button",{name:"Run Preview"}).click();await expect(page.getByText("Estimate is over 50,000.00 USD").first()).toBeVisible();state.failUnits=false;await page.getByRole("button",{name:"Retry units"}).click();await expect(page.getByLabel("BU/OU",{exact:true}).locator("option")).toHaveCount(2);
});


test("new workflow field failure keeps entries and focuses an error summary",async({page})=>{await stubWorkflows(page);await page.goto("/admin/approval-workflows");await page.getByRole("button",{name:"New Workflow",exact:true}).click();await page.getByRole("button",{name:"Create Workflow"}).click();await expect(page.getByRole("alert")).toBeFocused();await expect(page.getByLabel(/^Name/)).toHaveValue("");await expect(page.getByLabel(/^Name/)).toHaveAttribute("aria-invalid","true");await expect(page.getByRole("dialog")).toBeVisible();});

test("empty and blocked preview routes preserve the sample and explain recovery",async({page})=>{const state=await stubWorkflows(page);await page.goto(`/admin/approval-workflows/${workflowId}`);await page.getByRole("button",{name:"Route Preview",exact:true}).click();await page.getByLabel(/^Estimate/).fill("100");await page.getByRole("button",{name:"Run Preview"}).click();await expect(page.getByText("No approval steps on this route.")).toBeVisible();state.blockedPreview=true;await page.getByRole("button",{name:"Run Preview"}).click();await expect(page.getByText("Preview could not complete.",{exact:false})).toBeVisible();await expect(page.getByRole("alert")).toContainText("No eligible approver");await expect(page.getByLabel(/^Estimate/)).toHaveValue("100");});

test("published workflow has no check or publish action", async ({ page }) => {
    const state = await stubWorkflows(page);
    state.publishedOnly = true;
    await page.goto(`/admin/approval-workflows/${workflowId}`);
    await page.getByRole("button", { name: "Route Preview", exact: true }).waitFor();
    await expect(page.getByRole("button", { name: "Check", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Publish", exact: true })).toHaveCount(0);
});
