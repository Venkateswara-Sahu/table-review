import { test, expect } from "@playwright/test";
import {
  signIn,
  openPageTwoTable,
  assertHighlightContainsKnownSourceToken,
  fixturePath,
} from "./helpers";

test("original page highlight survives zoom and full-page context", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await expect(page.getByTestId("source-highlight")).toHaveAttribute(
    "data-page",
    "2",
  );
  await assertHighlightContainsKnownSourceToken(page);
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await assertHighlightContainsKnownSourceToken(page);
  await page.getByRole("button", { name: "Full page", exact: true }).click();
  await expect(
    page.getByText("Source: cell reference", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../artifacts/browser/source-page.png",
    fullPage: true,
  });
});

test("upload, explicit page selection and failed upload have real outcomes", async ({
  page,
}) => {
  await signIn(page);
  await page.getByLabel("Upload PDF").setInputFiles({
    name: "new-report.pdf",
    mimeType: "application/pdf",
    buffer: await (await import("node:fs/promises")).readFile(fixturePath()),
  });
  await expect(
    page.getByRole("button", { name: "Open new-report.pdf" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Open new-report.pdf" }).click();
  await page.getByLabel("Original page").fill("2");
  await page.getByRole("button", { name: "View page", exact: true }).click();
  await page.getByLabel("Pages to extract").fill("2");
  await page
    .getByRole("button", { name: "Extract selected pages", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Open table 1", exact: true }),
  ).toBeVisible({ timeout: 45_000 });
  await page.getByRole("button", { name: "All reports", exact: true }).click();
  await page.getByLabel("Upload PDF").setInputFiles({
    name: "broken.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("no PDF"),
  });
  await expect(page.getByRole("alert")).toContainText("supported PDF");
  await expect(
    page.getByRole("button", { name: "Open broken.pdf" }),
  ).toHaveCount(0);
});

test("unsupported page and no detected table still show original source", async ({
  page,
}) => {
  await signIn(page);
  await page
    .getByRole("button", { name: "Open report.pdf", exact: true })
    .click();
  await expect(
    page.getByText("No tables on this page", { exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeVisible();
  await page.getByRole("button", { name: "All reports", exact: true }).click();
  await page
    .getByRole("button", { name: "Open blank.pdf", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "no usable digital text" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Extract selected pages", exact: true }),
  ).toBeDisabled();
});

test("rotation and coarse source references remain honest", async ({
  page,
}) => {
  await signIn(page);
  await page
    .getByRole("button", { name: "Open rotated.pdf", exact: true })
    .click();
  await page.getByRole("button", { name: "Open table 1", exact: true }).click();
  await page
    .getByRole("button", { name: "Source: 1,234.50", exact: true })
    .click();
  const source = await page.getByTestId("source-image").boundingBox();
  expect(source).not.toBeNull();
  if (!source) throw new Error("Rotated image missing");
  expect(source.width / source.height).toBeCloseTo(260 / 460, 1);
  await page.getByRole("button", { name: "All reports", exact: true }).click();
  await page
    .getByRole("button", { name: "Open fallback.pdf", exact: true })
    .click();
  await page.getByRole("button", { name: "Open table 1", exact: true }).click();
  await page
    .getByRole("button", { name: "Source: manual value", exact: true })
    .click();
  await expect(
    page.getByText("Source: page reference", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Exact cell location is unavailable", { exact: true }),
  ).toBeVisible();
});

test("sign-out and revoked sessions clear all private screen state", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("source-image")).toHaveCount(0);
  await signIn(page, "browser-b");
  await expect(
    page.getByRole("button", { name: "Open report.pdf", exact: true }),
  ).toHaveCount(0);
  // Real API invalidates this session; the next foreground request must remove private state.
  const csrf = (await page.context().cookies()).find(
    (cookie) => cookie.name === "tr_csrf",
  )?.value;
  const response = await page.request.delete("/api/v1/session", {
    headers: { Origin: "http://127.0.0.1:5173", "X-CSRF-Token": csrf ?? "" },
  });
  expect(response.status()).toBe(204);
  await page
    .getByRole("button", { name: "Refresh reports", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
});

test("a selected region creates a manual table with honest source references", async ({
  page,
}) => {
  await signIn(page);
  await page
    .getByRole("button", { name: "Open report.pdf", exact: true })
    .click();
  await page.getByLabel("Original page").fill("2");
  await page.getByRole("button", { name: "View page", exact: true }).click();
  await page
    .getByRole("button", { name: "Select region", exact: true })
    .click();
  await expect(page.getByTestId("source-image")).toBeVisible();
  const box = await page.getByTestId("source-image").boundingBox();
  if (!box) throw new Error("Original source image unavailable");
  await page.mouse.move(box.x + 60, box.y + 124);
  await page.mouse.down();
  await page.mouse.move(box.x + 480, box.y + 252, { steps: 8 });
  await page.mouse.up();
  await expect(
    page.getByRole("heading", { name: "Selected region", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Manual rows").fill("2");
  await page.getByLabel("Manual columns").fill("3");
  await page
    .getByRole("button", { name: "Create manual table", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Source: blank cell", exact: true }),
  ).toHaveCount(6);
  await expect(
    page.getByText("Source: table reference", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Exact cell location is unavailable", { exact: true }),
  ).toBeVisible();
});
