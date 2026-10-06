import { defineConfig } from "@playwright/test";
import path from "node:path";
process.env.PLAYWRIGHT_BROWSERS_PATH = path.resolve("../.cache/ms-playwright");
export default defineConfig({
  globalTeardown: "./tests/teardown.ts",
  testDir: "./tests",
  testMatch: "study.spec.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:5174" },
  webServer: {
    command: "npx vite --host 127.0.0.1 --port 5174 --strictPort",
    url: "http://127.0.0.1:5174",
    reuseExistingServer: false,
  },
});
