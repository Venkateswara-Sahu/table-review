import { test, expect } from "@playwright/test";
import { openPageTwoTable } from "./helpers";

test("a real missing source token has an evidence reason and a saved dismissal", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page.getByLabel("Cell text", { exact: true }).fill("123450");
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /^Checks/ }).click();
  const issue = page
    .getByRole("region", { name: "Coverage discrepancy" })
    .filter({ hasText: "1,234.50" })
    .first();
  await expect(issue).toBeVisible();
  await expect(issue).toContainText("may");
  await issue
    .getByRole("button", { name: "Show source evidence", exact: true })
    .click();
  await expect(page.getByTestId("source-highlight")).toBeVisible();
  await issue
    .getByLabel("Decision reason")
    .fill("Manually compared this value with the original source");
  await issue
    .getByRole("button", { name: "Dismiss issue", exact: true })
    .click();
  await expect(issue.getByText("Dismissed", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("tab", { name: /^Checks/ }).click();
  await expect(
    page.getByText("Manually compared this value with the original source", {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Edit cell", exact: true }).click();
  // Restore the fixture through a real correction so existing source tests remain isolated.
  await page
    .getByRole("button", { name: "Source: 123450", exact: true })
    .click();
  await page.getByLabel("Cell text", { exact: true }).fill("1,234.50");
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /^Checks/ }).click();
  await expect(
    page
      .getByRole("region", { name: "Coverage discrepancy" })
      .filter({ hasText: "1,234.50" }),
  ).toHaveCount(0);
});
