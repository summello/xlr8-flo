import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// The API sets these on its own responses, but the API serves JSON. The document response is
// what a CSP protects, and Pages serves that from public/_headers — so this is where the
// application's framing, sniffing and referrer defences actually live.
const headers = readFileSync(fileURLToPath(new URL("../public/_headers", import.meta.url)), "utf8");

function directive(name: string): string {
  const value = headers.match(new RegExp(`^\\s*${name}:\\s*(.+)$`, "mi"))?.[1];
  if (value === undefined) throw new Error(`public/_headers does not set ${name}`);
  return value.trim();
}

describe("document security headers", () => {
  it("applies to every document path", () => {
    expect(headers).toMatch(/^\/\*$/m);
  });

  it.each([
    ["Cross-Origin-Opener-Policy", "same-origin"],
    ["Referrer-Policy", "strict-origin-when-cross-origin"],
    ["X-Content-Type-Options", "nosniff"],
    ["X-Frame-Options", "DENY"],
  ])("sets %s", (name, expected) => {
    expect(directive(name)).toBe(expected);
  });

  it("sets HSTS for at least a year, including subdomains", () => {
    const hsts = directive("Strict-Transport-Security");
    expect(hsts).toContain("includeSubDomains");
    expect(Number(hsts.match(/max-age=(\d+)/)?.[1])).toBeGreaterThanOrEqual(31_536_000);
  });

  it("locks down the features the app never uses", () => {
    expect(directive("Permissions-Policy")).toBe("camera=(), geolocation=(), microphone=()");
  });

  it("ships a CSP with no unsafe-inline script and no wildcard source", () => {
    const csp = directive("Content-Security-Policy");
    const parts = Object.fromEntries(
      csp.split(";").map((part) => {
        const [name, ...values] = part.trim().split(/\s+/);
        return [name, values];
      }),
    );
    expect(parts["script-src"]).toEqual(["'self'"]);
    expect(parts["frame-ancestors"]).toEqual(["'none'"]);
    expect(parts["object-src"]).toEqual(["'none'"]);
    expect(parts["default-src"]).toEqual(["'self'"]);
    // Radix sets inline style attributes on positioned elements, so style-src-attr is the one
    // place 'unsafe-inline' is allowed — it cannot execute script.
    expect(parts["style-src"]).toEqual(["'self'"]);
    expect(parts["style-src-attr"]).toEqual(["'unsafe-inline'"]);
    expect(csp).not.toMatch(/script-src[^;]*unsafe-inline/);
    expect(csp).not.toMatch(/[\s:]\*/);
  });
});
