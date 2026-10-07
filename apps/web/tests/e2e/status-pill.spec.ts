import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { STATUS_LABELS } from "../../src/components/status/labels";
import { statusSelections, statusTone } from "../../src/components/status/map";

const galleryPath = "/_dev/status-gallery";

test("gallery renders every closed document status with derived tone, icon, and text", async ({ page }) => {
  await page.goto(galleryPath);

  await expect(page).toHaveTitle("Status gallery");
  const selections = statusSelections();
  await expect(page.locator(".status-pill")).toHaveCount(selections.length);

  for (const selection of selections) {
    const pill = page.locator(
      `.status-pill[data-doc-type="${selection.docType}"][data-status="${selection.status}"]`,
    );
    await expect(pill).toHaveCount(1);
    await expect(pill).toHaveAttribute("data-tone", statusTone(selection.docType, selection.status)!);
    await expect(pill).toHaveText(STATUS_LABELS[selection.status]);
    await expect(pill.locator("svg")).toHaveCount(1);
    await expect(pill.locator("svg")).toHaveAttribute("aria-hidden", "true");
  }
});

test("greyscale keeps every status identifiable by its icon and text", async ({ page }) => {
  await page.goto(galleryPath);
  await page.locator("html").evaluate((root) => {
    root.style.filter = "saturate(0)";
  });

  for (const selection of statusSelections()) {
    const pill = page.locator(
      `.status-pill[data-doc-type="${selection.docType}"][data-status="${selection.status}"]`,
    );
    await expect(pill).toHaveText(STATUS_LABELS[selection.status]);
    await expect(pill.locator("svg[aria-hidden='true']")).toHaveCount(1);
  }
});

test("pill geometry resolves from the square status tokens rather than tag geometry", async ({ page }) => {
  await page.goto(galleryPath);
  const geometry = await page.locator(".status-pill").first().evaluate((pill) => {
    const root = getComputedStyle(document.documentElement);
    const style = getComputedStyle(pill);
    const iconStyle = getComputedStyle(pill.querySelector("svg")!);
    return {
      actual: {
        gap: style.gap,
        height: style.height,
        icon: iconStyle.width,
        padding: style.paddingInline,
        radius: style.borderRadius,
      },
      pillTokens: {
        gap: root.getPropertyValue("--pill-gap").trim(),
        height: root.getPropertyValue("--pill-h").trim(),
        icon: root.getPropertyValue("--pill-icon").trim(),
        padding: root.getPropertyValue("--pill-pad-x").trim(),
        radius: root.getPropertyValue("--pill-radius").trim(),
      },
      tagRadius: root.getPropertyValue("--tag-radius").trim(),
    };
  });

  expect(geometry.actual).toEqual(geometry.pillTokens);
  expect(geometry.actual.radius).not.toBe(geometry.tagRadius);
});

test("keyboard-only entry reaches and visibly focuses the gallery main content", async ({ page }) => {
  await page.goto(galleryPath);
  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await expect(skipLink).toBeFocused();
  expect(await skipLink.evaluate((link) => getComputedStyle(link).outlineStyle)).not.toBe("none");

  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
  await expect(page.getByRole("heading", { level: 1, name: "Status gallery" })).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
  test(`status gallery passes axe and resolves its tones in ${theme} theme`, async ({ page }) => {
    await page.addInitScript((selectedTheme) => {
      localStorage.setItem("xlr8flo.theme", selectedTheme);
    }, theme);
    await page.goto(galleryPath);

    const pill = page.locator('.status-pill[data-tone="success"]').first();
    const colours = await pill.evaluate((element) => {
      const style = getComputedStyle(element);
      const resolveColour = (value: string) => {
        const probe = document.createElement("span");
        probe.style.color = value;
        document.body.append(probe);
        const resolved = getComputedStyle(probe).color;
        probe.remove();
        return resolved;
      };
      return {
        actualBackground: style.backgroundColor,
        actualForeground: style.color,
        tokenBackground: resolveColour("var(--status-success-tint)"),
        tokenForeground: resolveColour("var(--status-success)"),
      };
    });
    expect(colours.actualBackground).toBe(colours.tokenBackground);
    expect(colours.actualForeground).toBe(colours.tokenForeground);

    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
}

for (const width of [375, 768, 1024, 1440]) {
  test(`status gallery has no horizontal page scroll at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(galleryPath);

    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  });
}
