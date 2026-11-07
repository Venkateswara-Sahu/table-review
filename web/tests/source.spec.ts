import { test, expect } from "@playwright/test";
import {
  apiHeaders,
  assertHighlightContainsKnownSourceToken,
  openPageTwoTable,
  openReport,
  reportRow,
} from "./helpers";

test("a temporarily busy PDF reader recovers within bounded read retries", async ({
  page,
}) => {
  let attempts = 0;
  await page.route(/\/pages\/1\/image(\?|$)/, async (route) => {
    attempts++;
    if (attempts <= 2) {
      await route.fulfill({
        status: 429,
        json: { code: "pdf_busy", message: "PDF operation busy" },
      });
    } else await route.continue();
  });
  await openReport(page, "report.pdf");
  await expect(page.getByTestId("source-image")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(attempts).toBeGreaterThanOrEqual(3);
});

test("persistent PDF contention stops, explains itself and can be retried", async ({
  page,
}) => {
  let attempts = 0;
  const busy = /\/pages\/1\/image(\?|$)/;
  await page.route(busy, async (route) => {
    attempts++;
    await route.fulfill({
      status: 429,
      json: { code: "pdf_busy", message: "PDF operation busy" },
    });
  });
  await openReport(page, "report.pdf");
  await expect(page.getByText("PDF operation busy")).toBeVisible();
  expect(attempts).toBe(3);
  await expect(
    page.getByRole("button", { name: "Retry page 1", exact: true }),
  ).toBeVisible();

  await page.unroute(busy);
  await page.getByRole("button", { name: "Retry page 1", exact: true }).click();
  await expect(page.getByTestId("source-image")).toBeVisible();
});

test("the source highlight keeps its geometry through zoom and page sizing", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const highlight = page.getByTestId("source-highlight");
  await expect(highlight).toHaveAttribute("data-page", "2");
  await expect(page.locator(".source-caption")).toContainText("Source for 2:2");
  await expect(page.locator(".source-caption")).toContainText(
    "page 2 · cell reference",
  );
  await assertHighlightContainsKnownSourceToken(page);

  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await assertHighlightContainsKnownSourceToken(page);
  await page.getByLabel("Page sizing").selectOption("page");
  await assertHighlightContainsKnownSourceToken(page);
  await expect(highlight).toHaveClass(/precision-cell/);
});

test("rotated pages keep their shape and coarse references stay honest", async ({
  page,
}) => {
  await openReport(page, "rotated.pdf");
  await page
    .getByRole("button", { name: "Review", exact: true })
    .first()
    .click();
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  await expect(page.getByTestId("source-image")).toBeVisible();
  const rotated = await page.getByTestId("source-image").boundingBox();
  if (!rotated) throw new Error("Rotated source image is unavailable");
  expect(rotated.width / rotated.height).toBeCloseTo(260 / 460, 1);

  await page.getByRole("link", { name: "Back to reports" }).click();
  await reportRow(page, "fallback.pdf")
    .getByRole("button", { name: "Open", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Review", exact: true })
    .first()
    .click();
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  await expect(page.locator(".source-caption")).toContainText(
    "page 1 · page reference",
  );
  await expect(page.locator(".source-precision-note")).toContainText(
    "Exact cell location is unavailable",
  );
});

test("a source cell jumps into the review grid and a revoked session clears it", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page
    .getByRole("button", { name: "Select source cell C4: 1,000.00" })
    .first()
    .click();
  await expect(page.locator(".grid-footnote").first()).toContainText("4:3");
  await expect(page.locator("textarea[data-cell-text-editor]")).toHaveValue(
    "1,000.00",
  );

  const response = await page.request.delete("/api/v1/session", {
    headers: await apiHeaders(page),
  });
  expect(response.status()).toBe(204);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByTestId("source-image")).toHaveCount(0);
});
