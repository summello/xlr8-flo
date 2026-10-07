import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { ROUTE_PATHS } from "../../src/routes/route-paths";

expect(ROUTE_PATHS, "MISSING: forms-kit route in the shared route manifest").toContain(
  "/_dev/forms",
);

for (const theme of ["light", "dark"] as const) {
  for (const route of ROUTE_PATHS) {
    test(`${route} passes axe in ${theme} theme`, async ({ page }) => {
      await page.addInitScript((selectedTheme) => {
        localStorage.setItem("xlr8flo.theme", selectedTheme);
      }, theme);
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      if (route === "/_dev/grid") {
        await expect(page.locator("tr[data-grid-row]").first()).toBeVisible();
      }

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
