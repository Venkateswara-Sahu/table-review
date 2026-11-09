import { test, expect } from "@playwright/test";
import { cell, openPageTwoTable, signIn } from "./helpers";

test("the global shortcut list opens from the keyboard and ignores typing", async ({
  page,
}) => {
  await signIn(page);
  await page.keyboard.press("Shift+Slash");
  const dialog = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Move between cells");
  await expect(dialog).toContainText("Open this list");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);

  const filter = page.getByLabel("Filter reports by name");
  await filter.fill("");
  await filter.press("Shift+Slash");
  await expect(dialog).toHaveCount(0);
  await expect(filter).toHaveValue("?");
});

test("the grid keeps one roving tab stop and clamps keyboard movement", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await cell(page, 1, 1).focus();
  await expect(cell(page, 1, 1)).toHaveAttribute("tabindex", "0");

  await page.keyboard.press("Control+Home");
  await expect(cell(page, 0, 0)).toBeFocused();
  await expect(cell(page, 0, 0)).toHaveAttribute("tabindex", "0");
  await expect(cell(page, 3, 2)).toHaveAttribute("tabindex", "-1");

  await page.keyboard.press("Control+End");
  await expect(cell(page, 3, 2)).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowDown");
  await expect(cell(page, 3, 2)).toBeFocused();

  await page.keyboard.press("Shift+ArrowLeft");
  await expect(page.locator(".grid-footnote").first()).toContainText(
    "2 selected",
  );
});

test("a dialog keeps keyboard focus inside its own surface", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const finish = page.getByRole("button", { name: "Finish", exact: true });
  await expect(finish).toBeEnabled();
  await finish.click();
  const dialog = page.getByRole("dialog", { name: "Finish this table" });
  await expect(dialog).toBeVisible();

  await page.keyboard.press("Tab");
  const inside = await page.evaluate(
    () => document.activeElement?.closest('[role="dialog"]') !== null,
  );
  expect(inside).toBe(true);
});
