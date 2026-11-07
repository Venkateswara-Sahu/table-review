import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import { fixturePath, openPageTwoTable, reportRow, signIn } from "./helpers";

test("the library filters and opens a report", async ({ page }) => {
  await signIn(page);
  await expect(
    page.getByRole("heading", { name: "Reports", exact: true }),
  ).toBeVisible();

  const row = reportRow(page, "report.pdf").first();
  await expect(row).toBeVisible();
  await expect(row).toContainText("2 pages");

  const filter = page.getByLabel("Filter reports by name");
  await filter.fill("rotated");
  await expect(reportRow(page, "report.pdf")).toHaveCount(0);
  await expect(reportRow(page, "rotated.pdf")).toHaveCount(1);

  await filter.fill("nothing-matches");
  await expect(
    page.getByRole("heading", { name: "No matching reports" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear filter" }).click();

  await row.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page).toHaveURL(/\/reports\/[0-9a-f-]+\/pages\/1$/);
  await expect(page.locator(".docbar-title h1")).toHaveText("report.pdf");
  await expect(
    page.getByRole("heading", { name: "Tables on page 1" }),
  ).toBeVisible();
});

test("uploading a PDF opens it, and deletion needs confirmation", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await signIn(page);
  await page.locator('input[type="file"]').setInputFiles({
    name: "lifecycle-report.pdf",
    mimeType: "application/pdf",
    buffer: await fs.readFile(fixturePath()),
  });

  await expect(page.locator(".docbar-title h1")).toHaveText(
    "lifecycle-report.pdf",
  );
  await page.getByRole("link", { name: "Back to reports" }).click();
  await expect(
    page.getByRole("heading", { name: "Reports", exact: true }),
  ).toBeVisible();

  const row = reportRow(page, "lifecycle-report.pdf").first();
  await expect(row).toBeVisible();

  await row
    .getByRole("button", { name: "More actions for lifecycle-report.pdf" })
    .click();
  await page.getByRole("menuitem", { name: "Delete report" }).click();
  const dialog = page.getByRole("dialog", {
    name: "Delete lifecycle-report.pdf?",
  });
  await expect(dialog).toContainText("cannot be undone");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(row).toBeVisible();

  await row
    .getByRole("button", { name: "More actions for lifecycle-report.pdf" })
    .click();
  await page.getByRole("menuitem", { name: "Delete report" }).click();
  await page
    .getByRole("dialog", { name: "Delete lifecycle-report.pdf?" })
    .getByRole("button", { name: "Delete report", exact: true })
    .click();
  await expect(
    page.locator(".toast").filter({ hasText: "Report deleted" }),
  ).toBeVisible();
  await expect(row).toHaveCount(0);

  await page.reload();
  await expect(reportRow(page, "lifecycle-report.pdf")).toHaveCount(0);
});

test("sign-out and a revoked session clear private report state", async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Reports", exact: true }),
  ).toHaveCount(0);

  await signIn(page, "browser-b");
  await expect(reportRow(page, "report.pdf")).toHaveCount(0);

  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();

  await openPageTwoTable(page);
  const csrf = (await page.context().cookies()).find(
    (item) => item.name === "tr_csrf",
  )?.value;
  const response = await page.request.delete("/api/v1/session", {
    headers: {
      Origin: "http://127.0.0.1:5173",
      "X-CSRF-Token": csrf ?? "",
    },
  });
  expect(response.status()).toBe(204);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("source-image")).toHaveCount(0);
});
