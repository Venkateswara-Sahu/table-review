import { test, expect } from "@playwright/test";
import type { Task } from "../src/api/models";
import { openReport, reportRow, signIn } from "./helpers";

const region = {
  page: 1,
  left: 60,
  top: 120,
  right: 480,
  bottom: 260,
  page_width: 612,
  page_height: 792,
};

function task(overrides: Partial<Task>): Task {
  return {
    id: "fixture-task",
    document_id: "fixture-document",
    kind: "extract",
    input: { pages: [1] },
    state: "succeeded",
    attempt_count: 1,
    cancellation_requested: false,
    failure_code: null,
    result: { conversion_outcome: "success" },
    created_at: "2026-10-17T10:00:00Z",
    ...overrides,
  };
}

for (const scenario of [
  {
    name: "not requested",
    items: [] as Task[],
    heading: "No tables on this page yet",
  },
  {
    name: "queued",
    items: [task({ state: "queued", result: {} })],
    heading: "Extraction queued",
  },
  {
    name: "running",
    items: [task({ state: "running", result: {} })],
    heading: "Extracting this page",
  },
  {
    name: "failed",
    items: [
      task({ state: "failed", result: {}, failure_code: "parse_failure" }),
    ],
    heading: "Extraction stopped",
  },
  {
    name: "complete without tables",
    items: [task({})],
    heading: "No tables found on this page",
  },
  {
    name: "complete without tables in a region",
    items: [task({ input: { pages: [1], region } })],
    heading: "No tables found in the selected region",
  },
  {
    name: "partial conversion",
    items: [task({ result: { conversion_outcome: "partial_success" } })],
    heading: "Extraction needs another look",
  },
]) {
  test(`the empty table state distinguishes ${scenario.name}`, async ({
    page,
  }) => {
    await page.route("**/api/v1/documents/*/tasks", (route) =>
      route.fulfill({ json: { items: scenario.items } }),
    );
    await openReport(page, "report.pdf");
    await expect(
      page.getByRole("heading", { name: scenario.heading, exact: true }),
    ).toBeVisible();
  });
}

test("native parser gaps are surfaced without inventing values", async ({
  page,
}) => {
  await page.route("**/api/v1/documents/*/tasks", (route) =>
    route.fulfill({
      json: {
        items: [
          task({
            result: {
              conversion_outcome: "success",
              warnings: [
                "Page 1: Parser omitted 2 table positions; blank cells added for manual image review",
              ],
            },
          }),
        ],
      },
    }),
  );
  await openReport(page, "report.pdf");
  await expect(page.locator(".page-notes")).toContainText(
    "Parser omitted 2 table positions",
  );
});

test("mixed text and image guidance leads to an explicit OCR region request", async ({
  page,
}) => {
  await page.route(/\/api\/v1\/documents\/[^/]+\/pages\/1$/, async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: { ...(await response.json()), mixed_image_content: true },
    });
  });
  await signIn(page);
  await reportRow(page, "report.pdf")
    .getByRole("button", { name: "Open", exact: true })
    .click();
  await expect(page.locator(".page-notes")).toContainText(
    "This page mixes PDF text and images.",
  );

  await page
    .getByRole("region", { name: "Report source" })
    .getByRole("button", { name: "Select region", exact: true })
    .click();
  const source = page.getByTestId("source-image");
  await expect(source).toBeVisible();
  const box = await source.boundingBox();
  if (!box) throw new Error("Original source image is unavailable");
  await page.mouse.move(box.x + 35, box.y + 100);
  await page.mouse.down();
  await page.mouse.move(box.x + 220, box.y + 230, { steps: 5 });
  await page.mouse.up();
  await expect(page.locator(".region-help-active")).toContainText(
    "Region selected · page 1",
  );
  await page
    .getByRole("button", { name: "Extract region", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Extract selected region",
  });
  await expect(dialog).toContainText("OCR reads the drawn area as a table.");

  const requested = page.waitForRequest(
    (request) => request.method() === "POST" && /\/runs$/.test(request.url()),
  );
  await dialog.getByRole("button", { name: "Start extraction" }).click();
  const payload = (await requested).postDataJSON() as {
    pages: number[];
    region: {
      page: number;
      left: number;
      right: number;
      top: number;
      bottom: number;
    };
  };
  expect(payload.pages).toEqual([1]);
  expect(payload.region.page).toBe(1);
  expect(payload.region.right).toBeGreaterThan(payload.region.left);
  expect(payload.region.bottom).toBeGreaterThan(payload.region.top);

  await page.keyboard.press("Escape");
  await expect(source).toBeVisible();
});
