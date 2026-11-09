import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import type { ReviewState, TableView } from "../src/api/models";
import {
  apiHeaders,
  openPageTwoTable,
  reviewProgress,
  tableIdFromUrl,
} from "./helpers";

async function view(page: Page) {
  return (await (
    await page.request.get(`/api/v1/tables/${tableIdFromUrl(page)}`)
  ).json()) as TableView;
}

async function downloadLatest(page: Page) {
  const button = page
    .getByRole("button", { name: "Download", exact: true })
    .first();
  await expect(button).toBeVisible({ timeout: 45_000 });
  const waiting = page.waitForEvent("download");
  await button.click();
  return waiting;
}

test("a draft CSV package is downloadable and marked as a draft", async ({
  page,
}, info) => {
  await openPageTwoTable(page);
  const current = await view(page);
  await page.getByRole("button", { name: "Finish", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Finish this table" });
  await dialog.getByRole("button", { name: "CSV package" }).click();

  await expect(page.locator(".toast")).toContainText(
    "CSV package export started",
  );
  await expect(dialog.getByText("CSV package (.zip)")).toBeVisible({
    timeout: 30_000,
  });
  await expect(dialog).toContainText("Draft");
  const download = await downloadLatest(page);
  expect(download.suggestedFilename()).toMatch(/^table-draft-[0-9a-f-]+\.zip$/);
  await download.saveAs(info.outputPath("draft.zip"));
  expect(
    (await fs.readFile(info.outputPath("draft.zip"))).subarray(0, 2).toString(),
  ).toBe("PK");
  expect(download.suggestedFilename()).toContain(current.revision.id);
});

test("an unreadable cell forces approval with warnings", async ({ page }) => {
  await openPageTwoTable(page);
  const current = await view(page);
  const reviewed = await page.request.post(
    `/api/v1/revisions/${current.revision.id}/cell-review`,
    {
      headers: await apiHeaders(page),
      data: {
        cell_ids: current.revision.content.cells.map((item) => item.id),
        verdict: "confirmed_value",
        reason: "",
      },
    },
  );
  expect(reviewed.status()).toBe(200);

  const first = current.revision.content.cells[0];
  const unreadable = await page.request.post(
    `/api/v1/revisions/${current.revision.id}/cell-review`,
    {
      headers: await apiHeaders(page),
      data: {
        cell_ids: [first.id],
        verdict: "unreadable",
        reason: "The source digits are blurred",
      },
    },
  );
  expect(unreadable.status()).toBe(200);
  const state = (await unreadable.json()) as ReviewState;
  expect(state.unreadable_count).toBe(1);
  expect(state.checked_cell_ids).toHaveLength(
    current.revision.content.cells.length,
  );

  await page.reload();
  await expect(reviewProgress(page)).toContainText("12/12");
  await page.getByRole("button", { name: "Finish", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Finish this table" });
  await expect(dialog).toContainText("1 cell marked unreadable");
  await expect(
    dialog.getByRole("button", { name: "Approve table", exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "Approve with warnings", exact: true }),
  ).toBeEnabled();
});

test("a failed export request keeps its retry key and can be retried", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const current = await view(page);
  const keys: string[] = [];
  let attempts = 0;
  await page.route("**/api/v1/tables/*/exports", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    keys.push(route.request().headers()["idempotency-key"] ?? "");
    attempts++;
    if (attempts === 1)
      await route.fulfill({
        status: 500,
        json: {
          code: "export_failed",
          message: "The export service is unavailable",
        },
      });
    else
      await route.fulfill({
        json: {
          id: crypto.randomUUID(),
          table_id: current.table.id,
          revision_id: current.revision.id,
          format: "workbook",
          state: "queued",
          manifest: { review_status: "draft" },
        },
      });
  });

  await page.getByRole("button", { name: "Finish", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Finish this table" });
  await dialog.getByRole("button", { name: "Workbook" }).click();
  await expect(page.locator(".toast-error")).toContainText(
    "The export service is unavailable",
  );
  await expect(dialog.getByRole("button", { name: "Workbook" })).toBeEnabled();

  await dialog.getByRole("button", { name: "Workbook" }).click();
  await expect(
    page.locator(".toast").filter({ hasText: "Workbook export started" }),
  ).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
});

test("failed and pending export records are described instead of offered", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const current = await view(page);
  await page.route("**/api/v1/tables/*/exports", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: crypto.randomUUID(),
            table_id: current.table.id,
            revision_id: current.revision.id,
            format: "workbook",
            state: "failed",
            failure_code: "workbook_text_unsupported",
            manifest: { review_status: "draft" },
          },
          {
            id: crypto.randomUUID(),
            table_id: current.table.id,
            revision_id: current.revision.id,
            format: "csv_package",
            state: "queued",
            manifest: { review_status: "draft" },
          },
        ],
      },
    }),
  );

  await page.getByRole("button", { name: "Finish", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Finish this table" });
  await expect(dialog.getByText("Use a CSV package")).toBeVisible();
  await expect(dialog.getByText("Preparing", { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Download", exact: true }),
  ).toHaveCount(0);
});
