import { test, expect } from "@playwright/test";
import type { Document, Task } from "../src/api/models";
import {
  createManualTable,
  fixtureTask,
  goToPage,
  openPageTwoTable,
  openReport,
  reviewProgress,
  signIn,
  tableIdFromUrl,
} from "./helpers";

async function reportDocument(page: import("@playwright/test").Page) {
  const documents = (await (
    await page.request.get("/api/v1/documents")
  ).json()) as { items: Document[] };
  const report = documents.items.find((item) => item.filename === "report.pdf");
  if (!report) throw new Error("The fixture report is missing");
  return report;
}

test("page input and previous/next controls clamp to the report", async ({
  page,
}) => {
  await openReport(page, "report.pdf");
  const input = page.getByLabel("Page number");

  await input.fill("0");
  await input.press("Enter");
  await expect(page).toHaveURL(/\/pages\/1$/);
  await expect(input).toHaveValue("1");

  await input.fill("99");
  await input.press("Enter");
  await expect(page).toHaveURL(/\/pages\/2$/);
  await expect(input).toHaveValue("2");
  await expect(page.getByRole("button", { name: "Next page" })).toBeDisabled();

  await input.fill("");
  await input.blur();
  await expect(page).toHaveURL(/\/pages\/1$/);
  await expect(input).toHaveValue("1");
  await expect(
    page.getByRole("button", { name: "Previous page" }),
  ).toBeDisabled();
});

test("the table switcher preserves the selected table and review state", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const extractedId = tableIdFromUrl(page);
  const manual = await createManualTable(page, 2, 3, "report.pdf");
  await page.goto(`/reports/${manual.document_id}/pages/2/tables/${manual.id}`);
  const grid = page.getByRole("grid", { name: "Review table" });
  await expect(grid).toHaveAttribute("aria-rowcount", "2");

  const switcher = page.getByLabel("Switch table");
  await expect(switcher).toHaveValue(manual.id);
  await switcher.selectOption(extractedId);
  await expect(page).toHaveURL(new RegExp(`/tables/${extractedId}$`));
  await expect(grid).toHaveAttribute("aria-rowcount", "4");
  await expect(reviewProgress(page)).toContainText("0/12");

  await switcher.selectOption(manual.id);
  await expect(page).toHaveURL(new RegExp(`/tables/${manual.id}$`));
  await expect(grid).toHaveAttribute("aria-rowcount", "2");
  await expect(reviewProgress(page)).toContainText("0/6");
});

test("activity opens a table on its source page, not the current page", async ({
  page,
}) => {
  await openReport(page, "report.pdf");
  await expect(page).toHaveURL(/\/pages\/1$/);
  await page.getByRole("button", { name: /^Activity/ }).click();
  const activity = page.getByRole("dialog", { name: "Activity" });
  await expect(activity).toBeVisible();

  await activity
    .getByRole("button", { name: /^Open table/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/pages\/2\/tables\/[0-9a-f-]+$/);
  await expect(page.getByTestId("source-image")).toHaveAttribute(
    "alt",
    "Original report page 2",
  );
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  await expect(activity).toHaveCount(0);
});

test("a failed extraction offers retry and keeps every earlier attempt", async ({
  page,
}) => {
  await signIn(page);
  const document = await reportDocument(page);
  const now = Date.now();
  const attempts: Task[] = [2, 1, 0].map((offset) =>
    fixtureTask({
      document_id: document.id,
      state: "failed",
      failure_code: "parse_failure",
      attempt_count: 3,
      result: {},
      created_at: new Date(now - offset * 60_000).toISOString(),
    }),
  );
  await page.route("**/api/v1/documents/*/tasks", (route) =>
    route.fulfill({ json: { items: attempts } }),
  );

  await page.goto(`/reports/${document.id}/pages/1`);
  await expect(
    page.getByRole("heading", { name: "Extraction stopped" }),
  ).toBeVisible();
  await expect(page.getByText("The extractor stopped before")).toBeVisible();

  const history = page.locator("details.attempts");
  const summary = history.locator("summary");
  await expect(summary).toContainText("3 attempts, same cause");
  await summary.click();
  await expect(history.locator(".timeline-row")).toHaveCount(3);

  let requested: { pages?: number[] } | null = null;
  await page.route("**/api/v1/documents/*/runs", async (route) => {
    requested = route.request().postDataJSON() as { pages?: number[] };
    await route.fulfill({
      json: fixtureTask({
        document_id: document.id,
        state: "queued",
        result: {},
      }),
    });
  });

  await page.getByRole("button", { name: "Retry page", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Extract tables" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Pages")).toHaveValue("1");
  await dialog.getByRole("button", { name: "Start extraction" }).click();

  await expect(page.locator(".toast")).toContainText(
    "Extraction queued for page 1.",
  );
  await expect(page.getByRole("dialog", { name: "Activity" })).toBeVisible();
  expect(requested).toEqual({ pages: [1], region: null });
});

test("a queued extraction disables a duplicate run and explains the wait", async ({
  page,
}) => {
  await signIn(page);
  const document = await reportDocument(page);
  const queued = fixtureTask({
    document_id: document.id,
    state: "queued",
    result: {},
  });
  await page.route("**/api/v1/documents/*/tasks", (route) =>
    route.fulfill({ json: { items: [queued] } }),
  );

  await page.goto(`/reports/${document.id}/pages/1`);
  await expect(
    page.getByRole("heading", { name: "Extraction queued" }),
  ).toBeVisible();
  const status = page.getByRole("status").filter({
    hasText: "Extraction is queued for this page.",
  });
  await expect(status).toBeVisible();
  const extract = page.locator(".pane-main .action-row.primary-row");
  await expect(extract).toContainText("Extract page");
  await expect(extract).toBeDisabled();

  await status.getByRole("button", { name: "Activity" }).click();
  const activity = page.getByRole("dialog", { name: "Activity" });
  await expect(activity).toContainText("Page 1");
  await expect(activity).toContainText("Queued");
});

test("a manual table needs a source region and valid dimensions", async ({
  page,
}) => {
  await openReport(page, "report.pdf");
  await goToPage(page, 2);
  await page
    .locator(".pane-main")
    .getByRole("button", { name: /^Create a blank table/ })
    .click();
  const dialog = page.getByRole("dialog", { name: "Create a manual table" });
  await expect(dialog).toContainText("Draw a rectangle around the table");
  await dialog.getByRole("button", { name: "Select a region" }).click();
  await expect(page.getByRole("button", { name: "Drawing…" })).toBeVisible();

  const image = page.getByTestId("source-image");
  const box = await image.boundingBox();
  if (!box) throw new Error("Original source image unavailable");
  await page.mouse.move(box.x + 60, box.y + 124);
  await page.mouse.down();
  await page.mouse.move(box.x + 480, box.y + 252, { steps: 8 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Manual table", exact: true }).click();

  const regionDialog = page.getByRole("dialog", {
    name: "Create a manual table",
  });
  await regionDialog.getByLabel("Rows", { exact: true }).fill("0");
  await regionDialog.getByRole("button", { name: "Create grid" }).click();
  await expect(regionDialog.getByRole("alert")).toContainText(
    "Use 1–500 rows and 1–60 columns.",
  );

  await regionDialog.getByLabel("Rows", { exact: true }).fill("2");
  await regionDialog.getByLabel("Columns", { exact: true }).fill("3");
  await regionDialog.getByRole("button", { name: "Create grid" }).click();
  await expect(page).toHaveURL(/\/tables\/[0-9a-f-]+$/);
  const grid = page.getByRole("grid", { name: "Review table" });
  await expect(grid).toHaveAttribute("aria-rowcount", "2");
  await expect(grid).toHaveAttribute("aria-colcount", "3");
  await expect(reviewProgress(page)).toContainText("0/6");
});
