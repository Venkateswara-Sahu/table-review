import { test, expect } from "@playwright/test";
import { openPageTwoTable } from "./helpers";

test("declared subtotal uses confirmed source context and reopens after an edit", async ({
  page,
}, info) => {
  await openPageTwoTable(page);
  await page.getByRole("tab", { name: "Subtotals", exact: true }).click();
  await page
    .locator('[data-row="3"][data-col="1"]')
    .click({ modifiers: ["Shift"] });
  await page.getByRole("tab", { name: "Context", exact: true }).click();
  for (const label of ["reporting period", "currency", "scale wording"]) {
    await page.locator('[data-row="1"][data-col="1"]').click();
    await page
      .locator('[data-row="3"][data-col="1"]')
      .click({ modifiers: ["Shift"] });
    await page
      .getByRole("button", {
        name: `Confirm ${label} for selection`,
        exact: true,
      })
      .click();
    await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  }
  await page.getByRole("tab", { name: "Subtotals", exact: true }).click();
  await page
    .getByRole("button", { name: "Use current cell as total", exact: true })
    .click();
  await page.locator('[data-row="1"][data-col="1"]').click();
  await page
    .locator('[data-row="2"][data-col="1"]')
    .click({ modifiers: ["Shift"] });
  await page
    .getByRole("button", { name: "Add selected cells as inputs", exact: true })
    .click();
  await page
    .getByLabel("Subtotal name", { exact: true })
    .fill("Revenue plus signed expense");
  await page
    .getByLabel("I checked the source scope and selected relation", {
      exact: true,
    })
    .check();
  await page
    .getByRole("button", { name: "Confirm and save subtotal", exact: true })
    .click();
  const result = page.getByRole("region", { name: "Saved subtotal checks" });
  await expect(result).toContainText("Within declared tolerance");
  await expect(result).toContainText("1111.05");
  await result.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("subtotal-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await result.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("subtotal-mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('[data-row="3"][data-col="1"]').click();
  await page.getByRole("tab", { name: "Edit cell", exact: true }).click();
  await page.getByLabel("Cell text", { exact: true }).fill("1112.05");
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Subtotals", exact: true }).click();
  await expect(result).toContainText(/Unavailable|Confirm again/);
  await expect(result).not.toContainText("Within declared tolerance");
});

test("unfinished subtotal setup blocks removal, restore and export without losing selection work", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page.getByRole("tab", { name: "Subtotals", exact: true }).click();
  await page
    .getByLabel("Subtotal name", { exact: true })
    .fill("Unfinished relation");
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await page
    .getByText("Earlier history and revision restore", { exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Remove table", exact: true }),
  ).toBeDisabled();
  for (const button of await page
    .getByRole("button", { name: /Restore (original|revision)/ })
    .all()) {
    await expect(button).toBeDisabled();
  }
  await page.getByRole("tab", { name: "Export", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Create workbook", exact: true }),
  ).toBeDisabled();
  await page.getByRole("tab", { name: "Subtotals", exact: true }).click();
  await expect(page.getByLabel("Subtotal name", { exact: true })).toHaveValue(
    "Unfinished relation",
  );
  page.once("dialog", (dialog) => void dialog.dismiss());
  await page
    .getByRole("button", { name: "Discard subtotal setup", exact: true })
    .click();
  await expect(page.getByLabel("Subtotal name", { exact: true })).toHaveValue(
    "Unfinished relation",
  );
  page.once("dialog", (dialog) => void dialog.accept());
  await page
    .getByRole("button", { name: "Discard subtotal setup", exact: true })
    .click();
  await expect(page.getByLabel("Subtotal name", { exact: true })).toHaveValue(
    "",
  );
  await expect(
    page.getByRole("button", { name: "Remove table", exact: true }),
  ).toBeEnabled();
});
