import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const storageKey = "xlr8flo.theme";

test("system and explicit theme states resolve in both directions", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");

  await expect(page.locator("html")).not.toHaveAttribute("data-theme");
  expect(await page.locator("html").evaluate((root) => getComputedStyle(root).colorScheme)).toBe(
    "dark",
  );

  await page.evaluate((key) => localStorage.setItem(key, "light"), storageKey);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await page.locator("html").evaluate((root) => getComputedStyle(root).colorScheme)).toBe(
    "light",
  );

  await page.emulateMedia({ colorScheme: "light" });
  await page.evaluate((key) => localStorage.setItem(key, "dark"), storageKey);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await page.locator("html").evaluate((root) => getComputedStyle(root).colorScheme)).toBe(
    "dark",
  );

  await page.evaluate((key) => localStorage.removeItem(key), storageKey);
  await page.reload();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme");
  expect(await page.locator("html").evaluate((root) => getComputedStyle(root).colorScheme)).toBe(
    "light",
  );
});

test("the token baseline is accessible and paints its own background", async ({ page }) => {
  await page.goto("/");

  const paint = await page.locator("body").evaluate((body) => ({
    background: getComputedStyle(body).backgroundColor,
    canvas: getComputedStyle(document.documentElement).getPropertyValue("--canvas").trim(),
  }));
  expect(paint.background).toBe(paint.canvas);
  expect(await page.locator("a, button, input, select, textarea, [tabindex]").count()).toBe(0);

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

for (const width of [375, 768, 1024, 1440]) {
  test(`does not create horizontal page scroll at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ height: 900, width });
    await page.goto("/");

    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  });
}
