import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

type Theme = "light" | "dark";
type ThemeValues = Record<string, string>;
type ThemePair = {
  background: string;
  foreground: string;
  label: string;
  threshold: number;
};
type DocumentedPalette = {
  expected: Record<Theme, ThemeValues>;
  money: Record<string, string>;
  pairs: ThemePair[];
  rootOnly: ThemeValues;
};
type SupportingTokens = Record<Theme, ThemeValues>;

const masterPath = fileURLToPath(
  new URL("../../../../../design-system/MASTER.md", import.meta.url),
);
const tokensPath = fileURLToPath(new URL("../tokens.css", import.meta.url));
const basePath = fileURLToPath(new URL("../base.css", import.meta.url));
const master = readFileSync(masterPath, "utf8");
const tokens = readFileSync(tokensPath, "utf8");
const base = readFileSync(basePath, "utf8");

function section(source: string, start: string, end: string): string {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  if (startIndex < 0 || endIndex < 0) {
    throw new Error(`Missing documented section ${start}`);
  }
  return source.slice(startIndex, endIndex);
}

function blockAfter(source: string, marker: string, startAt = 0): string {
  const markerIndex = source.indexOf(marker, startAt);
  if (markerIndex < 0) {
    throw new Error(`Missing CSS block ${marker}`);
  }
  const openIndex = source.indexOf("{", markerIndex);
  if (openIndex < 0) {
    throw new Error(`Missing opening brace for ${marker}`);
  }

  let depth = 1;
  for (let index = openIndex + 1; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) return source.slice(openIndex + 1, index);
  }
  throw new Error(`Missing closing brace for ${marker}`);
}

function declarations(block: string): ThemeValues {
  return Object.fromEntries(
    [...block.matchAll(/--([a-z0-9-]+)\s*:\s*([^;]+);/gi)].map((match) => [
      `--${match[1]}`,
      match[2]!.trim(),
    ]),
  );
}

function declarationOnlyRemainder(block: string): string {
  return block
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\bcolor-scheme\s*:\s*(?:light|dark)\s*;/g, "")
    .replace(/--[a-z0-9-]+\s*:\s*[^;]+;/gi, "")
    .trim();
}

function normalize(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

function tableRows(markdown: string): string[][] {
  const rows = markdown
    .split("\n")
    .filter((line) => line.startsWith("|"))
    .map((line) => line.slice(1, -1).split("|").map((cell) => cell.trim()));
  const divider = rows.findIndex((row) => row.every((cell) => /^:?-+:?$/.test(cell)));
  if (divider < 0) throw new Error("MASTER.md table has no header divider");
  return rows.slice(divider + 1);
}

function parsedTableRows<T>(
  markdown: string,
  sectionName: string,
  parse: (row: string[]) => T | undefined,
): T[] {
  const rows = tableRows(markdown);
  const parsed = rows.map((row) => ({ row, value: parse(row) }));
  const entries = parsed.flatMap(({ value }) => (value === undefined ? [] : [value]));
  if (entries.length !== rows.length) {
    const failed = parsed.find(({ value }) => value === undefined)!.row;
    throw new Error(
      `Unable to parse MASTER.md ${sectionName} row: | ${failed.join(" | ")} |`,
    );
  }
  return entries;
}

function inlineCode(cell: string): string[] {
  return [...cell.matchAll(/`([^`]+)`/g)].map((match) => match[1]!);
}

function parseDocumentedPalette(source: string): DocumentedPalette {
  const colour = section(source, "## 2. Colour", "## 3. Material");
  const expected: Record<Theme, ThemeValues> = { light: {}, dark: {} };
  const money: Record<string, string> = {};
  const rootOnly: ThemeValues = {};
  const pairs: ThemePair[] = [];

  const chrome = section(colour, "### 2.1 Chrome", "### 2.2 Lifecycle");
  const chromeCode = section(chrome, "```css", "```");
  Object.assign(expected.light, declarations(blockAfter(chromeCode, ":root")));
  Object.assign(
    expected.dark,
    declarations(blockAfter(chromeCode, ':root[data-theme="dark"]')),
  );
  // Floors come from the LIGHT block only: MASTER.md documents there the measured
  // minimum across both themes, and the dark block's ratios are that theme's own
  // measurements. Reading both would apply a dark measurement as a light floor.
  // The whitespace before the ratio also matters: without it a greedy prefix swallows
  // the leading digits and "4.5:1" is read as a floor of 5.
  const chromeLight = blockAfter(chromeCode, ":root");
  for (const match of chromeLight.matchAll(/(--[a-z-]+):[^;]+;\s*\/\*[^*]*\s(\d+(?:\.\d+)?)\s*:1/gi)) {
    pairs.push({
      background: "--canvas",
      foreground: match[1]!,
      label: `chrome ${match[1]}`,
      // The floor is the ratio MASTER.md documents beside the token, not a constant:
      // section 2.1 carries text at 4.5 and control boundaries at 3, and a constant
      // here would over-enforce the second on a compliant palette.
      threshold: Number(match[2]),
    });
  }
  const focusToken = chromeCode.match(/(--[a-z-]+):[^;]+;\s*\/\*\s*focus\s*\*\//i)?.[1];
  if (focusToken === undefined) throw new Error("MASTER.md does not document a focus token");
  pairs.push({
    background: "--canvas",
    foreground: focusToken,
    label: `focus ${focusToken}`,
    threshold: 3,
  });

  const phases = section(colour, "### 2.2 Lifecycle", "### 2.3 Status");
  const documentedPhases = parsedTableRows(phases, "section 2.2", (row) => {
    const phase = row[0]?.match(/\*\*([A-Za-z]+)\*\*/)?.[1]?.toLowerCase();
    const light = inlineCode(row[3] ?? "")[0];
    const dark = inlineCode(row[4] ?? "")[0];
    if (phase === undefined || light === undefined || dark === undefined) return undefined;
    return { dark, light, phase };
  });
  for (const { dark, light, phase } of documentedPhases) {
    const token = `--phase-${phase}`;
    expected.light[token] = light;
    expected.dark[token] = dark;
    pairs.push({
      background: "--canvas",
      foreground: token,
      label: `phase ${phase}`,
      threshold: 3,
    });
  }

  const statuses = section(colour, "### 2.3 Status", "### 2.4 Tags");
  const documentedStatuses = parsedTableRows(statuses, "section 2.3", (row) => {
    const token = inlineCode(row[0] ?? "")[0];
    const light = inlineCode(row[2] ?? "");
    const dark = inlineCode(row[3] ?? "");
    if (token === undefined || light.length !== 2 || dark.length !== 2) return undefined;
    return { dark, light, token };
  });
  for (const { dark, light, token } of documentedStatuses) {
    const tint = `${token}-tint`;
    expected.light[token] = light[0]!;
    expected.light[tint] = light[1]!;
    expected.dark[token] = dark[0]!;
    expected.dark[tint] = dark[1]!;
    pairs.push({
      background: tint,
      foreground: token,
      label: `status ${token}`,
      threshold: 4.5,
    });
  }

  const tags = section(colour, "### 2.4 Tags", "### 2.5 Money");
  const documentedTags = parsedTableRows(tags, "section 2.4", (row) => {
    const swatch = inlineCode(row[0] ?? "")[0];
    const light = inlineCode(row[1] ?? "");
    const dark = inlineCode(row[2] ?? "");
    if (swatch === undefined || light.length !== 2 || dark.length !== 2) return undefined;
    return { dark, light, swatch };
  });
  for (const { dark, light, swatch } of documentedTags) {
    const token = `--tag-${swatch}`;
    const tint = `${token}-tint`;
    expected.light[token] = light[0]!;
    expected.light[tint] = light[1]!;
    expected.dark[token] = dark[0]!;
    expected.dark[tint] = dark[1]!;
    pairs.push(
      {
        background: tint,
        foreground: token,
        label: `tag text ${swatch}`,
        threshold: 4.5,
      },
      {
        background: "--canvas",
        foreground: token,
        label: `tag mark ${swatch}`,
        threshold: 3,
      },
    );
  }

  const moneySection = section(colour, "### 2.5 Money", "### 2.6 Chart");
  const moneyCode = section(moneySection, "```", "```");
  const moneyRows = moneyCode.split("\n").slice(1).map((line) => line.trim()).filter(Boolean);
  for (const row of moneyRows) {
    const mapping = row.match(/^([a-z]+)[^\n]*?(--[a-z-]+)/);
    if (mapping === null) {
      throw new Error(`Unable to parse MASTER.md section 2.5 row: ${row}`);
    }
    const role = mapping[1]!;
    const documentedToken = mapping[2]!;
    const token = `--money-${role}`;
    money[token] = documentedToken;
    pairs.push({
      background: "--canvas",
      foreground: token,
      label: `money ${role}`,
      threshold: 4.5,
    });
  }

  const charts = section(colour, "### 2.6 Chart", "### 2.7 Hard rules");
  const documentedCharts = parsedTableRows(charts, "section 2.6", (row) => {
    const series = row[0]?.match(/\d+/)?.[0];
    const light = inlineCode(row[1] ?? "")[0];
    const dark = inlineCode(row[2] ?? "")[0];
    const stroke = inlineCode(row[3] ?? "")[0] ?? "none";
    if (series === undefined || light === undefined || dark === undefined) return undefined;
    return { dark, light, marker: (row[3] ?? "").includes("point marker"), series, stroke };
  });
  for (const { dark, light, marker, series, stroke } of documentedCharts) {
    const token = `--chart-${series}`;
    expected.light[token] = light;
    expected.dark[token] = dark;
    rootOnly[`${token}-stroke`] = stroke;
    if (marker) rootOnly[`${token}-marker`] = "circle";
    pairs.push({
      background: "--surface",
      foreground: token,
      label: `chart series ${series}`,
      threshold: 3,
    });
  }

  return { expected, money, pairs, rootOnly };
}

function parseSupportingTokens(source: string): SupportingTokens {
  const expected: SupportingTokens = { dark: {}, light: {} };
  const material = section(source, "## 3. Material", "## 4. Typography");
  const shadows = section(material, "### 3.1 Shadows", "### 3.2 The lit edge");
  const shadowCode = section(shadows, "```css", "```");
  Object.assign(expected.light, declarations(blockAfter(shadowCode, ":root")));
  Object.assign(
    expected.dark,
    declarations(blockAfter(shadowCode, ':root[data-theme="dark"]')),
  );

  const litEdge = section(material, "### 3.2 The lit edge", "### 3.3 Grain");
  const documentedEdges = [...litEdge.matchAll(/box-shadow:\s*var\(--shadow-md\),\s*([^;]+);/g)];
  if (documentedEdges.length !== 2) throw new Error("MASTER.md must document both lit edges");
  expected.light["--lit-edge"] = documentedEdges[0]![1]!.trim();
  expected.dark["--lit-edge"] = documentedEdges[1]![1]!.trim();

  const typography = section(source, "## 4. Typography", "## 5. Space");
  const fontCode = section(typography, "```css", "```");
  Object.assign(expected.light, declarations(fontCode));
  const documentedTypeScale = parsedTableRows(typography, "section 4", (row) => {
    const role = row[0]?.toLocaleLowerCase().replace(/\s+/g, "-");
    const dimensions = row[1]?.match(/^(\d+)\s*\/\s*(\d+)$/);
    const weight = row[2]?.match(/^\d+$/)?.[0];
    const tracking = row[3]
      ?.replace("−", "-")
      .replace(/^\+/, "")
      .match(/^-?(?:\d+(?:\.\d+)?)(?:em)?/)?.[0];
    if (
      role === undefined ||
      dimensions == null ||
      weight === undefined ||
      tracking === undefined
    ) {
      return undefined;
    }
    return {
      leading: `${dimensions[2]}px`,
      role,
      size: `${dimensions[1]}px`,
      tracking,
      weight,
    };
  });
  for (const { leading, role, size, tracking, weight } of documentedTypeScale) {
    expected.light[`--text-${role}`] = size;
    expected.light[`--leading-${role}`] = leading;
    expected.light[`--weight-${role}`] = weight;
    expected.light[`--tracking-${role}`] = tracking;
  }
  const mobileInputSize = typography.match(
    /Two exceptions where (\d+px) is mandatory:\**\s*any input below \d+px/,
  )?.[1];
  if (mobileInputSize === undefined) {
    throw new Error("MASTER.md does not document the mobile input text size");
  }
  expected.light["--text-input-mobile"] = mobileInputSize;

  const spacing = section(source, "## 5. Space", "## 6. Motion");
  const scale = spacing.match(/4px base grid\. `([^`]+)`/)?.[1];
  if (scale === undefined) throw new Error("MASTER.md does not document the spacing scale");
  for (const [index, value] of scale.split("·").map((item) => item.trim()).entries()) {
    expected.light[`--space-${index + 1}`] = `${value}px`;
  }
  Object.assign(expected.light, declarations(spacing));

  const motion = section(source, "## 6. Motion", "## 7. Components");
  const motionCode = section(motion, "```css", "```");
  Object.assign(expected.light, declarations(motionCode));

  const components = section(source, "## 7. Components", "## 8. Definition");
  const icons = section(components, "### 7.10 Icons", "---");
  const documentedIconSizes = icons.match(
    /(\d+px) inline\s*\/\s*(\d+px) control\s*\/\s*(\d+px) nav/,
  );
  if (documentedIconSizes === null) {
    throw new Error("MASTER.md does not document all three icon sizes");
  }
  for (const [index, role] of ["inline", "control", "nav"].entries()) {
    expected.light[`--icon-${role}`] = documentedIconSizes[index + 1]!;
  }

  const definitionOfDone = source.slice(source.indexOf("## 8. Definition"));
  const touchTarget = definitionOfDone.match(/Touch targets\s*≥\s*(\d+px)/)?.[1];
  if (touchTarget === undefined) {
    throw new Error("MASTER.md does not document the minimum touch target");
  }
  expected.light["--touch-target"] = touchTarget;
  return expected;
}

function parseTokenThemes(source: string) {
  const lightBlock = blockAfter(source, ":root {");
  const explicitDarkBlock = blockAfter(source, ':root[data-theme="dark"]');
  const mediaBlock = blockAfter(source, "@media (prefers-color-scheme: dark)");
  const systemDarkBlock = blockAfter(mediaBlock, ':root:not([data-theme="light"])');
  return {
    explicitDark: declarations(explicitDarkBlock),
    light: declarations(lightBlock),
    systemDark: declarations(systemDarkBlock),
  };
}

function resolve(values: ThemeValues, token: string): string {
  const value = values[token];
  if (value === undefined) throw new Error(`Missing token ${token}`);
  const reference = value.match(/^var\((--[a-z0-9-]+)\)$/i)?.[1];
  return reference === undefined ? value : resolve(values, reference);
}

function linearChannel(channel: number): number {
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
}

function parseColour(value: string): [number, number, number] {
  const hex = value.match(/^#([0-9a-f]{6})$/i)?.[1];
  if (hex !== undefined) {
    return [0, 2, 4].map((offset) => linearChannel(Number.parseInt(hex.slice(offset, offset + 2), 16) / 255)) as [
      number,
      number,
      number,
    ];
  }

  const oklch = value.match(
    /^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*[\d.]+)?\s*\)$/i,
  );
  if (oklch === null) throw new Error(`Unsupported colour ${value}`);
  const lightness = Number(oklch[1]);
  const chroma = Number(oklch[2]);
  const hue = (Number(oklch[3]) * Math.PI) / 180;
  const a = chroma * Math.cos(hue);
  const b = chroma * Math.sin(hue);
  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map((channel) => Math.min(1, Math.max(0, channel))) as [number, number, number];
}

function luminance(value: string): number {
  const [red, green, blue] = parseColour(value);
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrast(foreground: string, background: string): number {
  const first = luminance(foreground);
  const second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

const documented = parseDocumentedPalette(master);
const supporting = parseSupportingTokens(master);
const actual = parseTokenThemes(tokens);

function actualThemeValues(theme: Theme): ThemeValues {
  return theme === "light" ? actual.light : { ...actual.light, ...actual.explicitDark };
}

describe("design token contract", () => {
  it("transcribes every MASTER.md section 2 token with its documented value", () => {
    for (const theme of ["light", "dark"] as const) {
      const themeValues = theme === "light" ? actual.light : actual.explicitDark;
      for (const [token, expectedValue] of Object.entries(documented.expected[theme])) {
        expect(themeValues[token], `${theme}: missing token ${token}`).toBeDefined();
        expect(normalize(themeValues[token]!), `${theme}: ${token}`).toBe(normalize(expectedValue));
      }
    }
    for (const [token, expectedValue] of Object.entries(documented.rootOnly)) {
      expect(actual.light[token], `light: missing token ${token}`).toBeDefined();
      expect(normalize(actual.light[token]!), `light: ${token}`).toBe(normalize(expectedValue));
    }
  });

  it("maps every money-direction token to its documented semantic colour", () => {
    for (const theme of ["light", "dark"] as const) {
      const values = actualThemeValues(theme);
      for (const [token, documentedToken] of Object.entries(documented.money)) {
        expect(values[token], `${theme}: missing token ${token}`).toBeDefined();
        expect(resolve(values, token), `${theme}: ${token} must resolve to ${documentedToken}`).toBe(
          resolve(values, documentedToken),
        );
      }
    }
  });

  it("transcribes every documented supporting design token", () => {
    for (const [token, expectedValue] of Object.entries(supporting.light)) {
      expect(actual.light[token], `light: missing token ${token}`).toBeDefined();
      expect(normalize(actual.light[token]!), `light: ${token}`).toBe(normalize(expectedValue));
    }
    for (const [token, expectedValue] of Object.entries(supporting.dark)) {
      expect(actual.explicitDark[token], `dark: missing token ${token}`).toBeDefined();
      expect(normalize(actual.explicitDark[token]!), `dark: ${token}`).toBe(
        normalize(expectedValue),
      );
    }
  });

  it("makes the un-stamped dark-OS state identical to explicit dark", () => {
    expect(actual.systemDark).toEqual(actual.explicitDark);
  });

  it("sets the native colour scheme in all three theme states", () => {
    expect(blockAfter(tokens, ":root {")).toMatch(/\bcolor-scheme\s*:\s*light\s*;/);
    expect(blockAfter(tokens, ':root[data-theme="dark"]')).toMatch(
      /\bcolor-scheme\s*:\s*dark\s*;/,
    );
    const media = blockAfter(tokens, "@media (prefers-color-scheme: dark)");
    expect(blockAfter(media, ':root:not([data-theme="light"])')).toMatch(
      /\bcolor-scheme\s*:\s*dark\s*;/,
    );
  });

  it("keeps explicit and system theme blocks limited to token redefinitions", () => {
    expect(declarationOnlyRemainder(blockAfter(tokens, ':root[data-theme="dark"]'))).toBe("");
    const media = blockAfter(tokens, "@media (prefers-color-scheme: dark)");
    expect(
      declarationOnlyRemainder(blockAfter(media, ':root:not([data-theme="light"])')),
    ).toBe("");
  });

  it("paints the body on the documented canvas token", () => {
    expect(blockAfter(base, "body")).toMatch(/\bbackground\s*:\s*var\(--canvas\)\s*;/);
  });

  it("meets every contrast pair derived from MASTER.md section 2 in both themes", () => {
    expect(documented.pairs.length).toBeGreaterThan(0);
    for (const theme of ["light", "dark"] as const) {
      const values = actualThemeValues(theme);
      for (const pair of documented.pairs) {
        const ratio = contrast(resolve(values, pair.foreground), resolve(values, pair.background));
        expect(
          ratio,
          `${theme}: ${pair.label} (${pair.foreground} on ${pair.background}) is ${ratio.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(pair.threshold);
      }
    }
  });
});
