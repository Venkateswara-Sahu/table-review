import { test, expect, type Page } from "@playwright/test";
import type { TableView } from "../src/api/models";
import {
  cell,
  expectSaved,
  openPageTwoTable,
  reviewProgress,
  tableIdFromUrl,
} from "./helpers";

function view(page: Page) {
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

/** The synthetic report has no detected header row; mark row 1 and column 1. */
async function setHeaderRoles(page: Page) {
  const current = await view(page);
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

test("source context is confirmed, hovers its evidence and reopens after reload", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await setHeaderRoles(page);
  await openMoreSection(page, /^Source context/);
  await expect(
    page.getByRole("heading", { name: "Header path" }),
  ).toBeVisible();

  const period = page
    .locator("fieldset.context-field")
    .filter({ hasText: "Reporting period" });
  await expect(period).toContainText("Unconfirmed");
  await period
    .getByRole("button", { name: "Confirm reporting period", exact: true })
    .click();
  await expectSaved(page);
  await expect(period).toContainText("Confirmed:");

  await period.getByRole("button", { name: /^Source 1 · page 2$/ }).click();
  await expect(page.getByTestId("source-highlight")).toHaveAttribute(
    "data-page",
    "2",
  );

  await page.reload();
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  await cell(page, 1, 1).click();
  await openMoreSection(page, /^Source context/);
  const retained = page
    .locator("fieldset.context-field")
    .filter({ hasText: "Reporting period" });
  await expect(retained).toContainText("Confirmed:");
  await retained.getByRole("button", { name: "Reopen", exact: true }).click();
  await expectSaved(page);
  await expect(retained).toContainText("Unconfirmed");
});

test("a failed source context read can be retried without losing the panel", async ({
  page,
}) => {
  let attempts = 0;
  await page.route(
    /\/api\/v1\/tables\/[^/]+\/revisions\/[^/]+\/context\/[^/?]+/,
    async (route) => {
      attempts++;
      if (attempts <= 2) {
        await route.fulfill({
          status: 500,
          json: {
            code: "context_failed",
            message: "Source context is unavailable",
          },
        });
      } else await route.continue();
    },
  );
  await openPageTwoTable(page);
  await openMoreSection(page, /^Source context/);
  await expect(page.getByText("Context could not be loaded")).toBeVisible();
  await expect(page.getByText("Source context is unavailable")).toBeVisible();
  await page
    .getByRole("button", { name: "Retry source context", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Header path" }),
  ).toBeVisible();
  expect(attempts).toBeGreaterThanOrEqual(3);
});

test("a declared subtotal matches, then needs confirmation after its total changes", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await openPageTwoTable(page);
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

  const results = page.locator(".subtotal-results");
  await expect(results).toContainText("Within tolerance");
  await expect(results).toContainText(/1,111\.05|1111\.05/);

  await page.getByRole("tab", { name: "Cell", exact: true }).click();
  await cell(page, 3, 1).click();
  await page.locator("textarea[data-cell-text-editor]").fill("1112.05");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );
  await openMoreSection(page, /^Declared subtotals/);
  await expect(results).toContainText("Unavailable");
  await expect(results).toContainText("Confirm current source context");
  await expect(results).not.toContainText("Within tolerance");

  await openMoreSection(page, /^Source context/);
  await cell(page, 3, 1).click();
  for (const label of ["reporting period", "currency", "scale wording"]) {
    await page
      .getByRole("button", {
        name: `Confirm ${label}`,
        exact: true,
      })
      .click();
    await expectSaved(page);
  }
  await openMoreSection(page, /^Declared subtotals/);
  await expect(results).toContainText("Confirm again");
  await expect(results).not.toContainText("Within tolerance");

  await page.getByRole("button", { name: "Review relation" }).click();
  await page
    .getByRole("checkbox", {
      name: "I checked the source scope and the selected relation",
    })
    .check();
  await page
    .getByRole("button", { name: "Confirm subtotal", exact: true })
    .click();
  await expectSaved(page);
  await expect(results).toContainText("Differs");
});

test("history explains the edit and restores the original as a fresh draft", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(reviewProgress(page)).toContainText("1/12");
  await page.locator("textarea[data-cell-text-editor]").fill("1,234.51");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );

  await openMoreSection(page, /^History and restore/);
  const timeline = page.locator(".timeline-list");
  await expect(timeline).toContainText("Revision saved");
  await expect(timeline).toContainText("Cell review changed");

  const revisions = page.locator(".revision-list");
  await expect(revisions).toContainText("set text");
  await revisions
    .getByRole("button", { name: "Inspect", exact: true })
    .first()
    .click();
  const preview = page.getByRole("dialog", { name: /Historical revision/ });
  await expect(preview).toContainText("1,234.51");
  await preview
    .getByRole("button", { name: "Close", exact: true })
    .first()
    .click();

  await page
    .getByRole("button", { name: "Restore original as draft", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "Restored as a new draft",
  );
  await expect(reviewProgress(page)).toContainText("0/12");
  await page.getByRole("tab", { name: "Cell", exact: true }).click();
  await cell(page, 1, 1).click();
  await expect(page.locator("textarea[data-cell-text-editor]")).toHaveValue(
    "1,234.50",
  );
});

test("structure edits update the grid, spans and persisted evidence", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await openPageTwoTable(page);
  await openMoreSection(page, /^Table structure/);
  const grid = page.getByRole("grid", { name: "Review table" });

  await expect(grid).toHaveAttribute("aria-rowcount", "4");
  await page.getByRole("button", { name: "Insert row after" }).click();
  await expect(grid).toHaveAttribute("aria-rowcount", "5");
  await page.getByRole("button", { name: "Insert column after" }).click();
  await expect(grid).toHaveAttribute("aria-colcount", "4");
  await page.getByRole("button", { name: "Delete row" }).click();
  await expect(grid).toHaveAttribute("aria-rowcount", "4");
  await page.getByRole("button", { name: "Delete column" }).click();
  await expect(grid).toHaveAttribute("aria-colcount", "3");

  await cell(page, 1, 1).click();
  await cell(page, 2, 1).click({ modifiers: ["Shift"] });
  await page
    .getByRole("button", { name: "Merge 2×1 selection", exact: true })
    .click();
  const merged = page.locator(
    '.review-grid [role="gridcell"][aria-rowindex="2"][aria-colindex="2"]',
  );
  await expect(merged).toHaveAttribute("aria-rowspan", "2");
  await page.getByRole("button", { name: "Split cell", exact: true }).click();
  await expect(merged).toHaveAttribute("aria-rowspan", "1");
  await expect(cell(page, 2, 1)).toBeVisible();

  await page.getByLabel("Header rows").fill("1, 2");
  await page.getByRole("button", { name: "Save header roles" }).click();
  await expectSaved(page);
  await expect
    .poll(async () => (await view(page)).revision.content.header_rows)
    .toEqual([0, 1]);

  await page
    .getByLabel("Raw unit notes")
    .fill("USD millions; reviewed against the source.");
  await page.getByRole("button", { name: "Save unit notes" }).click();
  await expectSaved(page);
  await expect
    .poll(async () => (await view(page)).revision.content.unit_notes)
    .toEqual(["USD millions; reviewed against the source."]);
});
