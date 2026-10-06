import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import { signIn, fixturePath } from "./helpers";

test("delete report requires confirmation and removes access after reload", async ({
  page,
}) => {
  await signIn(page);
  await page.getByLabel("Upload PDF").setInputFiles({
    name: "delete-report.pdf",
    mimeType: "application/pdf",
    buffer: await fs.readFile(fixturePath()),
  });
  const row = page
    .getByRole("article")
    .filter({
      has: page.getByRole("heading", {
        name: "delete-report.pdf",
        exact: true,
      }),
    });
  await expect(row).toBeVisible();
  page.once("dialog", (dialog) => dialog.dismiss());
  await row
    .getByRole("button", { name: "Delete delete-report.pdf", exact: true })
    .click();
  await expect(row).toBeVisible();
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("Saved tables and exports");
    await dialog.accept();
  });
  await row
    .getByRole("button", { name: "Delete delete-report.pdf", exact: true })
    .click();
  await expect(row).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText("Report deleted");
  await page.reload();
  await expect(row).toHaveCount(0);
});
