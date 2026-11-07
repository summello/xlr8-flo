import { createHash } from "node:crypto";

import { defineConfig } from "@playwright/test";

// One port per checkout. Story worktrees run their suites concurrently, and a fixed
// port made `reuseExistingServer` hand a run the *other* worktree's dev server —
// which serves different code and fails tests that have nothing wrong with them.
// E2E_PORT overrides it when something else already holds the hashed port.
const port =
  Number(process.env.E2E_PORT) ||
  4173 + (createHash("sha256").update(import.meta.dirname).digest()[0]! % 200);
const url = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: ".",
  testMatch: ["e2e/**/*.e2e.ts", "tests/{a11y,e2e}/**/*.spec.ts"],
  fullyParallel: true,
  // ponytail: cap browser contention at two workers; use --workers on an idle host.
  workers: 2,
  // A pass on retry is reported as flaky, not hidden.
  // ponytail: drop the CI retry after three consecutive green milestone runs with zero flakes.
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: url,
  },
  // Frame time is wall-clock, so the budget test runs alone after everything else: beside
  // parallel workers it measured 27 ms against a 20 ms budget on a healthy machine.
  projects: [
    { name: "main", grepInvert: /frame budget/ },
    { name: "perf", grep: /frame budget/, dependencies: ["main"] },
  ],
  webServer: {
    command: process.env.CI
      ? `npm run build && npm run preview -- --host 127.0.0.1 --port ${port} --strictPort`
      : `npm run dev -- --host 127.0.0.1 --port ${port}`,
    // Build + preview took 268 s on the loaded local host; keep the local dev default.
    timeout: process.env.CI ? 300_000 : 60_000,
    reuseExistingServer: !process.env.CI,
    url,
  },
});
