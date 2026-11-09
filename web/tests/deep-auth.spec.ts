import { test, expect } from "@playwright/test";
import type { Document } from "../src/api/models";
import { apiHeaders, reportRow, signIn } from "./helpers";

async function reportId(page: import("@playwright/test").Page) {
  const documents = (await (
    await page.request.get("/api/v1/documents")
  ).json()) as { items: Document[] };
  const report = documents.items.find((item) => item.filename === "report.pdf");
  if (!report) throw new Error("The fixture report is missing");
  return report.id;
}

test("an unauthenticated deep link returns to the requested workspace after sign-in", async ({
  page,
}) => {
  await signIn(page);
  const documentId = await reportId(page);
  await page.request.delete("/api/v1/session", {
    headers: await apiHeaders(page),
  });

  await page.goto(`/reports/${documentId}/pages/2`);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Username").fill("browser-a");
  await page
    .getByLabel("Password", { exact: true })
    .fill("browser-fixture-password-123");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  await expect(page).toHaveURL(new RegExp(`/reports/${documentId}/pages/2$`));
  await expect(
    page.getByRole("heading", { name: "Page 2", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeVisible();
});

test("invalid credentials are explained without leaving a stale form", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Username").fill("browser-a");
  await page.getByLabel("Password", { exact: true }).fill("not-the-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  await expect(page.locator(".inline-note-danger")).toContainText(
    "Invalid credentials",
  );
  await expect(page.getByRole("button", { name: "Sign in" })).toBeEnabled();
  await expect(page.getByLabel("Password", { exact: true })).toHaveValue(
    "not-the-password",
  );
});

test("unknown routes and missing report resources land on an honest 404", async ({
  page,
}) => {
  await signIn(page);
  const documentId = await reportId(page);

  await page.goto("/a-route-that-does-not-exist");
  await expect(
    page.getByRole("heading", { name: "This page is not here" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Back to reports" }),
  ).toBeVisible();

  await page.goto(`/reports/${documentId}/pages/99`);
  await expect(
    page.getByRole("heading", { name: "This page is not here" }),
  ).toBeVisible();

  await page.goto(
    `/reports/${documentId}/pages/1/tables/00000000-0000-0000-0000-000000000000`,
  );
  await expect(
    page.getByRole("heading", { name: "This table is not available" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Back to page 1" }).click();
  await expect(page).toHaveURL(new RegExp(`/reports/${documentId}/pages/1$`));

  await page.goto("/reports/00000000-0000-0000-0000-000000000000/pages/1");
  await expect(page.getByText("This report could not be opened")).toBeVisible();

  await page.goto("/");
  await expect(reportRow(page, "report.pdf").first()).toBeVisible();
});

test("the API rejects missing sessions, missing CSRF tokens and cross-owner reads", async ({
  page,
}) => {
  expect((await page.request.get("/api/v1/documents")).status()).toBe(401);

  await signIn(page);
  const documentId = await reportId(page);

  const noToken = await page.request.post(
    `/api/v1/documents/${documentId}/open`,
    { headers: { Origin: "http://127.0.0.1:5173" } },
  );
  expect(noToken.status()).toBe(403);

  const opened = await page.request.post(
    `/api/v1/documents/${documentId}/open`,
    { headers: await apiHeaders(page) },
  );
  expect(opened.status()).toBe(200);

  const signedOut = await page.request.delete("/api/v1/session", {
    headers: await apiHeaders(page),
  });
  expect(signedOut.status()).toBe(204);

  await signIn(page, "browser-b");
  expect(
    (await page.request.get(`/api/v1/documents/${documentId}`)).status(),
  ).toBe(404);
  expect(
    (
      await page.request.get(`/api/v1/documents/${documentId}/tables?page_no=2`)
    ).status(),
  ).toBe(404);
});
