import { test, expect, type Page } from "@playwright/test";
import type { ReviewState, TableView } from "../src/api/models";
import {
  cell,
  createManualTable,
  expectSaved,
  openBlankManualTable,
  openPageTwoTable,
  reviewProgress,
  tableIdFromUrl,
} from "./helpers";

const editor = (page: Page) => page.locator("textarea[data-cell-text-editor]");

test("keyboard movement, range selection and Escape work in the grid", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const footnote = page.locator(".grid-footnote").first();
  await expect(footnote).toContainText("2:2");

  await cell(page, 1, 1).focus();
  await page.keyboard.press("ArrowRight");
  await expect(cell(page, 1, 2)).toBeFocused();
  await page.keyboard.press("Shift+ArrowLeft");
  await expect(footnote).toContainText("2 selected");

  await page.keyboard.press("Enter");
  await expect(editor(page)).toBeFocused();
  await expect(editor(page)).toHaveValue("1,234.50");
  await editor(page).fill("discard this");
  await page.keyboard.press("Escape");
  await expect(editor(page)).toHaveValue("1,234.50");
  await expect(page.locator(".review-dirty-chip")).toHaveCount(0);

  await page.getByRole("button", { name: "Keyboard shortcuts" }).click();
  const shortcuts = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(shortcuts).toContainText(
    "Confirm the value — saves the edit first",
  );
  await expect(shortcuts).toContainText("Next unchecked cell");
  await page.keyboard.press("Escape");
  await expect(shortcuts).toHaveCount(0);
});

test("a saved interpretation change creates a new revision and reopens review evidence", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const tableId = tableIdFromUrl(page);
  const before = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  const beforeCell = before.revision.content.cells.find(
    (item) => item.row === 1 && item.col === 1,
  );
  expect(beforeCell?.text).toBe("1,234.50");

  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(reviewProgress(page)).toContainText("1/12");

  const interpretation = page.getByLabel("Interpretation");
  const current = await interpretation.inputValue();
  const next = current === "uncertain" ? "text" : "uncertain";
  await interpretation.selectOption(next);
  await editor(page).fill("1,234.50 ");
  await expect(
    page.getByRole("button", { name: "Save type", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".rail-hint")).toContainText("Save or discard");
  await page.keyboard.press("Escape");
  await expect(editor(page)).toHaveValue("1,234.50");
  await expect(
    page.getByRole("button", { name: "Save type", exact: true }),
  ).toBeDisabled();
  await interpretation.selectOption(next);
  await expect(
    page.getByRole("button", { name: "Save type", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Save type", exact: true }).click();
  await expectSaved(page);
  await expect(page.locator(".review-alerts")).toContainText(
    "This edit changed the review evidence",
  );
  await expect(reviewProgress(page)).toContainText("0/12");
  await expect(cell(page, 1, 1)).toHaveAttribute(
    "aria-label",
    /1,234\.50, unreviewed/,
  );

  const after = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  const afterCell = after.revision.content.cells.find(
    (item) => item.row === 1 && item.col === 1,
  );
  expect(afterCell?.text).toBe("1,234.50");
  expect(afterCell?.interpretation).toBe(next);
  expect(after.revision.parent_revision_id).toBe(before.revision.id);
  expect(beforeCell?.text).toBe("1,234.50");
});

test("Ctrl+Enter confirms a changed value and N advances to the next unchecked cell", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const tableId = tableIdFromUrl(page);
  const before = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  const beforeCell = before.revision.content.cells.find(
    (item) => item.row === 1 && item.col === 1,
  );
  expect(beforeCell?.text).toBe("1,234.50");

  await editor(page).fill("1,235.00");
  await page.keyboard.press("Control+Enter");
  await expect(reviewProgress(page)).toContainText("1/12");
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );

  const after = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  const afterCell = after.revision.content.cells.find(
    (item) => item.row === 1 && item.col === 1,
  );
  expect(afterCell?.text).toBe("1,235.00");
  expect(after.revision.parent_revision_id).toBe(before.revision.id);

  await cell(page, 1, 1).click();
  await page.keyboard.press("n");
  await expect(page.locator(".verdict .cell-badge")).toHaveText("C2");
  await expect(editor(page)).toHaveValue("1,100.00");
  await expect(page.getByRole("button", { name: "Save value" })).toHaveCount(0);
});

test("a saved value can retry when recording its confirmation fails", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const tableId = tableIdFromUrl(page);
  await page.route("**/api/v1/revisions/*/cell-review", (route) =>
    route.abort("connectionfailed"),
  );
  await editor(page).fill("555.00");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-alerts")).toContainText(
    "Decision not recorded",
  );
  await expect(page.locator(".review-alerts")).toContainText("value was saved");
  await expect(page.locator(".review-dirty-chip")).toContainText("Saved");
  await expect(reviewProgress(page)).toContainText("0/12");

  const saved = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  expect(
    saved.revision.content.cells.find(
      (item) => item.row === 1 && item.col === 1,
    )?.text,
  ).toBe("555.00");

  await page.unrouteAll();
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );
  await expect(reviewProgress(page)).toContainText("1/12");
});

test("a failed save keeps the unsaved draft", async ({ page }) => {
  await openPageTwoTable(page);
  const tableId = tableIdFromUrl(page);
  await page.route(`**/api/v1/tables/${tableId}/edits`, (route) =>
    route.abort("connectionfailed"),
  );
  await editor(page).fill("keep this draft");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-alerts")).toContainText(
    "Change not saved",
  );
  await expect(editor(page)).toHaveValue("keep this draft");
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "Unsaved changes",
  );

  await page.unrouteAll();
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );
  const latest = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  expect(
    latest.revision.content.cells.find(
      (item) => item.row === 1 && item.col === 1,
    )?.text,
  ).toBe("keep this draft");
});

test("blank and unreadable decisions stay explicit, and an edit reopens them", async ({
  page,
}) => {
  await openBlankManualTable(page, 3, 2);
  await page
    .getByRole("button", { name: "Confirm blank", exact: true })
    .click();
  await expect(reviewProgress(page)).toContainText("1/6");
  await expect(cell(page, 0, 0)).toHaveAttribute(
    "aria-label",
    /confirmed blank/,
  );

  await cell(page, 1, 0).click();
  await page.getByRole("button", { name: "Unreadable" }).click();
  await page
    .getByLabel("Why is the source unreadable?")
    .fill("Source digits are blurred");
  await page
    .getByRole("button", { name: "Record unreadable", exact: true })
    .click();
  await expect(cell(page, 1, 0)).toHaveAttribute("aria-label", /unreadable/);
  await expect(reviewProgress(page)).toContainText("2/6");
  await expect(page.locator(".review-unreadable")).toContainText(
    "1 unreadable",
  );

  await page.getByRole("tab", { name: /^Checks/ }).click();
  await expect(
    page.getByRole("article", { name: "Unreadable value", exact: true }),
  ).toContainText("Source digits are blurred");

  await page.getByRole("tab", { name: "Cell", exact: true }).click();
  await editor(page).fill("55");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );
  await expect(reviewProgress(page)).toContainText("2/6");
  await expect(cell(page, 0, 0)).toHaveAttribute(
    "aria-label",
    /confirmed blank/,
  );
  await expect(page.getByRole("tab", { name: /^Checks/ })).not.toContainText(
    "unreadable",
  );

  const tableId = tableIdFromUrl(page);
  const view = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  const review = (await (
    await page.request.get(`/api/v1/revisions/${view.revision.id}/review`)
  ).json()) as ReviewState;
  expect(review.unreadable_count).toBe(0);
});

test("an unreadable rationale does not carry to another cell", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page.getByRole("button", { name: "Unreadable" }).click();
  await page
    .getByLabel("Why is the source unreadable?")
    .fill("Source digits are blurred");
  await cell(page, 2, 1).click();
  await expect(page.getByLabel("Why is the source unreadable?")).toHaveCount(0);

  await page.getByRole("button", { name: "Unreadable" }).click();
  await expect(page.getByLabel("Why is the source unreadable?")).toHaveValue(
    "",
  );
  await expect(
    page.getByRole("button", { name: "Record unreadable", exact: true }),
  ).toBeDisabled();
});

test("a mixed selection cannot confirm values or blanks", async ({ page }) => {
  await openBlankManualTable(page, 3, 2);
  await editor(page).fill("42");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );

  await cell(page, 0, 0).click();
  await cell(page, 0, 1).click({ modifiers: ["Shift"] });
  await expect(page.locator(".verdict-scope")).toContainText("2 selected");
  await expect(
    page.getByRole("button", { name: "Confirm selection", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", {
      name: "Confirm blanks for selection",
      exact: true,
    }),
  ).toBeDisabled();
});

test("a cleared draft cannot be confirmed or saved by the keyboard shortcut", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await editor(page).fill("");
  await expect(
    page.getByRole("button", { name: "Confirm value", exact: true }),
  ).toBeDisabled();
  await page.keyboard.press("Control+Enter");
  await expect(page.locator(".review-alerts")).toContainText(
    "Every selected cell needs text",
  );
  await expect(reviewProgress(page)).toContainText("0/12");

  await page.keyboard.press("Escape");
  await cell(page, 1, 2).click({ modifiers: ["Shift"] });
  await expect(page.locator(".verdict-scope")).toContainText("2 selected");
  await editor(page).fill("");
  await expect(
    page.getByRole("button", { name: "Confirm selection", exact: true }),
  ).toBeDisabled();
  await page.keyboard.press("Control+Enter");
  await expect(page.locator(".review-alerts")).toContainText(
    "Every selected cell needs text",
  );

  const tableId = tableIdFromUrl(page);
  const view = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  expect(
    view.revision.content.cells.find((item) => item.row === 1 && item.col === 1)
      ?.text,
  ).toBe("1,234.50");
  expect(
    view.revision.content.cells.find((item) => item.row === 1 && item.col === 2)
      ?.text,
  ).toBe("1,100.00");
});

test("keyboard range confirmation and clearing applies to every selected cell", async ({
  page,
}) => {
  await openBlankManualTable(page, 2, 3);
  await cell(page, 0, 0).click();
  await page.keyboard.press("Shift+ArrowDown");
  await page.keyboard.press("Shift+ArrowDown");
  await expect(page.locator(".verdict-scope")).toContainText("3 selected");

  await page
    .getByRole("button", {
      name: "Confirm blanks for selection",
      exact: true,
    })
    .click();
  await expect(reviewProgress(page)).toContainText("3/6");

  await page
    .getByRole("button", { name: "Clear decision", exact: true })
    .click();
  await expect(reviewProgress(page)).toContainText("0/6");
});

test("a conflicting draft in a second tab survives until an explicit reload", async ({
  page,
  context,
}) => {
  await openPageTwoTable(page);
  const second = await context.newPage();
  await second.goto(page.url());
  await expect(
    second.getByRole("grid", { name: "Review table" }),
  ).toBeVisible();

  await cell(page, 1, 1).click();
  await cell(second, 1, 1).click();
  await editor(second).fill("second draft");
  await editor(page).fill("first save");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );

  await second
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(second.locator(".review-alerts")).toContainText(
    "changed in another tab",
  );
  await expect(editor(second)).toHaveValue("second draft");

  await second
    .getByRole("button", { name: "Load latest revision", exact: true })
    .click();
  await expect(second.locator(".review-dirty-chip")).toContainText(
    "Latest revision loaded",
  );
  await expect(editor(second)).toHaveValue("second draft");
  await second
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(second.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );

  const tableId = tableIdFromUrl(page);
  const latest = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  expect(
    latest.revision.content.cells.find(
      (item) => item.row === 1 && item.col === 1,
    )?.text,
  ).toBe("second draft");
  await second.close();
});

test("large grids virtualize while keeping the final cell reachable", async ({
  page,
}) => {
  test.setTimeout(150_000);
  const created = await createManualTable(page, 500, 20);
  await page.goto(
    `/reports/${created.document_id}/pages/2/tables/${created.id}`,
  );
  const grid = page.getByRole("grid", { name: "Review table" });
  await expect(grid).toHaveAttribute("aria-rowcount", "500");
  await expect(grid).toHaveAttribute("aria-colcount", "20");
  expect(await page.getByRole("gridcell").count()).toBeLessThan(1000);

  await cell(page, 0, 0).focus();
  await page.keyboard.press("Control+End");
  await expect(cell(page, 499, 19)).toBeFocused();
  await editor(page).fill("last cell");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );

  const latest = (await (
    await page.request.get(`/api/v1/tables/${created.id}`)
  ).json()) as TableView;
  expect(
    latest.revision.content.cells.find(
      (item) => item.row === 499 && item.col === 19,
    )?.text,
  ).toBe("last cell");
});
