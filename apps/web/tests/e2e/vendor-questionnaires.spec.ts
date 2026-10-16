import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { publishedVersion, stubQuestionnaires } from "./vendor-questionnaires-fixture";

const open = async (page: Page) => {
  await page.goto("/admin/vendor-questionnaires");
  await page.getByRole("heading", { name: "Vendor Questionnaires", level: 2 }).waitFor();
};

async function addQuestion(page: Page, prompt: string, type: string, weight: string) {
  const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Question" }) });
  await form.getByLabel("Prompt").fill(prompt);
  await form.getByLabel("Type").selectOption({ label: type });
  await form.getByLabel("Weight").fill(weight);
  await form.getByRole("button", { name: "Add Question" }).click();
  await expect(page.getByRole("list", { name: "Questions" }).getByRole("listitem").filter({ has: page.locator(`input[value="${prompt}"]`) })).toBeVisible();
}

test("create, add three typed questions, reorder, publish, read-only, copy", async ({ page }) => {
  const state = await stubQuestionnaires(page);
  await open(page);
  await page.getByRole("button", { name: "New Draft" }).click();
  await expect(page.getByRole("heading", { name: "Version 1" })).toBeVisible();
  await addQuestion(page, "Insurance held?", "Yes or No", "40");
  await addQuestion(page, "Years trading", "Number", "30");
  await addQuestion(page, "Company profile", "Text", "30");
  await expect(page.getByRole("status").filter({ hasText: "Weights total 100 of 100" })).toBeVisible();
  await page.getByRole("button", { name: 'Move "Company profile" up' }).click();
  await expect(page.getByRole("list", { name: "Questions" }).getByLabel("Prompt")).toHaveCount(3);
  expect(state.versions[0]!.questions.map(q => q.prompt)).toEqual(["Insurance held?", "Company profile", "Years trading"]);
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("A published version never changes. Requests keep the version they were answered against.");
  await page.getByRole("button", { name: "Publish Version 1" }).click();
  await expect(page.getByRole("table", { name: "Questions in version 1" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add Question" })).toHaveCount(0);
  await page.getByRole("button", { name: "Copy to New Draft" }).click();
  await expect(page.getByRole("heading", { name: "Version 2" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Questions" }).getByLabel("Prompt")).toHaveCount(3);
});

test("publishing at 90 shows a focused summary with the total and keeps the draft", async ({ page }) => {
  const state = await stubQuestionnaires(page);
  await open(page);
  await page.getByRole("button", { name: "New Draft" }).click();
  await addQuestion(page, "Insurance held?", "Yes or No", "90");
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await page.getByRole("button", { name: "Publish Version 1" }).click();
  const summary = page.getByRole("alert").filter({ hasText: "Weights total 90 of 100" });
  await expect(summary).toBeFocused();
  expect(state.versions[0]!.status).toBe("draft");
  await expect(page.getByRole("button", { name: "Add Question" })).toBeVisible();
  await expect(page.getByLabel("Prompt").first()).toHaveValue("Insurance held?");
});

test("the weight total is the server's, never summed in the browser", async ({ page }) => {
  const state = await stubQuestionnaires(page);
  await open(page);
  await page.getByRole("button", { name: "New Draft" }).click();
  await addQuestion(page, "Insurance held?", "Yes or No", "90");
  state.forceTotal = 100; // the server says 100 while the field sums to 90
  await page.getByRole("button", { name: "Save Question" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Weights total 100 of 100" })).toBeVisible();
});

test("keyboard only: add, reorder with focus following, publish", async ({ page }) => {
  await stubQuestionnaires(page);
  await open(page);
  await page.getByRole("button", { name: "New Draft" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Version 1" })).toBeVisible();
  const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Question" }) });
  for (const [prompt, weight] of [["First question", "50"], ["Second question", "50"]] as const) {
    await form.getByLabel("Prompt").focus();
    await page.keyboard.type(prompt);
    await form.getByLabel("Weight").focus();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.type(weight);
    await page.keyboard.press("Enter");
    await expect(page.locator(`input[value="${prompt}"]`)).toBeVisible();
    await expect(form.getByLabel("Prompt")).toHaveValue("");
    await expect(form.getByLabel("Prompt")).toBeFocused();
  }
  const down = page.getByRole("button", { name: 'Move "First question" down' });
  await down.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("list", { name: "Questions" }).getByLabel("Prompt").first()).toHaveValue("Second question");
  await expect(down).toBeFocused();
  await expect(page.getByRole("button", { name: 'Move "First question" down' })).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Enter");
  await expect(down).toBeFocused();
  await page.getByRole("button", { name: "Publish", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("table", { name: "Questions in version 1" })).toBeVisible();
});

test("the preview fold is closed and shows no weight or points", async ({ page }) => {
  await stubQuestionnaires(page, [publishedVersion()]);
  await open(page);
  await page.getByRole("button", { name: "Open Version 1" }).click();
  const fold = page.locator("details.vq-preview");
  await expect(fold).not.toHaveAttribute("open", "");
  await fold.locator("summary").click();
  await expect(fold.getByText("Insurance held?")).toBeVisible();
  const text = await fold.innerText();
  expect(text).not.toMatch(/41|100|weight|points/i);
  expect(await fold.evaluate(el => el.innerHTML)).not.toMatch(/41|weight|points/i);
});

for (const theme of ["light", "dark"] as const) {
  test(`axe passes in the ${theme} theme, draft and published`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem("xlr8flo.theme", t), theme);
    await stubQuestionnaires(page, [publishedVersion()]);
    await open(page);
    await page.getByRole("button", { name: "Open Version 1" }).click();
    await page.getByRole("button", { name: "Copy to New Draft" }).click();
    await page.getByRole("heading", { name: "Version 2" }).waitFor();
    await page.locator("details.vq-preview summary").click();
    const result = await new AxeBuilder({ page }).analyze();
    expect(result.violations.map(v => `${v.id}: ${v.nodes.map(n => JSON.stringify(n.target)).join(" ")}`)).toEqual([]);
  });
}

test("fits 375 without sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await stubQuestionnaires(page, [publishedVersion()]);
  await open(page);
  await page.getByRole("button", { name: "Open Version 1" }).click();
  await page.getByRole("button", { name: "Copy to New Draft" }).click();
  await page.getByRole("heading", { name: "Version 2" }).waitFor();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
