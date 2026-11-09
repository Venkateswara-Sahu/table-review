import { test, expect, type Page } from "@playwright/test";
import type { ReviewState, Run, TableView } from "../src/api/models";
import { cell, openPageTwoTable, tableIdFromUrl } from "./helpers";

function documentIdFromUrl(page: Page) {
  const parts = new URL(page.url()).pathname.split("/").filter(Boolean);
  return parts[1];
}

async function rerunAndCompare(page: Page) {
  await openPageTwoTable(page);
  await page
    .locator("textarea[data-cell-text-editor]")
    .fill("retained earlier correction");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );

  const documentId = documentIdFromUrl(page);
  const tableId = tableIdFromUrl(page);
  const before = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;

  await page.getByRole("button", { name: "Page overview" }).click();
  await page
    .locator(".pane-main")
    .getByRole("button", { name: /^Extract page/ })
    .click();
  const dialog = page.getByRole("dialog", { name: "Extract tables" });
  await dialog.getByRole("button", { name: "Start extraction" }).click();
  const activity = page.getByRole("dialog", { name: "Activity" });
  await expect(activity).toBeVisible();

  await expect
    .poll(
      async () => {
        const runs = (await (
          await page.request.get(
            `/api/v1/documents/${documentId}/runs?limit=20`,
          )
        ).json()) as { items: Run[] };
        const comparison = runs.items[0]?.manifest?.comparison as
          { matches?: unknown[] } | undefined;
        return comparison?.matches?.length ?? 0;
      },
      { timeout: 60_000 },
    )
    .toBeGreaterThan(0);

  await activity.getByRole("button", { name: "Refresh", exact: true }).click();
  const compare = activity
    .getByRole("button", { name: "Compare replacement", exact: true })
    .first();
  await expect(compare).toBeVisible();
  await compare.click();
  const panel = activity.getByRole("region", {
    name: "Adopt extraction candidate",
  });
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("retained earlier correction");
  await expect(panel).toContainText("1,234.50");
  return { activity, panel, documentId, tableId, before };
}

test("a rerun compares by position and adopts only the candidate the reviewer confirms", async ({
  page,
}) => {
  test.setTimeout(150_000);
  const { activity, panel, documentId, tableId, before } =
    await rerunAndCompare(page);

  await expect(
    panel.getByRole("button", { name: "Adopt as new draft", exact: true }),
  ).toBeDisabled();
  await panel
    .getByRole("button", { name: "Keep current", exact: true })
    .click();
  await expect(panel).toHaveCount(0);

  await page.goto(`/reports/${documentId}/pages/2/tables/${tableId}`);
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  await cell(page, 1, 1).click();
  await expect(page.locator("textarea[data-cell-text-editor]")).toHaveValue(
    "retained earlier correction",
  );

  await page.getByRole("button", { name: /^Activity/ }).click();
  await activity
    .getByRole("button", { name: "Compare replacement", exact: true })
    .first()
    .click();
  const confirmed = activity.getByRole("region", {
    name: "Adopt extraction candidate",
  });
  await confirmed
    .getByRole("checkbox", {
      name: "I compared the source areas and these represent the same table",
    })
    .check();
  await confirmed
    .getByRole("checkbox", {
      name: "Use the complete candidate; keep my old corrections only in History",
    })
    .check();
  await expect(
    confirmed.getByRole("button", {
      name: "Adopt as new draft",
      exact: true,
    }),
  ).toBeEnabled();
  await confirmed
    .getByRole("button", { name: "Adopt as new draft", exact: true })
    .click();

  await expect(activity).toHaveCount(0);
  await expect(page.locator("textarea[data-cell-text-editor]")).toHaveValue(
    "1,234.50",
  );
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();

  const adopted = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  expect(adopted.revision.parent_revision_id).toBe(before.revision.id);
  const old = (await (
    await page.request.get(
      `/api/v1/tables/${tableId}/revisions/${before.revision.id}`,
    )
  ).json()) as TableView["revision"];
  expect(
    old.content.cells.some(
      (cell) => cell.text === "retained earlier correction",
    ),
  ).toBe(true);

  const review = (await (
    await page.request.get(`/api/v1/revisions/${adopted.revision.id}/review`)
  ).json()) as ReviewState;
  expect(review.checked_cell_ids).toHaveLength(0);
  expect(review.current_approval).toBeNull();
});
