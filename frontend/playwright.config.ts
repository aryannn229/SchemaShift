import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  // One snapshot set for every OS; visual specs only run with E2E_VISUAL=1 (see docs/progress/phase-F.md).
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}{ext}",
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.02 } },
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173" },
});
