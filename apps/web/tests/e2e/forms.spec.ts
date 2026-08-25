import { expect, test, type Page } from "@playwright/test";

async function completeForm(page: Page, email: string) {
  await page.getByLabel("Email").focus();
  await page.keyboard.type(email);
  await page.keyboard.press("Tab");
  await page.keyboard.type("correct horse battery staple");
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Approval mode")).toBeFocused();
  await page.keyboard.press("s");
  await expect(page.getByLabel("Approval mode")).toHaveValue("single");
  await page.keyboard.press("Tab");
  await page.keyboard.type("Avery Chen");
  await page.keyboard.press("Tab");
  await page.keyboard.type("125000.0000");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Submit fixture" })).toBeFocused();
  await page.keyboard.press("Enter");
}

test("every primitive keeps its visible label, persistent help, and blur validation", async ({
  page,
}) => {
  await page.goto("/_dev/forms");

  for (const label of ["Email", "Password", "Approval mode", "Reviewer", "Budget"]) {
    const field = page.getByLabel(label);
    await expect(field).toBeVisible();
    await expect(field).toHaveAttribute("aria-required", "true");
    const describedBy = await field.getAttribute("aria-describedby");
    expect(describedBy, `MISSING: helper relationship for ${label}`).toBeTruthy();
    const helperId = describedBy!.split(" ")[0]!;
    await expect(page.locator(`#${helperId}`)).toBeVisible();

    await field.focus();
    await page.keyboard.press("Tab");
    await expect(field).toHaveAttribute("aria-invalid", "true");
    await expect(page.locator(`#${helperId}`)).toBeVisible();
  }
});

test("invalid submit focuses the first field and the summary anchors return focus", async ({ page }) => {
  await page.goto("/_dev/forms");
  await page.getByRole("button", { name: "Submit fixture" }).focus();
  await page.keyboard.press("Enter");

  await expect(page.getByLabel("Email")).toBeFocused();
  const summary = page.getByRole("alert").filter({ hasText: "Review the fields below" });
  await expect(summary.getByRole("link")).toHaveCount(5);
  const budgetLink = summary.getByRole("link", { name: /Budget:/ });
  await budgetLink.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Budget")).toBeFocused();
});

test("a generated 422 maps to its field while preserving every entered value", async ({ page }) => {
  await page.goto("/_dev/forms");
  await completeForm(page, "locked@example.com");

  await expect(page.getByLabel("Email")).toBeFocused();
  await expect(page.getByLabel("Email")).toHaveValue("locked@example.com");
  await expect(page.getByLabel("Password")).toHaveValue("correct horse battery staple");
  await expect(page.getByLabel("Approval mode")).toHaveValue("single");
  await expect(page.getByLabel("Reviewer")).toHaveValue("Avery Chen");
  await expect(page.getByLabel("Budget")).toHaveValue("125000.0000");
  await expect(page.locator("#form-email-error")).toContainText(
    "This account is locked. Reset its password, then submit again.",
  );
  await expect(
    page.getByRole("alert").getByRole("link", { name: /Email: This account is locked/ }),
  ).toHaveAttribute("href", "#form-email");
});

test("keyboard-only submission completes without emitting a mouse or pointer event", async ({ page }) => {
  await page.addInitScript(() => {
    const events: string[] = [];
    for (const eventName of ["click", "mousedown", "mouseup", "pointerdown", "pointerup"]) {
      window.addEventListener(
        eventName,
        (event) => {
          if (eventName === "click" && event instanceof MouseEvent && event.detail === 0) return;
          events.push(eventName);
        },
        true,
      );
    }
    (window as Window & { __formMouseEvents?: string[] }).__formMouseEvents = events;
  });
  await page.goto("/_dev/forms");
  await completeForm(page, "active@example.com");

  await expect(page.getByRole("status")).toContainText("Form submitted");
  expect(
    await page.evaluate(
      () => (window as Window & { __formMouseEvents?: string[] }).__formMouseEvents ?? [],
    ),
  ).toEqual([]);
});
