import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

/** E22-S09: a person is shown by name with the email secondary. Any email reference must go through Person or personLabel. */
const SRC = join(__dirname, "../..");
const PATTERN = /\.(?:[a-z]+_)*emails?\b|["'](?:[a-z]+_)*emails?["']/g;

/** "<file>|<matched text>": the email being typed, proven or sent, not a person shown. */
const ALLOWED: Record<string, string> = {
  "features/auth/Screens.tsx|.email": "sign-in form: the email being typed",
  "features/auth/Screens.tsx|\"email\"": "sign-in form: input attributes",
  "features/invitation/Screen.tsx|.email": "invitation: the address the link was sent to, proven by the link",
  "features/invitation/Screen.tsx|\"email\"": "invitation: input attributes",
  "components/vendors/request/VendorRequestForm.tsx|.email": "vendor request form: contact email being typed",
  "components/vendors/request/VendorRequestForm.tsx|\"email\"": "vendor request form: input attributes",
  "components/requisitions/changes/ReviewScreen.tsx|.requester_email": "hands the requester to Versions, which renders it through Person",
  "routes/_dev/form-gallery.tsx|.email": "dev gallery: form validation demo",
  "routes/_dev/form-gallery.tsx|\"email\"": "dev gallery: input attributes",
};

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    return e.isDirectory() ? files(p) : p.endsWith(".tsx") && !p.endsWith(".test.tsx") ? [p] : [];
  });
}

function scan() {
  const hits: { key: string; where: string; ok: boolean }[] = [];
  for (const f of files(SRC)) {
    const rel = relative(SRC, f);
    readFileSync(f, "utf8").split("\n").forEach((line, i) => {
      for (const m of line.matchAll(PATTERN)) {
        const key = `${rel}|${m[0]}`;
        hits.push({ key, where: `${rel}:${i + 1} ${m[0]}`, ok: line.includes("<Person") || line.includes("personLabel(") });
      }
    });
  }
  return hits;
}

describe("person adoption", () => {
  it("every email reference renders through Person or personLabel, or is allowed", () => {
    const bad = scan().filter((h) => !h.ok && !(h.key in ALLOWED));
    expect(bad.map((h) => h.where)).toEqual([]);
  });
  it("every ALLOWED entry still matches something", () => {
    const keys = new Set(scan().map((h) => h.key));
    expect(Object.keys(ALLOWED).filter((k) => !keys.has(k))).toEqual([]);
  });
});
