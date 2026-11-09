import { test, expect, type Page } from "@playwright/test";
import type { TableView } from "../src/api/models";
import {
  cell,
  expectSaved,
  openPageTwoTable,
  reviewProgress,
  tableIdFromUrl,
} from "./helpers";

function tableView(page: Page) {
  return page.request
    .get(`/api/v1/tables/${tableIdFromUrl(page)}`)
    .then((response) => response.json() as Promise<TableView>);
}

async function openMoreSection(page: Page, name: RegExp) {
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  const section = page.getByRole("button", { name });
  // A section stays mounted across tab switches; only toggle it when closed.
  if ((await section.getAttribute("aria-expanded")) !== "true")
    await section.click();
}

async function setHeaderRoles(page: Page) {
  const current = await tableView(page);
  await openMoreSection(page, /^Table structure/);
  // The contract fixture already declares row 1 and column 1 as headers.
  if (
    current.revision.content.header_rows.includes(0) &&
    current.revision.content.row_label_columns.includes(0)
  )
    return;
  await page.getByLabel("Header rows").fill("1");
  await page.getByLabel("Row label columns").fill("1");
  await page.getByRole("button", { name: "Save header roles" }).click();
  await expectSaved(page);
}

async function createSubtotal(page: Page) {
  await setHeaderRoles(page);
  await openMoreSection(page, /^Source context/);
  for (const label of ["reporting period", "currency", "scale wording"]) {
    await cell(page, 1, 1).click();
    await cell(page, 3, 1).click({ modifiers: ["Shift"] });
    await page
      .getByRole("button", {
        name: `Confirm ${label} for selection`,
        exact: true,
      })
      .click();
    await expectSaved(page);
  }

  await page.getByRole("button", { name: /^Declared subtotals/ }).click();
  await page
    .getByRole("button", { name: "Use current cell", exact: true })
    .click();
  await cell(page, 1, 1).click();
  await cell(page, 2, 1).click({ modifiers: ["Shift"] });
  await page
    .getByRole("button", { name: "Add selection", exact: true })
    .click();
  await page.getByLabel("Subtotal name").fill("Revenue plus signed expense");
  await page
    .getByRole("checkbox", {
      name: "I checked the source scope and the selected relation",
    })
    .check();
  await page
    .getByRole("button", { name: "Confirm subtotal", exact: true })
    .click();
  await expectSaved(page);
}

test("reviewing a saved subtotal does not mark the form dirty or block leaving", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await createSubtotal(page);

  const results = page.locator(".subtotal-results");
  await expect(results).toContainText("Within tolerance");
  const result = page.locator(".subtotal-result").first();
  await result.getByRole("button", { name: "Review relation" }).click();

  await expect(
    result.getByRole("button", { name: "Remove", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".review-dirty-chip")).not.toContainText(
    "Unsaved subtotal setup",
  );

  await page.getByRole("button", { name: "Page overview" }).click();
  await expect(page).toHaveURL(/\/pages\/2$/);
  await expect(
    page.getByRole("heading", { name: "Page 2", exact: true }),
  ).toBeVisible();
});

test("a typed structure draft survives closing the section and switching tabs", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await openMoreSection(page, /^Table structure/);
  const headers = page.getByLabel("Header rows");
  await headers.fill("2");

  await page.getByRole("button", { name: /^Table structure/ }).click();
  await expect(headers).toBeHidden();
  await page.getByRole("button", { name: /^Table structure/ }).click();
  await expect(headers).toHaveValue("2");

  await page.getByRole("tab", { name: "Cell", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /^Table structure/ }),
  ).toBeHidden();
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await expect(headers).toHaveValue("2");
  await expect(
    page.getByRole("button", { name: "Save header roles" }),
  ).toBeEnabled();
  await expect(page.locator(".review-dirty-chip")).toHaveCount(0);
});

test("a wording choice does not carry from one cell to another", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const view = await tableView(page);
  const first = view.revision.content.cells.find(
    (item) => item.row === 0 && item.col === 0,
  );
  const second = view.revision.content.cells.find(
    (item) => item.row === 0 && item.col === 1,
  );
  if (!first || !second) throw new Error("Context fixture cells are missing");

  await page.route(/\/context\/[^/?]+$/, async (route) => {
    const cellId = new URL(route.request().url()).pathname.split("/").pop();
    const isFirst = cellId === first.id;
    await route.fulfill({
      json: {
        cell_id: cellId,
        column_headers: [],
        row_headers: [],
        sources: [],
        confirmed: [],
        warnings: [],
        proposals: isFirst
          ? [
              {
                id: "currency-usd",
                kind: "currency",
                value: "USD",
                source_ids: [],
              },
              {
                id: "currency-eur",
                kind: "currency",
                value: "EUR",
                source_ids: [],
              },
            ]
          : [
              {
                id: "currency-gbp",
                kind: "currency",
                value: "GBP",
                source_ids: [],
              },
            ],
      },
    });
  });

  await cell(page, 0, 0).click();
  await openMoreSection(page, /^Source context/);
  const wording = page.getByLabel("Source wording for currency");
  await expect(wording).toHaveValue("currency-usd");
  await wording.selectOption("currency-eur");

  await cell(page, 0, 1).click();
  await expect(wording).toBeVisible();
  await expect(wording).toHaveValue("currency-gbp");
});

test("reopening history after an edit shows the new revision", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const tableId = tableIdFromUrl(page);
  const before = (await (
    await page.request.get(`/api/v1/tables/${tableId}/history`)
  ).json()) as { items: unknown[] };
  await openMoreSection(page, /^History and restore/);
  await page.getByRole("button", { name: /^History and restore/ }).click();

  await page.getByRole("tab", { name: "Cell", exact: true }).click();
  await page.locator("textarea[data-cell-text-editor]").fill("1,234.51");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );
  await expect(reviewProgress(page)).toContainText("1/12");

  // With a stale cache the panel never asks the server after the edit. Wait
  // for the actual history response so this covers the refetch, not just DOM.
  const refreshed = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().endsWith(`/api/v1/tables/${tableId}/history`),
  );
  await openMoreSection(page, /^History and restore/);
  const after = (await (await refreshed).json()) as { items: unknown[] };
  expect(after.items.length).toBe(before.items.length + 1);
  await expect(page.locator(".timeline-list")).toContainText("Revision saved");
  const edit = page
    .locator(".revision-list li")
    .filter({ hasText: "set text" })
    .first();
  await expect(edit).toBeVisible();
  await expect(edit).toContainText("1 affected cells");
});
