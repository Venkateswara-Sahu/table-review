import { test, expect } from "@playwright/test";
import { openPageTwoTable, signIn } from "./helpers";

test("timeline explains review invalidation and keeps source-value changes", async ({
  page,
}, testInfo) => {
  await openPageTwoTable(page);
  await page
    .getByRole("button", { name: "Confirm values", exact: true })
    .click();
  await page.getByRole("tab", { name: "Edit cell", exact: true }).click();
  await page.getByLabel("Cell text", { exact: true }).fill("1234.51");
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  const timeline = page.getByRole("region", {
    name: "Ordered review timeline",
  });
  await expect(timeline).toContainText("1 cell review reopened.");
  const saved = timeline
    .locator(".timeline-entry")
    .filter({ hasText: "Saved set text" })
    .last();
  await saved.getByText("Inspect recorded changes", { exact: true }).click();
  await expect(saved).toContainText("1,234.50");
  await expect(saved).toContainText("1234.51");
  await saved.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("timeline-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await saved.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("timeline-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("timeline short pages advance by sequence and errors can be retried", async ({
  page,
}) => {
  await signIn(page);
  await page
    .getByRole("button", { name: "Open report.pdf", exact: true })
    .click();
  await page.getByLabel("Original page").fill("2");
  await page.getByRole("button", { name: "View page", exact: true }).click();
  await page
    .locator(".table-choices button")
    .filter({ hasText: "Extracted" })
    .first()
    .click();
  await page.locator('[data-row="1"][data-col="1"]').click();
  const after: number[] = [];
  let refuse = true;
  await page.route("**/timeline?*", async (route) => {
    if (refuse)
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          code: "unavailable",
          message: "Timeline temporarily unavailable",
        }),
      });
    after.push(
      Number(new URL(route.request().url()).searchParams.get("after")),
    );
    const response = await route.fetch();
    const data = await response.json();
    const items = data.items.slice(0, 1);
    await route.fulfill({
      response,
      json: {
        ...data,
        items,
        has_more: data.items.length > 1,
        next_after_sequence: items[0]?.sequence ?? data.next_after_sequence,
      },
    });
  });
  await page.getByRole("tab", { name: "History", exact: true }).click();
  const timeline = page.getByRole("region", {
    name: "Ordered review timeline",
  });
  await expect(timeline.getByRole("alert")).toContainText(
    "Timeline temporarily unavailable",
  );
  refuse = false;
  await timeline.getByRole("button", { name: "Retry timeline" }).click();
  await expect(timeline.locator(".timeline-entry")).toHaveCount(1);
  await timeline
    .getByRole("button", { name: "Load more timeline events" })
    .click();
  await expect(timeline.locator(".timeline-entry")).toHaveCount(2);
  expect(after[0]).toBe(0);
  expect(after[1]).toBeGreaterThan(after[0]);
});

test("timeline exposes interpretation changes even when the literal is unchanged", async ({
  page,
}) => {
  await signIn(page);
  await page
    .getByRole("button", { name: "Open report.pdf", exact: true })
    .click();
  await page.getByLabel("Original page").fill("2");
  await page.getByRole("button", { name: "View page", exact: true }).click();
  await page
    .locator(".table-choices button")
    .filter({ hasText: "Extracted" })
    .first()
    .click();
  await page.locator('[data-row="1"][data-col="1"]').click();
  await page.getByRole("tab", { name: "Edit cell", exact: true }).click();
  await page.getByLabel("Value interpretation").selectOption("uncertain");
  await page
    .getByRole("button", { name: "Save interpretation", exact: true })
    .click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "History", exact: true }).click();
  const saved = page
    .locator(".timeline-entry")
    .filter({ hasText: "Saved set interpretation" })
    .last();
  await saved.getByText("Inspect recorded changes", { exact: true }).click();
  await expect(saved.getByText("interpretation", { exact: true })).toHaveCount(
    2,
  );
  await expect(saved).toContainText("en_us_number");
  await expect(saved).toContainText("uncertain");
});
