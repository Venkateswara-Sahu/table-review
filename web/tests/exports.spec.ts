import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import { signIn, fixturePath } from "./helpers";

test("upload correction review export and reopen uses the requested snapshot", async ({
  page,
}, testInfo) => {
  test.setTimeout(100_000);
  await signIn(page);
  await page.getByLabel("Upload PDF").setInputFiles({
    name: "export-report.pdf",
    mimeType: "application/pdf",
    buffer: await fs.readFile(fixturePath()),
  });
  await page
    .getByRole("button", { name: "Open export-report.pdf", exact: true })
    .click();
  await page.getByLabel("Original page").fill("2");
  await page.getByRole("button", { name: "View page", exact: true }).click();
  await page.getByText("Extract tables", { exact: true }).click();
  await page.getByLabel("Pages to extract").fill("2");
  await page
    .getByRole("button", { name: "Extract selected pages", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Open table 1", exact: true })
    .click({ timeout: 45_000 });
  await page
    .getByRole("button", { name: "Source: 1,234.50", exact: true })
    .click();
  await page.getByLabel("Cell text", { exact: true }).fill("1234.50");
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.getByRole("gridcell").first().getByRole("button").focus();
  await page.keyboard.press("Control+Home");
  await page.keyboard.press("Control+Shift+End");
  await page
    .getByRole("button", { name: "Confirm values", exact: true })
    .click();
  await page.getByText("Approve table", { exact: true }).click();
  await page
    .getByRole("button", { name: "Approve with warnings", exact: true })
    .click();
  await expect(
    page.getByText("Reviewed with warnings", { exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Export", exact: true }).click();
  await page
    .getByRole("button", { name: "Create workbook", exact: true })
    .click();
  const downloadButton = page
    .getByRole("button", { name: "Download workbook", exact: true })
    .first();
  await expect(downloadButton).toBeVisible();
  const downloading = page.waitForEvent("download");
  await downloadButton.click();
  const download = await downloading;
  expect(download.suggestedFilename()).toContain("reviewed_with_warnings");
  await download.saveAs(testInfo.outputPath("reviewed.xlsx"));
  expect(
    (await fs.readFile(testInfo.outputPath("reviewed.xlsx")))
      .subarray(0, 2)
      .toString(),
  ).toBe("PK");
  await fs.mkdir("../artifacts/export-viewer", { recursive: true });
  await fs.copyFile(
    testInfo.outputPath("reviewed.xlsx"),
    "../artifacts/export-viewer/reviewed.xlsx",
  );
  await page.getByRole("tab", { name: "Edit cell", exact: true }).click();
  await page.getByLabel("Cell text", { exact: true }).fill("later text");
  await page.getByRole("tab", { name: "Export", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Create workbook", exact: true }),
  ).toBeDisabled();
  await page.getByRole("tab", { name: "Edit cell", exact: true }).click();
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("tab", { name: "Export", exact: true }).click();
  await expect(downloadButton).toBeVisible();
  await expect(page.getByRole("region", { name: "Exports" })).toContainText(
    "Reviewed with warnings",
  );
  await page
    .getByRole("button", { name: "Create CSV package", exact: true })
    .click();
  await expect(
    page
      .getByRole("button", { name: "Download CSV package", exact: true })
      .first(),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Exports" })).toContainText(
    "Draft",
  );
});
