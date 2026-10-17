import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { openPageTwoTable } from "./helpers";
import { signIn } from "./helpers";

test("failure banner belongs to current page and clears after a newer retry", async ({
  page,
}) => {
  let retried = false;
  await page.route("**/api/v1/documents/*/tasks", async (route) => {
    const base = {
      document_id: "fixture",
      attempt_count: 1,
      cancellation_requested: false,
      result: {},
    };
    await route.fulfill({
      status: 200,
      json: {
        items: [
          {
            ...base,
            id: "older-one",
            kind: "extract",
            input: { pages: [1] },
            state: "failed",
            failure_code: "parse_failure",
            created_at: "2026-10-09T10:00:00Z",
          },
          {
            ...base,
            id: "other-page",
            kind: "extract",
            input: { pages: [2] },
            state: "failed",
            failure_code: "parse_failure",
            created_at: "2026-10-09T10:01:00Z",
          },
          ...(retried
            ? [
                {
                  ...base,
                  id: "retry-one",
                  kind: "extract",
                  input: { pages: [1] },
                  state: "succeeded",
                  failure_code: null,
                  result: {
                    warnings: [
                      "Page 1: OCR omitted 1 table positions; blank cells added for manual image review",
                    ],
                  },
                  created_at: "2026-10-09T10:02:00Z",
                },
              ]
            : []),
        ],
      },
    });
  });
  await signIn(page);
  await page
    .getByRole("button", { name: "Open report.pdf", exact: true })
    .click();
  await expect(
    page.getByText("Extraction failed for page 1.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("Extraction failed for page 2.", { exact: false }),
  ).toHaveCount(0);
  retried = true;
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "report.pdf", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Extraction failed for page 1.", { exact: false }),
  ).toHaveCount(0);
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "OCR omitted 1 table positions" }),
  ).toBeVisible();
});

test("run details load on demand and large differences remain paginated", async ({
  page,
}) => {
  await openPageTwoTable(page);
  let detailsRequests = 0;
  await page.route("**/api/v1/documents/*/runs/*", async (route) => {
    detailsRequests++;
    const response = await route.fetch();
    const run = await response.json();
    // Synthetic display stress fixture; this is not parser-accuracy evidence.
    const changes = Array.from({ length: 600 }, (_, row) => ({
      row,
      col: 0,
      before: { text: `value ${row}`, reference: { precision: "page" } },
      after: { text: `value ${row}`, reference: { precision: "cell" } },
      fields: ["reference"],
    }));
    run.manifest.comparison = {
      status: "compared",
      baseline_run_id: "synthetic-pagination",
      matches: [
        {
          reviewed_diff: {
            equal: false,
            changed_cells: changes,
            table_changes: {},
          },
          original_diff: { equal: true, changed_cells: [], table_changes: {} },
        },
      ],
      ambiguous: [],
      unmatched_before: [],
      unmatched_after: [],
    };
    await route.fulfill({ response, json: run });
  });
  await page
    .getByRole("button", { name: "Extraction history", exact: true })
    .click();
  const history = page.getByRole("region", {
    name: "Extraction runs",
    exact: true,
  });
  await expect(
    history.getByText("View outcome and comparison", { exact: true }).first(),
  ).toBeVisible();
  expect(detailsRequests).toBe(0);
  await history
    .getByText("View outcome and comparison", { exact: true })
    .first()
    .click();
  await expect(
    history.getByText(/^Saved work vs new extraction:/).first(),
  ).toBeVisible();
  await expect(history.locator("tbody tr")).toHaveCount(0);
  await history
    .getByText(/^Saved work vs new extraction:/)
    .first()
    .click();
  await expect(history.locator("tbody tr")).toHaveCount(50);
  await history
    .getByText("Inspect changed fields", { exact: true })
    .first()
    .click();
  const changedFields = history
    .getByRole("region", { name: "Rerun comparison", exact: true })
    .locator("pre")
    .first();
  await expect(changedFields).toContainText('"precision": "page"');
  await expect(changedFields).toContainText('"precision": "cell"');
  for (let index = 0; index < 11; index++)
    await history
      .getByRole("button", { name: "Next changes", exact: true })
      .click();
  await expect(
    history.getByText("Showing 551–600 of 600 changed cells", { exact: true }),
  ).toBeVisible();
  await expect(
    history.getByRole("button", { name: "Next changes", exact: true }),
  ).toBeDisabled();
  await expect(history.locator("tbody tr")).toHaveCount(50);
  await expect(history.getByText("value 599", { exact: true })).toHaveCount(2);
});

test("rerun history compares saved corrections without replacing the selected table", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await openPageTwoTable(page);
  await page
    .getByLabel("Cell text", { exact: true })
    .fill("my saved correction");
  await page.getByLabel("Cell text", { exact: true }).press("Control+Enter");
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  const selected = new URL(page.url()).searchParams.get("table");
  const reportId = new URL(page.url()).searchParams.get("document");
  const initialRuns = (
    await (await page.request.get(`/api/v1/documents/${reportId}/runs`)).json()
  ).items.length;
  await page.getByText("Extract tables", { exact: true }).click();
  await page.getByLabel("Pages to extract").fill("2");
  await page
    .getByRole("button", { name: "Extract selected pages", exact: true })
    .click();
  await page.getByText("Extract tables", { exact: true }).click();
  await page.getByText("Extraction history", { exact: true }).click();
  const history = page.getByRole("region", {
    name: "Extraction runs",
    exact: true,
  });
  await expect(
    history.getByText(`${initialRuns + 1} runs`, { exact: true }),
  ).toBeVisible({
    timeout: 90_000,
  });
  await history
    .getByText("View outcome and comparison", { exact: true })
    .first()
    .click();
  await history
    .getByText(/^Saved work vs new extraction:/)
    .first()
    .click();
  await expect(
    history.getByText("my saved correction", { exact: true }),
  ).toBeVisible();
  await expect(history.getByText("1,234.50", { exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("table")).toBe(selected);
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "my saved correction",
  );
  const downloaded = page.waitForEvent("download");
  await history
    .getByRole("button", { name: "Download run evidence", exact: true })
    .first()
    .click();
  expect((await downloaded).suggestedFilename()).toMatch(
    /^extraction-.*\.json$/,
  );
  await expect(page.getByText(/^Extracting selected pages/)).toHaveCount(0);
  const output = path.resolve("../.cache/extraction-history");
  mkdirSync(output, { recursive: true });
  for (const [name, width, height] of [
    ["desktop", 1440, 900],
    ["phone", 390, 844],
  ] as const) {
    await page.setViewportSize({ width, height });
    await expect(history).toBeVisible();
    await history.evaluate((element) => {
      element.scrollTop = 0;
    });
    if (name === "desktop") {
      await expect(page.getByTestId("source-image")).toBeVisible();
      await expect(page.getByTestId("source-image")).toHaveJSProperty(
        "complete",
        true,
      );
    }
    const bounds = await history.boundingBox();
    expect(bounds?.height).toBeLessThan(height * 0.55);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({ path: path.join(output, `${name}.png`) });
  }
});
