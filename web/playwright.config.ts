import { defineConfig } from "@playwright/test";
import path from "node:path";

process.env.PLAYWRIGHT_BROWSERS_PATH = path.resolve("../.cache/ms-playwright");

export default defineConfig({
  globalTeardown: "./tests/teardown.ts",
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5173",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `"${path.resolve(process.platform === "win32" ? "../.venv/Scripts/python.exe" : "../.venv/bin/python")}" "${path.resolve("../scripts/browser_test_server.py")}"`,
      url: "http://127.0.0.1:8765/api/v1/test-ready",
      timeout: 150_000,
      reuseExistingServer: false,
    },
    {
      command: "npm run dev",
      url: "http://127.0.0.1:5173",
      timeout: 30_000,
      reuseExistingServer: false,
    },
  ],
});
