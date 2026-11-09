import { test, expect } from "@playwright/test";
import type { Document } from "../src/api/models";
import { reportRow, signIn } from "./helpers";

test("a failed report list load explains itself and refetches", async ({
  page,
}) => {
  let attempts = 0;
  await page.route("**/api/v1/documents", async (route) => {
    attempts++;
    if (attempts === 1)
      await route.fulfill({
        // Client errors are not retried, so the failure state is observable.
        status: 400,
        json: {
          code: "documents_unavailable",
          message: "The report list is temporarily unavailable",
        },
      });
    else await route.continue();
  });

  await signIn(page);
  await expect(page.getByText("Reports could not be loaded")).toBeVisible();
  await expect(
    page.getByText("The report list is temporarily unavailable"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(reportRow(page, "report.pdf").first()).toBeVisible();
  expect(attempts).toBeGreaterThanOrEqual(2);
});

test("an upload that is not a PDF is rejected without creating a report", async ({
  page,
}) => {
  await signIn(page);
  await page.locator('input[type="file"]').setInputFiles({
    name: "broken.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("this is not a pdf"),
  });

  await expect(page.locator(".toast-error")).toContainText(
    "This file does not contain a supported PDF",
  );
  await expect(reportRow(page, "broken.pdf")).toHaveCount(0);
  await expect(page.locator('input[type="file"]')).toHaveValue("");
});

test("a failed deletion keeps the report and explains the failure", async ({
  page,
}) => {
  await signIn(page);
  const documents = (await (
    await page.request.get("/api/v1/documents")
  ).json()) as { items: Document[] };
  const report = documents.items.find((item) => item.filename === "report.pdf");
  if (!report) throw new Error("The fixture report is missing");

  await page.route(`**/api/v1/documents/${report.id}`, async (route) => {
    if (route.request().method() !== "DELETE") return route.continue();
    await route.fulfill({
      status: 500,
      json: {
        code: "deletion_failed",
        message: "The report could not be deleted",
      },
    });
  });

  const row = reportRow(page, "report.pdf").first();
  await row
    .getByRole("button", { name: "More actions for report.pdf" })
    .click();
  await page.getByRole("menuitem", { name: "Delete report" }).click();
  await page
    .getByRole("dialog", { name: "Delete report.pdf?" })
    .getByRole("button", { name: "Delete report", exact: true })
    .click();

  await expect(page.locator(".toast-error")).toContainText(
    "The report could not be deleted",
  );
  await expect(row).toBeVisible();
  expect(
    (await (
      await page.request.get(`/api/v1/documents/${report.id}`)
    ).json()) as Document,
  ).toMatchObject({ id: report.id, filename: "report.pdf" });
});

test("a failed open stays on the library and can be retried", async ({
  page,
}) => {
  await signIn(page);
  const documents = (await (
    await page.request.get("/api/v1/documents")
  ).json()) as { items: Document[] };
  const report = documents.items.find((item) => item.filename === "report.pdf");
  if (!report) throw new Error("The fixture report is missing");

  let attempts = 0;
  await page.route(`**/api/v1/documents/${report.id}/open`, async (route) => {
    attempts++;
    if (attempts === 1)
      await route.fulfill({
        status: 503,
        json: {
          code: "open_failed",
          message: "The report is busy; try again",
        },
      });
    else await route.continue();
  });

  const row = reportRow(page, "report.pdf").first();
  await row.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page.locator(".toast-error")).toContainText(
    "The report is busy; try again",
  );
  await expect(page).toHaveURL(/\/$/);

  await row.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/reports/${report.id}/pages/1$`));
});

test("an empty library offers the only action that can start work", async ({
  page,
}) => {
  await page.route("**/api/v1/documents", (route) =>
    route.fulfill({ json: { items: [] } }),
  );
  await signIn(page);

  await expect(
    page.getByRole("heading", { name: "No reports yet" }),
  ).toBeVisible();
  await expect(page.getByText("0 reports")).toBeVisible();

  const choosing = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Choose a PDF" }).click();
  expect((await choosing).isMultiple()).toBe(false);
});
