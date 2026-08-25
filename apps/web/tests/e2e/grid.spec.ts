import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const gridPath = "/_dev/grid";

async function openGrid(page: Page, path = gridPath) {
  await page.goto(path);
  const table = page.getByRole("table", { name: "Projects data grid" });
  await expect(table).toHaveAttribute("aria-rowcount", "10000");
  await expect(page.locator("tr[data-grid-row]").first()).toBeVisible();
  return table;
}

function columnHeader(page: Page, id: string) {
  return page.locator(`th:has([aria-label="Column options for ${id}"])`);
}

test("sort, filter, and cursor pagination are URL-addressed server requests capped at 50", async ({
  page,
  request,
}) => {
  const firstRequest = page.waitForRequest((candidate) =>
    candidate.url().includes("/api/_dev/grid"),
  );
  await openGrid(page);
  const initialUrl = new URL((await firstRequest).url());
  expect(initialUrl.searchParams.get("page_size")).toBe("50");
  await expect(page.locator("tr[data-grid-row]")).toHaveCount(50);

  const sortRequest = page.waitForRequest((candidate) => {
    const url = new URL(candidate.url());
    return url.pathname === "/api/_dev/grid" && url.searchParams.get("sort") === "name";
  });
  const projectHeader = columnHeader(page, "Project");
  await projectHeader.locator(".grid-sort-button").click();
  expect(new URL((await sortRequest).url()).searchParams.get("direction")).toBe("asc");
  await expect(projectHeader).toHaveAttribute("aria-sort", "ascending");

  const filterRequest = page.waitForRequest((candidate) => {
    const url = new URL(candidate.url());
    return url.pathname === "/api/_dev/grid" && url.searchParams.get("filter") === "009";
  });
  await page.getByLabel("Filter records").fill("009");
  await page.getByRole("button", { name: "Apply filter" }).click();
  const filteredUrl = new URL((await filterRequest).url());
  expect(filteredUrl.searchParams.get("page_size")).toBe("50");
  await expect(page).toHaveURL(/filter=009/);
  expect(await page.locator("tr[data-grid-row]").count()).toBeLessThanOrEqual(50);

  const rejected = await request.get("/api/_dev/grid?page_size=51");
  expect(rejected.status()).toBe(400);
  expect(await rejected.text()).toContain("page_size must be 50");
});

test("the 10,000-row fixture virtualizes an accumulated server window within a frame budget", async ({
  page,
}) => {
  const table = await openGrid(page);
  await page.getByRole("button", { name: "Load next 50" }).click();
  await expect(page.getByText("10,000 server records · 100 loaded")).toBeVisible();

  const rowNodes = page.locator("tr[data-grid-row]");
  expect(await rowNodes.count()).toBeLessThan(60);
  await expect(table).toHaveAttribute("aria-rowcount", "10000");
  await expect(rowNodes.first()).toHaveAttribute("aria-rowindex", "1");

  const frameIntervals = await page.locator(".data-grid-scroller").evaluate(
    (scroller) =>
      new Promise<number[]>((resolve) => {
        const samples: number[] = [];
        let previous = performance.now();
        const step = () => {
          const now = performance.now();
          if (samples.length > 0) samples.push(now - previous);
          else samples.push(0);
          previous = now;
          scroller.scrollTop += scroller.clientHeight / 8;
          if (samples.length < 24) requestAnimationFrame(step);
          else resolve(samples.slice(2));
        };
        requestAnimationFrame(step);
      }),
  );
  const averageFrame = frameIntervals.reduce((total, sample) => total + sample, 0) / frameIntervals.length;
  expect(averageFrame).toBeLessThan(20);
  expect(await rowNodes.count()).toBeLessThan(60);
});

test("sortable semantics and the complete keyboard model operate against server row positions", async ({
  page,
}) => {
  await openGrid(page);
  const sortableHeaders = page.locator("th[aria-sort]");
  await expect(sortableHeaders).toHaveCount(5);
  for (let index = 0; index < 5; index += 1) {
    await expect(sortableHeaders.nth(index)).toHaveAttribute("aria-sort", "none");
  }

  const firstCell = page.locator('[data-grid-row="0"] [data-column-id="reference"]');
  await firstCell.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.locator('[data-grid-row="0"] [data-column-id="name"]')).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.locator('[data-grid-row="0"] [data-column-id="units"]')).toBeFocused();
  await page.keyboard.press("Home");
  await expect(firstCell).toBeFocused();

  await page.keyboard.press("Space");
  await expect(page.locator('[data-grid-row="0"]')).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("1 selected")).toBeVisible();

  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog", { name: "Capital project 00001" });
  await expect(sheet).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await expect(firstCell).toBeFocused();

  const lastPageRequest = page.waitForRequest((candidate) => {
    const url = new URL(candidate.url());
    return url.pathname === "/api/_dev/grid" && url.searchParams.has("cursor");
  });
  await page.keyboard.press("Control+End");
  await lastPageRequest;
  await expect(page.locator('[data-grid-row="9999"] [data-column-id="units"]')).toBeFocused();
  await expect(page.getByRole("table", { name: "Projects data grid" })).toHaveAttribute(
    "aria-rowcount",
    "10000",
  );

  await page.keyboard.press("Control+Home");
  await expect(firstCell).toBeFocused();
  await page.keyboard.press("/");
  await expect(page.getByLabel("Filter records")).toBeFocused();
});

test("focus follows virtualized arrow navigation instead of falling back to the document", async ({
  page,
}) => {
  await openGrid(page);
  await page.getByRole("button", { name: "Load next 50" }).click();
  await expect(page.getByText("10,000 server records · 100 loaded")).toBeVisible();
  await page.locator('[data-grid-row="0"] [data-column-id="reference"]').focus();

  for (let index = 0; index < 30; index += 1) await page.keyboard.press("ArrowDown");

  await expect(page.locator('[data-grid-row="30"] [data-column-id="reference"]')).toBeFocused();
  expect(await page.locator("tr[data-grid-row]").count()).toBeLessThan(60);
});

test("column resize, reorder, hide, and pin are all operable without a mouse", async ({ page }) => {
  await openGrid(page);

  const referenceMenu = columnHeader(page, "Reference").locator("summary");
  await referenceMenu.focus();
  await page.keyboard.press("Enter");
  const moveLater = page.getByRole("menuitem", { name: "Move later" }).first();
  await moveLater.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("th[scope=col]").first()).toContainText("Project");

  const ownerMenu = columnHeader(page, "Owner").locator("summary");
  await ownerMenu.focus();
  await page.keyboard.press("Enter");
  const hideOwner = columnHeader(page, "Owner").getByRole("menuitem", { name: "Hide column" });
  await hideOwner.focus();
  await page.keyboard.press("Enter");
  await expect(columnHeader(page, "Owner")).toHaveCount(0);

  const projectMenu = columnHeader(page, "Project").locator("summary");
  await projectMenu.focus();
  await page.keyboard.press("Enter");
  const pin = columnHeader(page, "Project").getByRole("menuitem", { name: "Pin left" });
  await pin.focus();
  await page.keyboard.press("Enter");
  await expect(columnHeader(page, "Project")).toHaveAttribute("data-pinned", "start");

  const resizer = columnHeader(page, "Project").getByRole("separator", {
    name: "Resize Project column",
  });
  const before = await columnHeader(page, "Project").evaluate((header) => header.getBoundingClientRect().width);
  await resizer.focus();
  await page.keyboard.press("ArrowRight");
  const after = await columnHeader(page, "Project").evaluate((header) => header.getBoundingClientRect().width);
  expect(after).toBeGreaterThan(before);
});

test("density and named views persist only in the user preference key", async ({ page }) => {
  const methods: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/_dev/grid")) methods.push(request.method());
  });
  await openGrid(page);

  const compact = page.getByRole("button", { name: "Compact" });
  await compact.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".data-grid-frame")).toHaveAttribute("data-density", "compact");
  const rowGeometry = await page.locator("tr[data-grid-row]").first().evaluate((row) => ({
    row: getComputedStyle(row).height,
    token: getComputedStyle(document.documentElement).getPropertyValue("--row-h-compact").trim(),
  }));
  expect(rowGeometry.row).toBe(rowGeometry.token);

  await page.getByLabel("Filter records").fill("plant");
  await page.getByRole("button", { name: "Apply filter" }).click();
  await expect(page).toHaveURL(/filter=plant/);
  await page.getByLabel("View name").fill("Plant work");
  await page.getByRole("button", { name: "Save view" }).press("Enter");

  const stored = await page.evaluate(() => ({
    keys: Object.keys(localStorage),
    value: localStorage.getItem("xlr8flo.preferences.fixture-user.grid.project-gallery"),
  }));
  expect(stored.keys).toContain("xlr8flo.preferences.fixture-user.grid.project-gallery");
  expect(stored.keys.some((key) => key.includes("project-00001"))).toBe(false);
  expect(JSON.parse(stored.value!)).toMatchObject({
    density: "compact",
    views: [{ density: "compact", filter: "plant", name: "Plant work" }],
  });

  await page.reload();
  await expect(page.locator(".data-grid-frame")).toHaveAttribute("data-density", "compact");
  expect(methods.every((method) => method === "GET")).toBe(true);
});

test("loading, partial, empty, and error states reserve and preserve the right information", async ({
  page,
}) => {
  await page.addInitScript(() => {
    (window as Window & { __gridCls?: number }).__gridCls = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const shift = entry as PerformanceEntry & { hadRecentInput: boolean; value: number };
        if (!shift.hadRecentInput) (window as Window & { __gridCls?: number }).__gridCls! += shift.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
  await page.route("**/api/_dev/grid**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 180));
    await route.continue();
  });
  await page.goto(gridPath);

  const skeletonSurface = page.locator(".grid-skeleton");
  await expect(skeletonSurface).toHaveClass(/\bmaterial-surface\b/);
  await expect(skeletonSurface).toHaveClass(/\bmaterial-shadow\b/);
  await expect(skeletonSurface).toHaveAttribute("data-shadow", "md");
  const skeleton = page.getByTestId("grid-skeleton-row").first();
  await expect(skeleton).toBeVisible();
  const skeletonHeight = await skeleton.evaluate((row) => row.getBoundingClientRect().height);
  await expect(page.locator("tr[data-grid-row]").first()).toBeVisible();
  const rowHeight = await page.locator("tr[data-grid-row]").first().evaluate((row) => row.getBoundingClientRect().height);
  expect(skeletonHeight).toBe(rowHeight);
  expect(await page.evaluate(() => (window as Window & { __gridCls?: number }).__gridCls ?? 0)).toBeLessThan(0.1);

  await page.getByRole("button", { name: "Load next 50" }).click();
  await expect(page.getByText("Loading more records…")).toBeVisible();
  await expect(page.getByText("10,000 server records · 100 loaded")).toBeVisible();

  await page.goto(`${gridPath}?fixture=empty`);
  const emptySurface = page.locator(".grid-empty-state");
  await expect(emptySurface).toHaveClass(/\bmaterial-surface\b/);
  await expect(emptySurface).toHaveClass(/\bmaterial-shadow\b/);
  await expect(emptySurface).toHaveAttribute("data-shadow", "md");
  await expect(page.getByText("No projects exist in this view.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add your first project — press C" })).toBeVisible();
  await expect(page.locator(".grid-empty-state svg")).toHaveCount(1);

  await page.goto(`${gridPath}?fixture=error`);
  const errorSurface = page.getByRole("alert");
  await expect(errorSurface).toHaveClass(/\bmaterial-surface\b/);
  await expect(errorSurface).toHaveClass(/\bmaterial-shadow\b/);
  await expect(errorSurface).toHaveAttribute("data-shadow", "md");
  await expect(errorSurface).toContainText("The server responded with 503");
  await expect(errorSurface).toContainText("filter, sort, columns, density, and saved views are preserved");
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
  test(`grid passes axe and retains glass, status, numeric, and selection signals in ${theme}`, async ({
    page,
  }) => {
    await page.addInitScript((selectedTheme) => {
      localStorage.setItem("xlr8flo.theme", selectedTheme);
    }, theme);
    await openGrid(page);

    const header = page.locator(".data-grid-header");
    await expect(header).toHaveClass(/glass-grid-header/);
    await expect(header).toHaveCSS("position", "sticky");
    expect(await header.evaluate((element) => getComputedStyle(element).backdropFilter)).toContain(
      "saturate(1.8)",
    );
    await expect(page.locator(".status-pill").first().locator("svg[aria-hidden=true]")).toHaveCount(1);
    const units = page.locator('[data-grid-row="0"] [data-column-id="units"]');
    await expect(units).toHaveCSS("text-align", "right");
    expect(await units.evaluate((cell) => getComputedStyle(cell).fontVariantNumeric)).toContain("tabular-nums");

    await page.locator('[data-grid-row="0"] [data-column-id="reference"]').focus();
    await page.keyboard.press("Space");
    await page.locator("html").evaluate((root) => {
      root.style.filter = "grayscale(1)";
    });
    await expect(page.locator('[data-grid-row="0"]')).toHaveAttribute("aria-selected", "true");
    await expect(page.locator('[data-grid-row="0"]')).toHaveCSS("border-left-width", "2px");

    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
}

for (const width of [375, 768, 1024, 1440]) {
  test(`grid reflows without horizontal page scroll and keeps touch controls at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await openGrid(page);
    const pageWidths = await page.evaluate(() => ({
      client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
    }));
    expect(pageWidths.scroll).toBeLessThanOrEqual(pageWidths.client);

    if (width < 768) {
      const firstCell = page.locator("tr[data-grid-row] td").first();
      expect(await firstCell.evaluate((cell) => getComputedStyle(cell, "::before").content)).toContain(
        "Reference",
      );
    }
    if (width < 1024) {
      const compact = page.getByRole("button", { name: "Compact" });
      expect(await compact.evaluate((button) => button.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
    }
  });
}
