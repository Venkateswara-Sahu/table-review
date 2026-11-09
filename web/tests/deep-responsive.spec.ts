import { test, expect } from "@playwright/test";
import { openPageTwoTable, reportRow, signIn } from "./helpers";

async function pageOverflow(page: import("@playwright/test").Page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
}

test("the review desk and verdict tray fit at 1100px", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 900 });
  await openPageTwoTable(page);

  const tray = page.locator(".verdict");
  await expect(tray).toBeVisible();
  const box = await tray.boundingBox();
  if (!box) throw new Error("The verdict tray has no layout box");
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(1100.5);

  for (const name of [
    "Confirm value",
    "Confirm blank",
    "Unreadable",
    "Next unchecked cell",
  ]) {
    const button = tray.getByRole("button", { name, exact: true });
    await expect(button).toBeVisible();
    const bounds = await button.boundingBox();
    if (!bounds) throw new Error(`${name} has no layout box`);
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(1100.5);
  }

  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
  expect(
    await page
      .locator(".table-area")
      .evaluate((element) => element.scrollWidth - element.clientWidth),
  ).toBeLessThanOrEqual(1);
});

test("the mobile verdict tray stays inside the viewport and above the safe edge", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openPageTwoTable(page);
  const tray = page.locator(".verdict");
  await expect(tray).toBeVisible();
  const box = await tray.boundingBox();
  if (!box) throw new Error("The verdict tray has no layout box");
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390.5);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(844.5);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("the report library and its rows collapse without horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  await expect(reportRow(page, "report.pdf").first()).toBeVisible();
  await expect(page.getByLabel("Filter reports by name")).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);

  await page.getByLabel("Filter reports by name").fill("rotated");
  await expect(reportRow(page, "rotated.pdf")).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});
