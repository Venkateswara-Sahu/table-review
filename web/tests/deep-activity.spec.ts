import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import type { Document, Task } from "../src/api/models";
import { fixtureTask, openReport, signIn } from "./helpers";

async function reportDocument(page: import("@playwright/test").Page) {
  const documents = (await (
    await page.request.get("/api/v1/documents")
  ).json()) as { items: Document[] };
  const report = documents.items.find((item) => item.filename === "report.pdf");
  if (!report) throw new Error("The fixture report is missing");
  return report;
}

test("earlier extraction attempts are collapsed and can be expanded", async ({
  page,
}) => {
  await signIn(page);
  const document = await reportDocument(page);
  const now = Date.now();
  const attempts: Task[] = [2, 1, 0].map((offset) =>
    fixtureTask({
      document_id: document.id,
      state: "failed",
      failure_code: "ocr_unavailable",
      attempt_count: 3,
      result: {},
      created_at: new Date(now - offset * 60_000).toISOString(),
    }),
  );
  await page.route("**/api/v1/documents/*/tasks", (route) =>
    route.fulfill({ json: { items: attempts } }),
  );

  await page.goto(`/reports/${document.id}/pages/1`);
  await page.getByRole("button", { name: /^Activity/ }).click();
  const activity = page.getByRole("dialog", { name: "Activity" });
  const group = activity.locator(".activity-group").first();
  await expect(group).toContainText("Page 1");
  await expect(group).toContainText("3 attempts kept");
  const earlier = group.locator("details.activity-attempts");
  await expect(earlier.locator("summary")).toContainText("2 earlier attempts");
  await earlier.locator("summary").click();
  await expect(earlier.locator(".timeline-row")).toHaveCount(2);
  await expect(
    group.getByRole("button", { name: "Retry page", exact: true }),
  ).toBeVisible();
});

test("retrying a failed page sends the failed task and keeps the drawer", async ({
  page,
}) => {
  await signIn(page);
  const document = await reportDocument(page);
  const failed = fixtureTask({
    document_id: document.id,
    state: "failed",
    failure_code: "parse_failure",
    result: {},
  });
  await page.route("**/api/v1/documents/*/tasks", (route) =>
    route.fulfill({ json: { items: [failed] } }),
  );
  await page.goto(`/reports/${document.id}/pages/1`);
  await page.getByRole("button", { name: /^Activity/ }).click();
  const activity = page.getByRole("dialog", { name: "Activity" });

  const retrying = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(`/api/v1/tasks/${failed.id}/retry`),
  );
  await page.route("**/api/v1/tasks/*/retry", (route) =>
    route.fulfill({
      json: fixtureTask({
        document_id: document.id,
        state: "queued",
        result: {},
      }),
    }),
  );
  await activity
    .getByRole("button", { name: "Retry page", exact: true })
    .click();
  await retrying;
  await expect(activity).toBeVisible();
});

test("run evidence reveals the manifest and downloads the exact record", async ({
  page,
}, testInfo) => {
  await openReport(page, "report.pdf");
  await page.getByRole("button", { name: /^Activity/ }).click();
  const activity = page.getByRole("dialog", { name: "Activity" });
  const run = activity.locator(".run-row").first();
  await expect(run).toBeVisible();
  await expect(run).toContainText("Pages 2");

  await run.getByRole("button", { name: "Run evidence" }).click();
  await expect(
    run.getByRole("button", { name: "Hide evidence" }),
  ).toBeVisible();
  await expect(run.getByText("Recorded manifest")).toBeVisible();
  await run.getByText("Recorded manifest").click();
  await expect(run.locator(".run-evidence pre")).toContainText('"pages"');

  const downloading = page.waitForEvent("download");
  await run.getByRole("button", { name: "JSON" }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/^extraction-.+\.json$/);
  const path = await download.path();
  if (!path) throw new Error("Run evidence download has no local path");
  const record = JSON.parse(await fs.readFile(path, "utf8")) as {
    id?: string;
    manifest?: Record<string, unknown>;
    table_ids?: string[];
  };
  expect(record.id).toBeTruthy();
  expect(record.manifest).toBeTruthy();
  expect(Array.isArray(record.table_ids)).toBe(true);
  await download.saveAs(testInfo.outputPath("run-evidence.json"));
});

test("a failed run history read can be retried in place", async ({ page }) => {
  let attempts = 0;
  await page.route(
    /\/api\/v1\/documents\/[^/]+\/runs(?:\?|$)/,
    async (route) => {
      attempts++;
      if (attempts === 1)
        await route.fulfill({
          status: 400,
          json: {
            code: "runs_unavailable",
            message: "Run history is unavailable",
          },
        });
      else await route.continue();
    },
  );

  await openReport(page, "report.pdf");
  await page.getByRole("button", { name: /^Activity/ }).click();
  const activity = page.getByRole("dialog", { name: "Activity" });
  await expect(activity).toContainText("Run history is unavailable");
  await activity
    .getByRole("button", { name: "Try again", exact: true })
    .click();
  await expect(activity.locator(".run-row").first()).toBeVisible();
  expect(attempts).toBeGreaterThanOrEqual(2);
});

test("cancelling running work shows that the stop was requested", async ({
  page,
}) => {
  await signIn(page);
  const document = await reportDocument(page);
  const running = fixtureTask({
    document_id: document.id,
    state: "running",
    result: {},
  });
  await page.route("**/api/v1/documents/*/tasks", (route) =>
    route.fulfill({ json: { items: [running] } }),
  );
  await page.route("**/api/v1/tasks/*/cancel", async (route) => {
    running.cancellation_requested = true;
    await route.fulfill({ json: running });
  });

  await page.goto(`/reports/${document.id}/pages/1`);
  await page
    .locator(".command")
    .getByRole("button", { name: /^Activity/ })
    .click();
  const activity = page.getByRole("dialog", { name: "Activity" });
  const cancelling = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(`/api/v1/tasks/${running.id}/cancel`),
  );
  await activity.getByRole("button", { name: "Cancel", exact: true }).click();
  await cancelling;
  await expect(
    activity.getByRole("button", { name: "Stopping…" }),
  ).toBeDisabled();
});
