// Run from this folder:  DC_RUNTIME=<path to dc-runtime.js> AXE_CORE=<path to axe.min.js> npx playwright test
// DC_RUNTIME is artifact-type/dc-runtime.js from the canvas (read it with the Artifact tool, path "artifact-type/dc-runtime.js").
// AXE_CORE defaults to apps/web's copy of axe-core when run from a full checkout.
import { defineConfig } from 'playwright/test';

const PORT = process.env.PORT ?? '4173';   // set PORT when another checkout already serves 4173

export default defineConfig({
  testDir: '.',
  testMatch: '*.spec.mjs',
  timeout: 30_000,
  fullyParallel: true,
  reporter: [['list']],
  use: { baseURL: `http://localhost:${PORT}`, viewport: { width: 1440, height: 900 } },
  webServer: {
    command: 'node serve.mjs',
    url: `http://localhost:${PORT}/Main.dc.html`,
    reuseExistingServer: true,
    env: { DC_RUNTIME: process.env.DC_RUNTIME ?? '', PORT },
  },
});
