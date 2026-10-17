import { test, expect } from "@playwright/test";
import { openPageTwoTable } from "./helpers";

test("source context is confirmed, retained after reload and reopened", async ({
  page,
}, testInfo) => {
  await openPageTwoTable(page);
  await expect(page.locator(".grid-context")).toContainText("Revenue / 2025");
  await page.getByRole("tab", { name: "Context", exact: true }).click();
  const panel = page.getByRole("tabpanel", { name: "Context", exact: true });
  await expect(
    panel.getByLabel("Source wording for reporting period"),
  ).toHaveValue(/.+/);
  await expect(
    panel.getByRole("group", { name: "Reporting period", exact: true }),
  ).toContainText("2025");
  await panel
    .getByRole("button", { name: "View header source: 2025", exact: true })
    .click();
  await expect(page.locator(".grid-context")).toContainText("B2");
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith("/edits") &&
      response.request().method() === "POST",
  );
  await panel
    .getByRole("button", { name: "Confirm reporting period", exact: true })
    .click();
  const revision = await (await saved).json();
  expect(revision.content.context_bindings[0].value).toBe("2025");
  await expect(panel).toContainText("Confirmed for this cell: 2025.");
  await panel
    .getByRole("button", { name: "Reopen reporting period", exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("context-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await panel
    .getByRole("button", { name: "Reopen reporting period", exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("context-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  await page
    .getByRole("button", { name: "Source: 1,234.50", exact: true })
    .click();
  await page.getByRole("tab", { name: "Context", exact: true }).click();
  await expect(panel).toContainText("Confirmed for this cell: 2025.");
  await panel
    .getByRole("button", { name: "Reopen reporting period", exact: true })
    .click();
  await expect(
    panel.getByRole("group", { name: "Reporting period", exact: true }),
  ).toContainText("Unconfirmed for this cell");
  await page.getByRole("tab", { name: "Edit cell", exact: true }).click();
  await page.locator("textarea[data-cell-text-editor]").fill("1234.51");
  await page.getByRole("tab", { name: "Context", exact: true }).click();
  await expect(
    panel.getByRole("button", {
      name: "Confirm reporting period",
      exact: true,
    }),
  ).toBeDisabled();
  await expect(panel).toContainText("Save or discard unsaved edits");
});

test("failed context reads have an explicit working retry", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page.route("**/context/**", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        code: "unavailable",
        message: "Temporary context failure",
      }),
    }),
  );
  await page
    .getByRole("button", { name: "Source: (123.45)", exact: true })
    .click();
  await page.getByRole("tab", { name: "Context", exact: true }).click();
  const panel = page.getByRole("tabpanel", { name: "Context", exact: true });
  await expect(panel.getByRole("alert")).toContainText(
    "Temporary context failure",
  );
  await page.unroute("**/context/**");
  await panel
    .getByRole("button", { name: "Retry source context", exact: true })
    .click();
  await expect(
    panel.getByRole("heading", { name: "Header path" }),
  ).toBeVisible();
  await expect(page.locator(".grid-context")).toContainText("Expense");
});
