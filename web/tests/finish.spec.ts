import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import type { ReviewState, TableView } from "../src/api/models";
import {
  apiHeaders,
  cell,
  expectSaved,
  openPageTwoTable,
  reviewProgress,
  tableIdFromUrl,
} from "./helpers";

const editor = (page: Page) => page.locator("textarea[data-cell-text-editor]");

async function reviewEverything(page: Page) {
  const tableId = tableIdFromUrl(page);
  const view = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  const reviewed = await page.request.post(
    `/api/v1/revisions/${view.revision.id}/cell-review`,
    {
      headers: await apiHeaders(page),
      data: {
        cell_ids: view.revision.content.cells.map((item) => item.id),
        verdict: "confirmed_value",
        reason: "",
      },
    },
  );
  expect(reviewed.status()).toBe(200);
  let review = (await reviewed.json()) as ReviewState;
  for (const issue of review.issues) {
    const decision = await page.request.post(
      `/api/v1/revisions/${view.revision.id}/issues/${issue.id}/decision`,
      {
        headers: await apiHeaders(page),
        data: {
          kind: "dismiss",
          reason: "Compared the recorded literal with the pinned source page.",
        },
      },
    );
    expect(decision.status()).toBe(200);
    review = (await decision.json()) as ReviewState;
  }
  return { view, review };
}

async function downloadLatest(page: Page) {
  const downloadButton = page
    .getByRole("button", { name: "Download", exact: true })
    .first();
  await expect(downloadButton).toBeVisible({ timeout: 45_000 });
  const waiting = page.waitForEvent("download");
  await downloadButton.click();
  return waiting;
}

test("a draft export is explicit before approval", async ({ page }, info) => {
  await openPageTwoTable(page);
  await page.getByRole("button", { name: "Finish", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Finish this table" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("0 of 12 cells have a recorded decision");
  await expect(
    dialog.getByRole("button", { name: "Approve table", exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "Approve with warnings", exact: true }),
  ).toBeDisabled();

  const posting = page.waitForRequest(
    (request) =>
      request.method() === "POST" && request.url().endsWith("/exports"),
  );
  await dialog.getByRole("button", { name: "Workbook", exact: true }).click();
  const request = await posting;
  expect(request.postDataJSON()).toMatchObject({ format: "workbook" });
  await expect(page.locator(".toast")).toContainText("Workbook export started");
  await expect(dialog.getByText("Workbook (.xlsx)")).toBeVisible({
    timeout: 30_000,
  });
  await expect(dialog).toContainText("Draft");

  const download = await downloadLatest(page);
  expect(download.suggestedFilename()).toMatch(
    /^table-draft-[0-9a-f-]+\.xlsx$/,
  );
  await download.saveAs(info.outputPath("draft.xlsx"));
  expect(
    (await fs.readFile(info.outputPath("draft.xlsx")))
      .subarray(0, 2)
      .toString(),
  ).toBe("PK");
});

test("approval with warnings exports the pinned warning record and an edit clears it", async ({
  page,
}, info) => {
  test.setTimeout(120_000);
  await openPageTwoTable(page);
  await editor(page).fill("1234.50");
  await page.getByRole("button", { name: "Save value", exact: true }).click();
  await expectSaved(page);

  await cell(page, 0, 0).click();
  await cell(page, 3, 2).click({ modifiers: ["Shift"] });
  await expect(page.locator(".bulk-strip")).toContainText("12 cells selected");
  await page
    .getByRole("button", { name: "Confirm values", exact: true })
    .click();
  await expect(reviewProgress(page)).toContainText("12/12");

  await page.getByRole("button", { name: "Finish", exact: true }).click();
  const preflight = page.getByRole("dialog", { name: "Finish this table" });
  await expect(preflight).toContainText("1 unresolved check warning");
  await expect(
    preflight.getByRole("button", { name: "Approve table", exact: true }),
  ).toBeDisabled();
  await preflight
    .getByRole("button", { name: "Approve with warnings", exact: true })
    .click();

  const approved = page.getByRole("dialog", {
    name: "Approved and ready to export",
  });
  await expect(approved).toBeVisible();
  await expect(approved).toContainText("Reviewed with warnings");
  await approved
    .getByRole("button", { name: "Export workbook", exact: true })
    .click();
  const download = await downloadLatest(page);
  expect(download.suggestedFilename()).toContain("reviewed_with_warnings");
  expect(download.suggestedFilename()).toMatch(/\.xlsx$/);
  await download.saveAs(info.outputPath("reviewed-warnings.xlsx"));
  expect(
    (await fs.readFile(info.outputPath("reviewed-warnings.xlsx")))
      .subarray(0, 2)
      .toString(),
  ).toBe("PK");

  await approved
    .getByRole("button", { name: "Close", exact: true })
    .first()
    .click();
  await expect(page.locator(".review-approved-inline")).toContainText(
    "Approved with warnings",
  );

  await cell(page, 1, 1).click();
  await editor(page).fill("1234.51");
  await page.getByRole("button", { name: "Save value", exact: true }).click();
  await expectSaved(page);
  await expect(page.locator(".review-approved-inline")).toHaveCount(0);
  await expect(reviewProgress(page)).toContainText("11/12");

  await page.getByRole("button", { name: "Finish", exact: true }).click();
  const invalidated = page.getByRole("dialog", { name: "Finish this table" });
  await expect(invalidated).toContainText("Review the remaining 1 cell");
  await expect(
    invalidated.getByRole("button", { name: "Approve table", exact: true }),
  ).toBeDisabled();
  await expect(
    invalidated.getByRole("button", {
      name: "Approve with warnings",
      exact: true,
    }),
  ).toBeDisabled();

  const tableId = tableIdFromUrl(page);
  const view = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  const review = (await (
    await page.request.get(`/api/v1/revisions/${view.revision.id}/review`)
  ).json()) as ReviewState;
  expect(review.current_approval).toBeNull();
});

test("a fully reviewed revision can be approved cleanly and exported", async ({
  page,
}, info) => {
  test.setTimeout(120_000);
  await openPageTwoTable(page);
  const { view, review } = await reviewEverything(page);
  expect(review.checked_cell_ids.length).toBe(
    view.revision.content.cells.length,
  );
  expect(review.unresolved_count).toBe(0);
  expect(review.unreadable_count).toBe(0);
  expect(review.checks_not_run).toBe(false);

  await page.reload();
  await expect(reviewProgress(page)).toContainText("12/12");
  await page.getByRole("button", { name: "Finish", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Finish this table" });
  await expect(dialog).toContainText("You can approve this revision now.");
  await expect(
    dialog.getByRole("button", { name: "Approve table", exact: true }),
  ).toBeEnabled();
  await dialog
    .getByRole("button", { name: "Approve table", exact: true })
    .click();

  const approved = page.getByRole("dialog", {
    name: "Approved and ready to export",
  });
  await expect(approved).toContainText("Reviewed");
  await approved
    .getByRole("button", { name: "Export CSV package", exact: true })
    .click();
  const download = await downloadLatest(page);
  expect(download.suggestedFilename()).toMatch(/^table-reviewed-.*\.zip$/);
  await download.saveAs(info.outputPath("reviewed.zip"));
  expect(
    (await fs.readFile(info.outputPath("reviewed.zip")))
      .subarray(0, 2)
      .toString(),
  ).toBe("PK");

  const latest = (await (
    await page.request.get(`/api/v1/revisions/${view.revision.id}/review`)
  ).json()) as ReviewState;
  expect(latest.current_approval?.label).toBe("reviewed");
});
