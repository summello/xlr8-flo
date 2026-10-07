import { createHash } from "node:crypto";

import { defineConfig } from "@playwright/test";

// One port per checkout. Story worktrees run their suites concurrently, and a fixed
// port made `reuseExistingServer` hand a run the *other* worktree's dev server —
// which serves different code and fails tests that have nothing wrong with them.
const port =
  4173 + (createHash("sha256").update(import.meta.dirname).digest()[0]! % 200);
const url = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: ".",
  testMatch: ["e2e/**/*.e2e.ts", "tests/{a11y,e2e}/**/*.spec.ts"],
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
    command: `npm run dev -- --host 127.0.0.1 --port ${port}`,
    reuseExistingServer: !process.env.CI,
    url,
  },
});
