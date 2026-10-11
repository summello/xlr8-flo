import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";
function literals(source: string) { return source.match(/\b\d+(?:\.\d+)?(?:px|ms|s)\b/g) ?? []; }
describe("project UI source guards", () => {
  it("uses tokens or named properties for dimensions and durations", () => {
    for (const folder of [new URL(".", import.meta.url), new URL("../../routes/projects/", import.meta.url)]) {
      for (const file of readdirSync(folder).filter(f => /\.tsx?$/.test(f) && !f.endsWith(".test.ts"))) {
        expect(literals(readFileSync(new URL(file, folder), "utf8")), file).toEqual([]);
      }
    }
    expect(literals(readFileSync(new URL("../ui/Money.tsx", import.meta.url), "utf8"))).toEqual([]);
  });
  it("rejects planted source dimensions", () => {
    expect(literals('height: "45px", transitionDuration: "7s"')).toEqual(["45px", "7s"]);
  });
  it("money formatting never coerces decimal amounts to floating point", () => {
    const source = readFileSync(new URL("../../lib/money.ts", import.meta.url), "utf8");
    expect(source).not.toMatch(/\b(?:Number|parseFloat)\s*\(/);
  });
});
