import { test, expect } from "@playwright/test";
import {
  cell,
  goToPage,
  openFirstTable,
  openPageTwoTable,
  openReport,
  resetTable,
  reviewProgress,
  tableIdFromUrl,
} from "./helpers";

test("page navigation, the table overview and source stay in sync", async ({
  page,
}) => {
  await openReport(page, "report.pdf");
  await expect(page.getByLabel("Page number")).toHaveValue("1");
  await expect(
    page.getByRole("button", { name: "Previous page" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("heading", { name: "Page 1", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeVisible();

  await goToPage(page, 2);
  await expect(
    page.getByRole("heading", { name: "Page 2", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Next page" })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: /Review table/ }).first(),
  ).toBeVisible();

  await openFirstTable(page);
  await resetTable(page);
  await expect(page.getByLabel("Switch table")).toHaveValue(
    tableIdFromUrl(page),
  );
  await expect(reviewProgress(page)).toContainText("0/12");

  await page.getByRole("button", { name: "Page overview" }).click();
  await expect(page).toHaveURL(/\/pages\/2$/);
  await expect(
    page.getByRole("heading", { name: "Page 2", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeVisible();
});

test("the activity drawer lists extraction work and opens its table", async ({
  page,
}) => {
  await openReport(page, "report.pdf");
  await goToPage(page, 2);
  await page.getByRole("button", { name: /^Activity/ }).click();
  const activity = page.getByRole("dialog", { name: "Activity" });
  await expect(activity).toBeVisible();
  await expect(
    activity.getByRole("heading", { name: "By page" }),
  ).toBeVisible();
  await expect(
    activity.getByRole("heading", { name: "Extraction runs" }),
  ).toBeVisible();
  await expect(activity).toContainText(
    /Synthetic test fixture|Conversion completed/,
  );
  await expect(activity).toContainText("Pages 2");
  await expect(activity).toContainText(/\d+ saved tables?/);

  await activity
    .getByRole("button", { name: /^Open / })
    .first()
    .click();
  await expect(activity).toHaveCount(0);
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
});

test("extraction validates pages and reports its queued work", async ({
  page,
}) => {
  await openReport(page, "report.pdf");
  await page
    .locator(".pane-main")
    .getByRole("button", { name: /^Extract page/ })
    .click();
  const dialog = page.getByRole("dialog", { name: "Extract tables" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Pages").fill("9");
  await dialog.getByRole("button", { name: "Start extraction" }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "Page 9 is outside this report.",
  );

  await dialog.getByLabel("Pages").fill("1");
  await dialog.getByRole("button", { name: "Start extraction" }).click();
  await expect(page.locator(".toast")).toContainText(
    "Extraction queued for page 1.",
  );
  const activity = page.getByRole("dialog", { name: "Activity" });
  await expect(activity).toBeVisible();
  await expect(activity.locator(".activity-group-head").first()).toContainText(
    "Page 1",
  );
  await page.keyboard.press("Escape");
  await expect(activity).toHaveCount(0);
});

test("drawing a source region creates a blank manual table with honest geometry", async ({
  page,
}) => {
  await openReport(page, "report.pdf");
  await goToPage(page, 2);
  await page
    .locator(".source-tools")
    .getByRole("button", { name: "Select region" })
    .click();
  await expect(page.getByRole("button", { name: "Drawing…" })).toBeVisible();

  const image = page.getByTestId("source-image");
  const box = await image.boundingBox();
  if (!box) throw new Error("Original source image unavailable");
  await page.mouse.move(box.x + 60, box.y + 124);
  await page.mouse.down();
  await page.mouse.move(box.x + 480, box.y + 252, { steps: 8 });
  await page.mouse.up();

  await expect(page.locator(".region-help-active")).toContainText(
    "Region selected · page 2",
  );
  await page.getByRole("button", { name: "Manual table", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Create a manual table" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Rows").fill("2");
  await dialog.getByLabel("Columns").fill("3");
  await dialog.getByRole("button", { name: "Create grid" }).click();

  await expect(page).toHaveURL(/\/tables\/[0-9a-f-]+$/);
  const grid = page.getByRole("grid", { name: "Review table" });
  await expect(grid).toHaveAttribute("aria-rowcount", "2");
  await expect(grid).toHaveAttribute("aria-colcount", "3");
  await expect(reviewProgress(page)).toContainText("0/6");
  await expect(
    page.getByText("Exact cell location is unavailable."),
  ).toBeVisible();
});

test("a dirty edit blocks leaving until it is saved or discarded", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const editor = page.locator("textarea[data-cell-text-editor]");
  await editor.fill("1234.51");
  await page.getByRole("button", { name: "Page overview" }).click();
  await expect(page.locator(".toast")).toContainText(
    "Save or discard the open edit before leaving this table.",
  );
  await expect(page).toHaveURL(/\/tables\/[0-9a-f-]+$/);
  await expect(editor).toHaveValue("1234.51");

  await editor.press("Escape");
  await expect(editor).toHaveValue("1,234.50");
  await page.getByRole("button", { name: "Page overview" }).click();
  await expect(page).toHaveURL(/\/pages\/2$/);
});

test("a removed table can be restored into a fresh review", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page.getByRole("tab", { name: "Details" }).click();
  await page.getByRole("button", { name: /Revision details/ }).click();

  await page.getByRole("button", { name: "Remove table" }).click();
  let dialog = page.getByRole("dialog", {
    name: "Remove this table from active work?",
  });
  await expect(dialog).toContainText("Removed tables");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();

  await page.getByRole("button", { name: "Remove table" }).click();
  dialog = page.getByRole("dialog", {
    name: "Remove this table from active work?",
  });
  await dialog.getByRole("button", { name: "Remove table" }).click();
  await expect(page).toHaveURL(/\/pages\/2$/);
  await expect(
    page.getByRole("heading", { name: "Page 2", exact: true }),
  ).toBeVisible();

  await page.getByText("Removed tables (1)").click();
  await page.getByRole("button", { name: "Restore", exact: true }).click();
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  await expect(reviewProgress(page)).toContainText("0/12");
});

test("the mobile workspace switches between source and table without overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openPageTwoTable(page);
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();

  const switcher = page.getByRole("group", { name: "Workspace view" });
  await switcher.getByRole("button", { name: "Source", exact: true }).click();
  await expect(page.getByTestId("source-image")).toBeVisible();
  await switcher.getByRole("button", { name: "Table", exact: true }).click();
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);

  const overview = page.getByRole("button", { name: "Page overview" });
  await expect(overview).toBeVisible();
  await overview.click();
  await expect(page.getByRole("heading", { name: "Page 2" })).toBeVisible();
  await expect(page.getByRole("grid", { name: "Review table" })).toHaveCount(0);
  await openFirstTable(page);
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);

  await cell(page, 1, 1).click();
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(reviewProgress(page)).toContainText("1/12");
  const clear = page.getByRole("button", {
    name: "Clear decision",
    exact: true,
  });
  await expect(clear).toBeVisible();
  await clear.click();
  await expect(reviewProgress(page)).toContainText("0/12");
});
