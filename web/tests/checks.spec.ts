import { test, expect, type Page } from "@playwright/test";
import {
  cell,
  gridCell,
  openBlankManualTable,
  openPageTwoTable,
  reviewProgress,
  tableIdFromUrl,
} from "./helpers";

const editor = (page: Page) => page.locator("textarea[data-cell-text-editor]");

test("unreviewed blank cells stay open until the source decision is recorded", async ({
  page,
}) => {
  await openBlankManualTable(page, 3, 2);
  await page.getByRole("tab", { name: /^Checks/ }).click();

  await expect(
    page.getByRole("region", { name: "Source checks" }),
  ).toBeVisible();
  await expect(page.getByTestId("issue-progress")).toHaveText(
    "8 unresolved issues",
  );
  await expect(
    page.getByRole("article", { name: "Unreviewed blank" }),
  ).toHaveCount(6);

  const first = page.getByRole("article", { name: "Unreviewed blank" }).first();
  await first.getByRole("button", { name: /^Inspect/ }).click();
  await expect(
    page.getByRole("tab", { name: "Cell", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(gridCell(page, 0, 0)).toHaveAttribute("aria-selected", "true");
  await page
    .getByRole("button", { name: "Confirm blank", exact: true })
    .click();
  await expect(reviewProgress(page)).toContainText("1/6");

  await page.getByRole("tab", { name: /^Checks/ }).click();
  await expect(
    page.getByRole("article", { name: "Unreviewed blank" }),
  ).toHaveCount(5);

  await page.getByRole("tab", { name: "Cell", exact: true }).click();
  await page
    .getByRole("button", { name: "Next unchecked cell", exact: true })
    .click();
  await expect(editor(page)).toBeFocused();
  await expect(editor(page)).toHaveValue("");
  await expect(page.locator(".grid-footnote").first()).toContainText("1:2");
  await page
    .getByRole("button", { name: "Confirm blank", exact: true })
    .click();
  await expect(reviewProgress(page)).toContainText("2/6");
});

test("a source check decision needs a reason and survives reload until reopened", async ({
  page,
}) => {
  await openBlankManualTable(page, 3, 2);
  await page.getByRole("tab", { name: /^Checks/ }).click();

  const issue = page.getByRole("article", {
    name: "Limited source reference",
    exact: true,
  });
  await expect(issue).toBeVisible();
  await issue.getByRole("button", { name: "Record a decision" }).click();
  await issue
    .getByLabel("Decision reason for Limited source reference")
    .fill("Compared the coarse region with the original page");
  await issue.getByRole("button", { name: "Dismiss issue" }).click();

  await page.getByRole("button", { name: /^All / }).click();
  await expect(issue.getByText("Dismissed", { exact: true })).toBeVisible();
  await expect(issue).toContainText(
    "Compared the coarse region with the original page",
  );
  await expect(page.getByTestId("issue-progress")).toHaveText(
    "7 unresolved issues",
  );

  await page.reload();
  await page.getByRole("tab", { name: /^Checks/ }).click();
  await page.getByRole("button", { name: /^All / }).click();
  const retained = page.getByRole("article", {
    name: "Limited source reference",
    exact: true,
  });
  await expect(retained).toContainText("Dismissed");
  await expect(retained).toContainText(
    "Compared the coarse region with the original page",
  );

  await retained.getByRole("button", { name: "Change decision" }).click();
  await retained
    .getByLabel("Decision reason for Limited source reference")
    .fill("The coarse reference is expected for a manual grid");
  await retained.getByRole("button", { name: "Record resolved" }).click();
  await expect(retained.getByText("Resolved", { exact: true })).toBeVisible();

  await retained.getByRole("button", { name: "Change decision" }).click();
  await retained
    .getByLabel("Decision reason for Limited source reference")
    .fill("Reopened to compare the region again");
  await retained.getByRole("button", { name: "Reopen" }).click();
  await expect(retained.getByText("Open", { exact: true })).toBeVisible();
  await expect(page.getByTestId("issue-progress")).not.toHaveText(
    "No unresolved issues",
  );
});

test("an edit that drops a source literal raises a coverage discrepancy", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await editor(page).fill("123450");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );

  await page.getByRole("tab", { name: /^Checks/ }).click();
  const issue = page
    .getByRole("article", { name: "Coverage discrepancy" })
    .filter({ hasText: "1,234.50" })
    .first();
  await expect(issue).toBeVisible();
  await expect(issue).toContainText("may also be a header or note");
  await issue.getByRole("button", { name: /^Inspect/ }).click();
  await expect(page.getByTestId("source-highlight")).toBeVisible();
  const selected = page.locator('[role="gridcell"][aria-selected="true"]');
  await expect(selected).toHaveCount(1);
  await expect(selected).toHaveClass(/cell-issue-focused/);

  await page.getByRole("tab", { name: /^Checks/ }).click();
  const current = page
    .getByRole("article", { name: "Coverage discrepancy" })
    .filter({ hasText: "1,234.50" })
    .first();
  await current.getByRole("button", { name: "Record a decision" }).click();
  await current
    .getByLabel("Decision reason for Coverage discrepancy")
    .fill("Checked the source literal manually");
  await current.getByRole("button", { name: "Dismiss issue" }).click();

  await page.reload();
  await page.getByRole("tab", { name: /^Checks/ }).click();
  await page.getByRole("button", { name: /^All / }).click();
  const dismissed = page
    .getByRole("article", { name: "Coverage discrepancy" })
    .filter({ hasText: "1,234.50" })
    .first();
  await expect(dismissed).toContainText("Dismissed");
  await expect(dismissed).toContainText("Checked the source literal manually");

  await page.getByRole("tab", { name: "Cell", exact: true }).click();
  await cell(page, 1, 1).click();
  await editor(page).fill("1,234.50");
  await page
    .getByRole("button", { name: "Confirm value", exact: true })
    .click();
  await expect(page.locator(".review-dirty-chip")).toContainText(
    "confirmed against source",
  );
  await page.getByRole("tab", { name: /^Checks/ }).click();
  await expect(
    page.getByRole("region", { name: "Source checks" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^All / }).click();
  await expect(
    page
      .getByRole("article", { name: "Coverage discrepancy" })
      .filter({ hasText: "1,234.50" }),
  ).toHaveCount(0);

  const tableId = tableIdFromUrl(page);
  const view = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as {
    revision: {
      content: { cells: Array<{ row: number; col: number; text: string }> };
    };
  };
  expect(
    view.revision.content.cells.find((item) => item.row === 1 && item.col === 1)
      ?.text,
  ).toBe("1,234.50");
});
