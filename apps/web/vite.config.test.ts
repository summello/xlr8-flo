import { afterEach, describe, expect, it, vi } from "vitest";
import { resolveConfig } from "vite";

const FIXTURES = ["xlr8flo-grid-fixture", "xlr8flo-form-fixture"];

async function resolved(mode: string) {
  const config = await resolveConfig({ configFile: "./vite.config.ts" }, "serve", mode);
  return {
    names: config.plugins.map((plugin) => plugin.name),
    proxy: config.server.proxy,
  };
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("the acc-008 walkthrough harness", () => {
  it("proxies /api with the origin secret and registers no fixture", async () => {
    vi.stubEnv("ORIGIN_SHARED_SECRET", "s".repeat(32));
    const { names, proxy } = await resolved("walkthrough");
    for (const fixture of FIXTURES) {
      expect(names).not.toContain(fixture);
    }
    expect(proxy).toMatchObject({
      "/api": {
        headers: { "X-FLO-Origin-Secret": "s".repeat(32) },
        target: "http://127.0.0.1:8000",
      },
    });
  });

  it("refuses to start without a 32 character origin secret", async () => {
    vi.stubEnv("ORIGIN_SHARED_SECRET", "short");
    await expect(resolved("walkthrough")).rejects.toThrow("ORIGIN_SHARED_SECRET");
  });

  it("leaves every other mode unchanged: both fixtures, no proxy", async () => {
    const { names, proxy } = await resolved("development");
    for (const fixture of FIXTURES) {
      expect(names).toContain(fixture);
    }
    expect(proxy).toBeUndefined();
  });
});
