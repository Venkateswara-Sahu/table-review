import { test, expect } from "@playwright/test";
import { signIn, openPageTwoTable } from "./helpers";
import { mkdirSync } from "node:fs";
import path from "node:path";

test("Enter and page buttons navigate without losing a table draft", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page
    .getByLabel("Cell text", { exact: true })
    .fill("keep my correction");
  await page
    .getByRole("button", { name: "Previous page", exact: true })
    .click();
  await expect(page.getByLabel("Original page", { exact: true })).toHaveValue(
    "1",
  );
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "keep my correction",
  );
  await expect(
    page.getByRole("button", { name: "Return to table source", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Original page", { exact: true }).fill("2");
  await page.getByLabel("Original page", { exact: true }).press("Enter");
  await expect(page.getByLabel("Original page", { exact: true })).toHaveValue(
    "2",
  );
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "keep my correction",
  );
  await page
    .getByRole("button", { name: "Discard unsaved changes", exact: true })
    .click();
});

test("a source rectangle is visible during drawing and Escape cancels it", async ({
  page,
}) => {
  await signIn(page);
  await page
    .getByRole("button", { name: "Open report.pdf", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Select region", exact: true })
    .click();
  const box = await page.getByTestId("source-image").boundingBox();
  if (!box) throw new Error("Source image unavailable");
  await page.mouse.move(box.x + 30, box.y + 70);
  await page.mouse.down();
  await page.mouse.move(box.x + 180, box.y + 150, { steps: 6 });
  await expect(page.getByTestId("region-preview")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await expect(page.getByTestId("region-preview")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Select region", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
});

test("the continuous reader reaches another page while retaining the selected table", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page
    .getByRole("button", { name: "Previous page", exact: true })
    .click();
  const reader = page.getByRole("region", {
    name: "Scrollable report pages",
    exact: true,
  });
  await reader.hover();
  await page.mouse.wheel(0, 1000);
  await expect(page.getByLabel("Original page", { exact: true })).toHaveValue(
    "2",
  );
  await expect(
    page.getByRole("grid", { name: "Review table", exact: true }),
  ).toBeVisible();
});

test("source selection, issue navigation and the responsive workspace remain usable", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const sourceCell = page
    .getByRole("button", { name: /^Select source cell A1:/ })
    .first();
  await sourceCell.click();
  await expect(page.locator(".grid-context")).toContainText("A1");
  await page
    .getByRole("grid", { name: "Review table", exact: true })
    .getByRole("button")
    .first()
    .focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Cell text", { exact: true })).toBeFocused();
  const output = path.resolve("../.cache/workspace-redesign");
  mkdirSync(output, { recursive: true });
  async function settledSource() {
    await expect(page.getByLabel("Original page", { exact: true })).toHaveValue(
      "2",
    );
    await expect(page.getByTestId("source-image")).toBeVisible();
    await expect(page.getByTestId("source-image")).toHaveJSProperty(
      "complete",
      true,
    );
    expect(
      await page
        .getByTestId("source-image")
        .evaluate((image: HTMLImageElement) => image.naturalWidth),
    ).toBeGreaterThan(0);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await settledSource();
  await page.screenshot({ path: path.join(output, "desktop-edit.png") });
  await page
    .getByRole("button", { name: "Source: 1,234.50", exact: true })
    .click();
  await page.getByLabel("Cell text", { exact: true }).fill("123450");
  await page.getByLabel("Cell text", { exact: true }).press("Control+Enter");
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /^Checks/ }).click();
  const next = page.getByRole("button", {
    name: "Next unresolved issue",
    exact: true,
  });
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page.locator(".issue-card.active-issue")).toBeVisible();
  await settledSource();
  await page.screenshot({ path: path.join(output, "desktop-checks.png") });
  await page.setViewportSize({ width: 1100, height: 800 });
  await settledSource();
  await page.screenshot({ path: path.join(output, "laptop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".inspector-body").hover();
  await page.mouse.wheel(0, 1200);
  await page.getByRole("tab", { name: "Edit cell", exact: true }).click();
  await expect(page.getByLabel("Cell text", { exact: true })).toBeVisible();
  await expect
    .poll(async () => {
      const panel = await page.locator(".inspector-body").boundingBox();
      const field = await page
        .getByLabel("Cell text", { exact: true })
        .boundingBox();
      return (
        !!panel &&
        !!field &&
        field.y >= panel.y &&
        field.y + field.height <= panel.y + panel.height
      );
    })
    .toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: path.join(output, "phone-edit.png") });
  await page
    .getByRole("button", { name: "Source: 123450", exact: true })
    .click();
  await page.getByLabel("Cell text", { exact: true }).fill("1,234.50");
  await page.getByLabel("Cell text", { exact: true }).press("Control+Enter");
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Source", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Scrollable report pages", exact: true }),
  ).toBeVisible();
  await settledSource();
  await page.screenshot({ path: path.join(output, "phone-source.png") });
});
