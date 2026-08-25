import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readdirSync, readFileSync } from "node:fs";
import { extname, join, resolve } from "node:path";

const webRoot = resolve(import.meta.dirname, "../..");
const sourceRoot = join(webRoot, "src");
const materialPath = join(sourceRoot, "styles/material.css");
const tokenPath = join(sourceRoot, "styles/tokens.css");
const materialSource = readFileSync(materialPath, "utf8");
const tokenSource = readFileSync(tokenPath, "utf8");
const expectedGlassRoles = [
  "glass-command-palette",
  "glass-grid-header",
  "glass-sheet-backdrop",
] as const;
const expectedShadows = ["sm", "md", "lg", "xl", "drag"] as const;

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? sourceFiles(path) : [path];
  });
}

function blockAfter(source: string, marker: string): string {
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(`Missing ${marker}`);
  const open = source.indexOf("{", start + marker.length);
  if (open < 0) throw new Error(`Missing block for ${marker}`);
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) return source.slice(open + 1, index);
  }
  throw new Error(`Unclosed block for ${marker}`);
}

async function selectTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((selectedTheme) => {
    localStorage.setItem("xlr8flo.theme", selectedTheme);
  }, theme);
}

test("material source admits only the five documented token-backed shadow depths", () => {
  const mappedShadows = [...materialSource.matchAll(/\.material-shadow\[data-shadow="([a-z]+)"\]/g)]
    .map((match) => match[1])
    .sort();
  expect(mappedShadows).toEqual([...expectedShadows].sort());

  for (const shadow of expectedShadows) {
    expect(
      materialSource,
      `MISSING: --shadow-${shadow} utility mapping`,
    ).toMatch(new RegExp(`data-shadow="${shadow}"[^}]+var\\(--shadow-${shadow}\\)`));
  }
  expect(materialSource).toMatch(
    /box-shadow:\s*var\(--material-shadow\),\s*var\(--lit-edge\)\s*;/,
  );

  const lightTokens = blockAfter(tokenSource, ":root");
  expect(lightTokens).toMatch(/--shadow-color:\s*(?!0\s+0%\s+0%)[^;]+;/);
  for (const shadow of expectedShadows) {
    const declaration = lightTokens.match(new RegExp(`--shadow-${shadow}:([^;]+);`))?.[1];
    expect(declaration, `MISSING: light --shadow-${shadow}`).toBeDefined();
    expect(declaration).toContain("hsl(var(--shadow-color)");
    expect(declaration).toContain(",");
  }
});

test("glass is restricted to the three honest roles with both opaque fallbacks", () => {
  const glassRoles = new Set(
    sourceFiles(sourceRoot).flatMap((path) => {
      const source = readFileSync(path, "utf8");
      return [...source.matchAll(/\bglass-[a-z-]+\b/g)].map((match) => match[0]);
    }),
  );
  expect([...glassRoles].sort()).toEqual([...expectedGlassRoles].sort());

  for (const role of expectedGlassRoles) {
    expect(materialSource, `MISSING: .${role}`).toContain(`.${role}`);
  }
  expect(materialSource).toMatch(/backdrop-filter:\s*blur\(20px\)\s+saturate\(180%\)\s*;/);

  const unsupported = blockAfter(materialSource, "@supports not (backdrop-filter: blur(1px))");
  const reduced = blockAfter(materialSource, "@media (prefers-reduced-transparency: reduce)");
  for (const role of expectedGlassRoles) {
    expect(unsupported, `MISSING: unsupported fallback for .${role}`).toContain(`.${role}`);
    expect(reduced, `MISSING: reduced-transparency fallback for .${role}`).toContain(`.${role}`);
  }
  expect(unsupported).toMatch(/background:\s*var\(--surface\)\s*;/);
  expect(reduced).toMatch(/background:\s*var\(--surface\)\s*;/);
  expect(reduced).toMatch(/backdrop-filter:\s*none\s*;/);
});

test("grain, focus, and component shadow guards are explicit", () => {
  const grain = blockAfter(materialSource, "body::before");
  const opacity = Number(grain.match(/opacity:\s*([\d.]+)\s*;/)?.[1]);
  expect(grain).toMatch(/position:\s*fixed\s*;/);
  expect(grain).toMatch(/pointer-events:\s*none\s*;/);
  expect(grain).toContain("data:image/svg+xml");
  expect(grain).not.toMatch(/url\(["']?https?:/);
  expect(opacity).toBeGreaterThan(0);
  expect(opacity).toBeLessThanOrEqual(0.03);

  const reduced = blockAfter(materialSource, "@media (prefers-reduced-transparency: reduce)");
  expect(blockAfter(reduced, "body::before")).toMatch(/display:\s*none\s*;/);

  const focus = blockAfter(materialSource, ":focus-visible");
  expect(focus).toMatch(/outline:\s*2px\s+solid\s+var\(--ring\)\s*;/);
  expect(focus).toMatch(/outline-offset:\s*2px\s*;/);
  expect(focus).toMatch(/var\(--ring\)\s+18%,\s*transparent/);

  const componentSources = sourceFiles(join(sourceRoot, "components"))
    .filter((path) => [".ts", ".tsx"].includes(extname(path)))
    .map((path) => ({ path, source: readFileSync(path, "utf8") }));
  for (const component of componentSources) {
    expect(component.source, `${component.path} hardcodes a shadow`).not.toMatch(
      /box-shadow|boxShadow/,
    );
  }

  const nonTokenSources = sourceFiles(sourceRoot).filter((path) => path !== tokenPath);
  for (const path of nonTokenSources) {
    expect(readFileSync(path, "utf8"), `${path} restates hsl() outside tokens.css`).not.toMatch(
      /\bhsl\(/i,
    );
  }
});

test("grain stacks below sticky chrome", async ({ page }) => {
  await page.goto("/");

  const stacking = await page.evaluate(() => ({
    grain: Number(getComputedStyle(document.body, "::before").zIndex),
    sticky: Number(getComputedStyle(document.documentElement).getPropertyValue("--z-sticky")),
  }));

  expect(stacking.grain).toBeLessThan(stacking.sticky);
});

for (const theme of ["light", "dark"] as const) {
  test(`surface shadows resolve from the ${theme} scale and always include its lit edge`, async ({
    page,
  }) => {
    await selectTheme(page, theme);
    await page.goto("/");

    const defaultSurface = page.locator(".empty-state");
    await expect(defaultSurface).toHaveClass(/\bmaterial-surface\b/);
    await expect(defaultSurface).toHaveClass(/\bmaterial-shadow\b/);
    await expect(defaultSurface).toHaveAttribute("data-shadow", "md");

    const shadows = await page.evaluate((depths) => {
      const results: Record<string, { actual: string; expected: string }> = {};
      for (const depth of depths) {
        const surface = document.createElement("div");
        surface.className = "material-shadow";
        surface.dataset.shadow = depth;
        document.body.append(surface);

        const expected = document.createElement("div");
        expected.style.boxShadow = `var(--shadow-${depth}), var(--lit-edge)`;
        document.body.append(expected);

        results[depth] = {
          actual: getComputedStyle(surface).boxShadow,
          expected: getComputedStyle(expected).boxShadow,
        };
        surface.remove();
        expected.remove();
      }
      return results;
    }, expectedShadows);

    for (const shadow of expectedShadows) {
      expect(shadows[shadow], `MISSING: rendered ${theme} ${shadow} shadow`).toBeDefined();
      expect(shadows[shadow]!.actual).toBe(shadows[shadow]!.expected);
      expect(shadows[shadow]!.actual).not.toBe("none");
    }
  });

  test(`glass text clears 4.5:1 on the lightest and darkest ${theme} content`, async ({ page }) => {
    await selectTheme(page, theme);
    await page.goto("/");

    const ratios = await page.evaluate(() => {
      type Pixel = [number, number, number, number];
      const canvas = document.createElement("canvas");
      canvas.width = 1;
      canvas.height = 1;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (context === null) throw new Error("Canvas context unavailable");

      const pixel = (colour: string): Pixel => {
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = colour;
        context.fillRect(0, 0, 1, 1);
        return [...context.getImageData(0, 0, 1, 1).data].map((value) => value / 255) as Pixel;
      };
      const resolveColour = (value: string) => {
        const probe = document.createElement("span");
        probe.style.color = value;
        document.body.append(probe);
        const resolved = getComputedStyle(probe).color;
        probe.remove();
        return pixel(resolved);
      };
      const linear = (channel: number) =>
        channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      const luminance = ([red, green, blue]: Pixel) =>
        0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue);
      const contrast = (first: Pixel, second: Pixel) => {
        const firstLuminance = luminance(first);
        const secondLuminance = luminance(second);
        return (
          (Math.max(firstLuminance, secondLuminance) + 0.05) /
          (Math.min(firstLuminance, secondLuminance) + 0.05)
        );
      };

      const glass = document.createElement("div");
      glass.className = "glass-command-palette";
      document.body.append(glass);
      const glassPixel = pixel(getComputedStyle(glass).backgroundColor);
      glass.remove();

      const backdrops = ["--canvas", "--sunken", "--surface", "--raised"].map((token) =>
        resolveColour(`var(${token})`),
      );
      const extrema = backdrops.reduce(
        (current, candidate) => ({
          darkest: luminance(candidate) < luminance(current.darkest) ? candidate : current.darkest,
          lightest: luminance(candidate) > luminance(current.lightest) ? candidate : current.lightest,
        }),
        { darkest: backdrops[0]!, lightest: backdrops[0]! },
      );
      const composite = (backdrop: Pixel): Pixel => [
        glassPixel[0] * glassPixel[3] + backdrop[0] * (1 - glassPixel[3]),
        glassPixel[1] * glassPixel[3] + backdrop[1] * (1 - glassPixel[3]),
        glassPixel[2] * glassPixel[3] + backdrop[2] * (1 - glassPixel[3]),
        1,
      ];

      return ["--fg", "--fg-secondary", "--fg-muted"].flatMap((foreground) => {
        const foregroundPixel = resolveColour(`var(${foreground})`);
        return (Object.keys(extrema) as Array<keyof typeof extrema>).map((backdrop) => ({
          backdrop,
          foreground,
          glass: contrast(foregroundPixel, composite(extrema[backdrop])),
          opaqueFallback: contrast(foregroundPixel, resolveColour("var(--surface)")),
        }));
      });
    });

    for (const ratio of ratios) {
      expect(
        ratio.glass,
        `${theme}: ${ratio.foreground} on glass over ${ratio.backdrop} is ${ratio.glass.toFixed(2)}:1`,
      ).toBeGreaterThanOrEqual(4.5);
      expect(
        ratio.opaqueFallback,
        `${theme}: ${ratio.foreground} on opaque fallback is ${ratio.opaqueFallback.toFixed(2)}:1`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });
}

test("reduced transparency removes grain and makes every glass role opaque", async ({ page }) => {
  const client = await page.context().newCDPSession(page);
  await client.send("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-reduced-transparency", value: "reduce" }],
  });
  await page.goto("/");

  const result = await page.evaluate((roles) => {
    const resolveBackground = (value: string) => {
      const probe = document.createElement("div");
      probe.style.background = value;
      document.body.append(probe);
      const background = getComputedStyle(probe).backgroundColor;
      probe.remove();
      return background;
    };
    const glass = Object.fromEntries(
      roles.map((role) => {
        const probe = document.createElement("div");
        probe.className = role;
        document.body.append(probe);
        const style = getComputedStyle(probe);
        const values = {
          backdropFilter: style.backdropFilter,
          background: style.backgroundColor,
        };
        probe.remove();
        return [role, values];
      }),
    );
    return {
      glass,
      grainDisplay: getComputedStyle(document.body, "::before").display,
      surface: resolveBackground("var(--surface)"),
    };
  }, expectedGlassRoles);

  expect(result.grainDisplay).toBe("none");
  for (const role of expectedGlassRoles) {
    expect(result.glass[role], `MISSING: reduced-transparency ${role}`).toEqual({
      backdropFilter: "none",
      background: result.surface,
    });
  }
});

for (const theme of ["light", "dark"] as const) {
  test(`keyboard focus remains visible and unclipped in ${theme} material`, async ({ page }) => {
    await selectTheme(page, theme);
    await page.goto("/");

    const assertKeyboardFocus = async (label: string) => {
      const focus = await page.evaluate(() => {
        const element = document.activeElement;
        if (!(element instanceof HTMLElement)) throw new Error("No focused HTML element");
        const style = getComputedStyle(element);
        const focusRect = element.getBoundingClientRect();
        const focusExtent = 8;
        const clippingAncestor = (() => {
          let ancestor = element.parentElement;
          while (ancestor !== null) {
            const ancestorStyle = getComputedStyle(ancestor);
            if (ancestorStyle.overflow === "hidden") {
              const ancestorRect = ancestor.getBoundingClientRect();
              const clipsFocus =
                focusRect.top - focusExtent < ancestorRect.top ||
                focusRect.right + focusExtent > ancestorRect.right ||
                focusRect.bottom + focusExtent > ancestorRect.bottom ||
                focusRect.left - focusExtent < ancestorRect.left;
              if (clipsFocus) return ancestor.className || ancestor.tagName;
            }
            ancestor = ancestor.parentElement;
          }
          return null;
        })();
        return {
          clippingAncestor,
          glow: style.boxShadow,
          outlineStyle: style.outlineStyle,
          outlineWidth: style.outlineWidth,
        };
      });
      expect(focus.outlineStyle, `${theme}: ${label} has no solid ring`).toBe("solid");
      expect(focus.outlineWidth, `${theme}: ${label} ring width`).toBe("2px");
      expect(focus.glow, `${theme}: ${label} has no glow`).not.toBe("none");
      expect(focus.clippingAncestor, `${theme}: ${label} is clipped`).toBeNull();
    };

    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
    await assertKeyboardFocus("skip link");

    const commandTrigger = page.locator(".command-trigger");
    for (
      let tab = 0;
      tab < 30 &&
      !(await commandTrigger.evaluate((element) => element === document.activeElement));
      tab += 1
    ) {
      await page.keyboard.press("Tab");
      await assertKeyboardFocus(`shell tab stop ${tab + 1}`);
    }
    await expect(commandTrigger).toBeFocused();

    await page.keyboard.press("Control+k");
    const search = page.getByRole("combobox", { name: "Search commands" });
    await expect(search).toBeFocused();
    await assertKeyboardFocus("command search");
    await page.keyboard.press("Tab");
    await assertKeyboardFocus("command result");

    await page.keyboard.press("Escape");
  });
}

for (const theme of ["light", "dark"] as const) {
  test(`disabling every decorative material effect preserves keyboard use and axe in ${theme}`, async ({
    page,
  }) => {
    await selectTheme(page, theme);
    await page.goto("/");
    await page.addStyleTag({
      content: `
        body::before { display: none !important; }
        .material-shadow, :focus-visible { box-shadow: none !important; }
        .glass-grid-header, .glass-command-palette, .glass-sheet-backdrop {
          background: var(--surface) !important;
          backdrop-filter: none !important;
        }
      `,
    });

    await page.keyboard.press("Tab");
    const skipLink = page.getByRole("link", { name: "Skip to main content" });
    await expect(skipLink).toBeFocused();
    await expect(skipLink).toHaveCSS("outline-style", "solid");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("main")).toBeFocused();

    await page.keyboard.press("Control+k");
    await expect(page.getByRole("combobox", { name: "Search commands" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("main")).toBeFocused();

    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
}
