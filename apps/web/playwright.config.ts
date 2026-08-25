import { createHash } from "node:crypto";

import { defineConfig } from "@playwright/test";

// One port per checkout. Story worktrees run their suites concurrently, and a fixed
// port made `reuseExistingServer` hand a run the *other* worktree's dev server —
// which serves different code and fails tests that have nothing wrong with them.
const port =
  4173 + (createHash("sha256").update(import.meta.dirname).digest()[0]! % 200);
const url = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  use: {
    baseURL: url,
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port}`,
    reuseExistingServer: !process.env.CI,
    url,
  },
});
