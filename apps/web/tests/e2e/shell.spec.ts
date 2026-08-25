import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function openCommandMenu(page: Page) {
  await page.keyboard.press("Control+k");
  return page.getByRole("dialog", { name: "Command menu" });
}

test("skip link is first, keyboard-visible, and jumps to main", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");

  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await expect(skipLink).toBeFocused();
  expect(await skipLink.evaluate((link) => getComputedStyle(link).outlineStyle)).not.toBe("none");

  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
});

test("command menu is interactive within one frame and has no open animation", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Control+k");

  const state = await page.evaluate(
    () =>
      new Promise<{ activeLabel: string | null; animationCount: number; dialogVisible: boolean }>((resolve) => {
        requestAnimationFrame(() => {
          const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
          resolve({
            activeLabel: document.activeElement?.getAttribute("aria-label") ?? null,
            animationCount: dialog?.getAnimations().length ?? -1,
            dialogVisible: dialog !== null && dialog.getClientRects().length > 0,
          });
        });
      }),
  );

  expect(state).toEqual({ activeLabel: "Search commands", animationCount: 0, dialogVisible: true });
});

test("palette filters permissions, fuzzy-highlights, navigates with arrows, and gates destructive actions", async ({
  page,
}) => {
  await page.goto("/");
  const dialog = await openCommandMenu(page);

  await expect(dialog.getByText("Manage organization", { exact: true })).toHaveCount(0);
  await expect(dialog.getByText("Archive current view", { exact: true })).toHaveCount(0);
  await page.keyboard.down("Alt");
  await expect(dialog.getByText("Archive current view", { exact: true })).toBeVisible();
  await page.keyboard.up("Alt");

  const search = page.getByRole("combobox", { name: "Search commands" });
  await search.fill("npr");
  const record = dialog.getByRole("option", { name: "North plant renewal" });
  await expect(record.locator("mark")).toHaveCount(3);
  await search.press("ArrowDown");
  await expect(record).toHaveAttribute("aria-selected", "false");
  await search.press("ArrowUp");
  await expect(record).toHaveAttribute("aria-selected", "true");
  await search.press("Enter");

  await expect(page.getByRole("heading", { level: 1, name: "North plant renewal" })).toBeVisible();
  await expect(page.getByRole("main")).toBeFocused();
});

test("the shortcut displayed by the registry performs that same action", async ({ page }) => {
  await page.goto("/");
  const dialog = await openCommandMenu(page);
  const createProject = dialog.getByRole("option", { name: /Create project/ });
  await expect(createProject.getByText("C", { exact: true })).toBeVisible();

  await page.keyboard.press("Escape");
  await page.keyboard.press("c");

  await expect(page.getByRole("heading", { level: 1, name: "Create project" })).toBeVisible();
  await expect(page.getByTestId("route-announcer")).toHaveText("Create project");
  await expect(page.getByRole("main")).toBeFocused();
});

test("route navigation focuses main, announces the title, and keeps every breadcrumb a link", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Purchase orders", exact: true }).click();

  await expect(page).toHaveTitle("Purchase orders");
  await expect(page.getByRole("main")).toBeFocused();
  await expect(page.getByTestId("route-announcer")).toHaveText("Purchase orders");
  const breadcrumbs = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(breadcrumbs.getByRole("link")).toHaveCount(5);
  await expect(breadcrumbs.getByRole("link", { name: "Purchase orders" })).toBeVisible();

  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await expect(page).toHaveTitle("Projects");
  await expect(page.getByRole("main")).toBeFocused();
  await expect(page.getByTestId("route-announcer")).toHaveText("Projects");

  await page.goBack();
  await expect(page).toHaveTitle("Purchase orders");
  await expect(page.getByRole("heading", { level: 1, name: "Purchase orders" })).toBeVisible();
});

test("mobile drawer navigation leaves route-change focus on main", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/");

  await page.getByRole("button", { name: "Open navigation" }).click();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await drawer.getByRole("link", { name: "Purchase orders", exact: true }).click();

  await expect(drawer).toHaveCount(0);
  await expect(page.getByRole("main")).toBeFocused();
  await expect(page.getByTestId("route-announcer")).toHaveText("Purchase orders");
});

test("breadcrumb focus glow is not clipped by its focusable anchor", async ({ page }) => {
  await page.goto("/purchase-orders");
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb" });
  const project = breadcrumb.getByRole("link", { name: "North plant renewal" });
  await project.focus();

  await expect(project).toBeFocused();
  expect(
    await project.evaluate((link) => ({
      anchorOverflow: getComputedStyle(link).overflow,
      glow: getComputedStyle(link).boxShadow,
      labelOverflow: getComputedStyle(link.firstElementChild!).overflow,
    })),
  ).toEqual({
    anchorOverflow: "visible",
    glow: expect.not.stringMatching(/^none$/),
    labelOverflow: "hidden",
  });
});

test("Escape closes only the topmost layer and restores focus at each level", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/");

  const navigationTrigger = page.getByRole("button", { name: "Open navigation" });
  await navigationTrigger.click();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await expect(drawer).toBeVisible();

  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: "Command menu" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Command menu" })).toHaveCount(0);
  expect(await drawer.evaluate((element) => element.contains(document.activeElement))).toBe(true);

  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(navigationTrigger).toBeFocused();
});

for (const width of [375, 768, 1024, 1440]) {
  test(`shell is responsive without horizontal page scroll at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");

    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);

    const desktopSidebar = page.getByLabel("Primary sidebar");
    if (width < 768) {
      await expect(desktopSidebar).toBeHidden();
      await expect(page.getByRole("button", { name: "Open navigation" })).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link")).toHaveCount(3);
    } else {
      await expect(desktopSidebar).toBeVisible();
      await expect(desktopSidebar).toHaveCSS("width", width < 1024 ? "56px" : "240px");
    }
  });
}

for (const theme of ["light", "dark"] as const) {
  test(`axe passes on the shell in ${theme} theme`, async ({ page }) => {
    await page.addInitScript((selectedTheme) => {
      localStorage.setItem("xlr8flo.theme", selectedTheme);
    }, theme);
    await page.goto("/");

    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
}

test("greyscale leaves active navigation text and a non-colour indicator", async ({ page }) => {
  await page.goto("/");
  await page.locator("html").evaluate((root) => {
    root.style.filter = "grayscale(1)";
  });

  const active = page.getByRole("link", { name: "Projects", exact: true }).first();
  await expect(active).toHaveAttribute("aria-current", "page");
  expect(await active.evaluate((link) => getComputedStyle(link, "::before").width)).toBe("2px");
  await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
});

test("all shell landmarks are labelled", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("complementary", { name: "Primary sidebar" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Lifecycle modules" })).toBeVisible();
  await expect(page.getByRole("banner", { name: "Application header" })).toBeVisible();
  await expect(page.getByRole("main")).toBeVisible();

  await page.setViewportSize({ width: 375, height: 900 });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("complementary", { name: "Mobile primary sidebar" })).toBeVisible();
});
