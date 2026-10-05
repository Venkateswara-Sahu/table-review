import { expect, type Page } from "@playwright/test";
import path from "node:path";

export const fixturePath = (name = "report.pdf") =>
  path.resolve("../.cache/browser-fixtures", name);

export async function signIn(page: Page, username = "browser-a") {
  await page.goto("/");
  await page.getByLabel("Username").fill(username);
  await page
    .getByLabel("Password", { exact: true })
    .fill("browser-fixture-password-123");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Your reports" }),
  ).toBeVisible();
}

export async function openPageTwoTable(page: Page) {
  await signIn(page);
  await page
    .getByRole("button", { name: "Open report.pdf", exact: true })
    .click();
  await page.getByLabel("Original page").fill("2");
  await page.getByRole("button", { name: "View page", exact: true }).click();
  await page.getByRole("button", { name: "Open table 1", exact: true }).click();
  await page
    .getByRole("button", { name: "Source: 1,234.50", exact: true })
    .click();
  await expect(page.getByTestId("source-highlight")).toBeVisible();
}

export async function assertHighlightContainsKnownSourceToken(page: Page) {
  const highlight = page.getByTestId("source-highlight");
  const image = page.getByTestId("source-image");
  const rect = await highlight.boundingBox();
  const source = await image.boundingBox();
  expect(rect).not.toBeNull();
  expect(source).not.toBeNull();
  if (!rect || !source) throw new Error("Source geometry not visible");
  // Independently read PDFium fixture token: x=247.308..292.128, y=168.172..178.492.
  const sx = source.width / 612;
  const sy = source.height / 792;
  const tokenX = source.x + 270 * sx;
  const tokenY = source.y + 173 * sy;
  expect(rect.x).toBeLessThanOrEqual(tokenX);
  expect(rect.x + rect.width).toBeGreaterThanOrEqual(tokenX);
  expect(rect.y).toBeLessThanOrEqual(tokenY);
  expect(rect.y + rect.height).toBeGreaterThanOrEqual(tokenY);
}
