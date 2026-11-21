import { test, expect } from "@playwright/test";
import type { Document, Task } from "../src/api/models";
import {
  createManualTable,
  fixtureTask,
  goToPage,
  openPageTwoTable,
  openReport,
  reviewProgress,
  scrollSourceToPage,
  signIn,
  tableIdFromUrl,
} from "./helpers";

async function reportDocument(page: import("@playwright/test").Page) {
  const documents = (await (
    await page.request.get("/api/v1/documents")
  ).json()) as { items: Document[] };
  const report = documents.items.find((item) => item.filename === "report.pdf");
  if (!report) throw new Error("The fixture report is missing");
  return report;
}

test("page input and previous/next controls clamp to the report", async ({
  page,
}) => {
  await openReport(page, "report.pdf");
  const input = page.getByLabel("Page number");

  await input.fill("0");
  await input.press("Enter");
  await expect(page).toHaveURL(/\/pages\/1$/);
  await expect(input).toHaveValue("1");

  await input.fill("99");
  await input.press("Enter");
  await expect(page).toHaveURL(/\/pages\/2$/);
  await expect(input).toHaveValue("2");
  await expect(page.getByRole("button", { name: "Next page" })).toBeDisabled();

  await input.fill("");
  await input.blur();
  await expect(page).toHaveURL(/\/pages\/1$/);
  await expect(input).toHaveValue("1");
  await expect(
    page.getByRole("button", { name: "Previous page" }),
  ).toBeDisabled();
});

test("scrolling the source preview selects the visible page in overview", async ({
  page,
}) => {
  await openReport(page, "long-report.pdf");
  const input = page.getByLabel("Page number");
  await expect(input).toHaveValue("1");

  await scrollSourceToPage(page, 4);
  await expect(page.locator(".page-pill")).toContainText("Page 4");
  await expect(page).toHaveURL(/\/pages\/4$/);
  await expect(input).toHaveValue("4");
});

test("scrolling across pages never reloads the workspace, snaps scroll back, or drops viewer state", async ({
  page,
}) => {
  await openReport(page, "long-report.pdf");
  await expect(page.getByTestId("source-image")).toBeVisible();

  // Delay page metadata reads to reproduce real-world latency during scroll sync.
  await page.route(
    /\/api\/v1\/documents\/[^/]+\/pages\/\d+$/,
    async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 250));
      await route.continue();
    },
  );

  // Customize viewer state: zoom in and open the navigator drawer.
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  const zoomBefore = await page.locator(".zoom-value").textContent();
  await page
    .getByRole("button", { name: "Document outline", exact: true })
    .click();
  const navDrawer = page.locator('aside[aria-label="Document navigator"]');
  await expect(navDrawer).toBeVisible();

  // Record DOM identity, history depth, and watch for any BootScreen unmount flash.
  const initialHistoryLen = await page.evaluate(() => {
    const el = document.querySelector(".source-scroll");
    (window as unknown as Record<string, unknown>).__scrollEl = el;
    (window as unknown as Record<string, unknown>).__bootFlashes = 0;
    const observer = new MutationObserver(() => {
      if (document.querySelector(".boot-screen")) {
        (window as unknown as Record<string, number>).__bootFlashes++;
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    (window as unknown as Record<string, unknown>).__bootObserver = observer;
    return window.history.length;
  });

  // Scroll to halfway down page 4 (not the top edge) and wait past the 300ms route sync.
  const targetScrollTop = await page
    .locator(".source-scroll")
    .evaluate((el) => {
      const root = el as HTMLElement;
      const sheet = root.querySelector<HTMLElement>('[data-source-page="4"]');
      if (!sheet) throw new Error("Page 4 missing");
      root.scrollTop +=
        sheet.getBoundingClientRect().top -
        root.getBoundingClientRect().top +
        180;
      return root.scrollTop;
    });

  await expect(page.locator(".page-pill")).toContainText("Page 4");
  await expect(page).toHaveURL(/\/pages\/4$/);
  await expect(page.getByLabel("Page number")).toHaveValue("4");
  await page.waitForTimeout(450);

  const check = await page.evaluate(() => {
    const rec = window as unknown as Record<string, unknown>;
    (rec.__bootObserver as MutationObserver | undefined)?.disconnect();
    const el = document.querySelector<HTMLElement>(".source-scroll");
    return {
      sameNode: el === rec.__scrollEl,
      bootFlashes: rec.__bootFlashes as number,
      scrollTop: el?.scrollTop ?? -1,
      historyLen: window.history.length,
    };
  });

  expect(check.sameNode).toBe(true);
  expect(check.bootFlashes).toBe(0);
  expect(Math.abs(check.scrollTop - targetScrollTop)).toBeLessThan(25);
  expect(check.historyLen).toBe(initialHistoryLen);
  await expect(page.locator(".zoom-value")).toHaveText(zoomBefore!);
  await expect(navDrawer).toBeVisible();
});

test("scrolling the source preview does not leave an open table", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const input = page.getByLabel("Page number");
  await expect(input).toHaveValue("2");
  const tableUrl = page.url();

  await scrollSourceToPage(page, 1);
  await expect(page.locator(".page-pill")).toContainText("Page 1");
  await page.waitForTimeout(500);
  await expect(page).toHaveURL(tableUrl);
  await expect(input).toHaveValue("2");
});

test("the table switcher preserves the selected table and review state", async ({
  page,
}) => {
  await openPageTwoTable(page);
  const extractedId = tableIdFromUrl(page);
  const manual = await createManualTable(page, 2, 3, "report.pdf");
  await page.goto(`/reports/${manual.document_id}/pages/2/tables/${manual.id}`);
  const grid = page.getByRole("grid", { name: "Review table" });
  await expect(grid).toHaveAttribute("aria-rowcount", "2");

  const switcher = page.getByLabel("Switch table");
  await expect(switcher).toHaveValue(manual.id);
  await switcher.selectOption(extractedId);
  await expect(page).toHaveURL(new RegExp(`/tables/${extractedId}$`));
  await expect(grid).toHaveAttribute("aria-rowcount", "4");
  await expect(reviewProgress(page)).toContainText("0/12");

  await switcher.selectOption(manual.id);
  await expect(page).toHaveURL(new RegExp(`/tables/${manual.id}$`));
  await expect(grid).toHaveAttribute("aria-rowcount", "2");
  await expect(reviewProgress(page)).toContainText("0/6");
});

test("activity opens a table on its source page, not the current page", async ({
  page,
}) => {
  await openReport(page, "report.pdf");
  await expect(page).toHaveURL(/\/pages\/1$/);
  await page.getByRole("button", { name: /^Activity/ }).click();
  const activity = page.getByRole("dialog", { name: "Activity" });
  await expect(activity).toBeVisible();

  await activity
    .getByRole("button", { name: /^Open table/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/pages\/2\/tables\/[0-9a-f-]+$/);
  await expect(page.getByTestId("source-image")).toHaveAttribute(
    "alt",
    "Original report page 2",
  );
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  await expect(activity).toHaveCount(0);
});

test("a failed extraction offers retry and keeps every earlier attempt", async ({
  page,
}) => {
  await signIn(page);
  const document = await reportDocument(page);
  const now = Date.now();
  const attempts: Task[] = [2, 1, 0].map((offset) =>
    fixtureTask({
      document_id: document.id,
      state: "failed",
      failure_code: "parse_failure",
      attempt_count: 3,
      result: {},
      created_at: new Date(now - offset * 60_000).toISOString(),
    }),
  );
  await page.route("**/api/v1/documents/*/tasks", (route) =>
    route.fulfill({ json: { items: attempts } }),
  );

  await page.goto(`/reports/${document.id}/pages/1`);
  await expect(
    page.getByRole("heading", { name: "Extraction stopped" }),
  ).toBeVisible();
  await expect(page.getByText("The extractor stopped before")).toBeVisible();

  const history = page.locator("details.attempts");
  const summary = history.locator("summary");
  await expect(summary).toContainText("3 attempts, same cause");
  await summary.click();
  await expect(history.locator(".timeline-row")).toHaveCount(3);

  let requested: { pages?: number[] } | null = null;
  await page.route("**/api/v1/documents/*/runs", async (route) => {
    requested = route.request().postDataJSON() as { pages?: number[] };
    await route.fulfill({
      json: fixtureTask({
        document_id: document.id,
        state: "queued",
        result: {},
      }),
    });
  });

  await page.getByRole("button", { name: "Retry page", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Extract tables" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Pages")).toHaveValue("1");
  await dialog.getByRole("button", { name: "Start extraction" }).click();

  await expect(page.locator(".toast")).toContainText(
    "Extraction queued for page 1.",
  );
  await expect(page.getByRole("dialog", { name: "Activity" })).toBeVisible();
  expect(requested).toEqual({
    pages: [1],
    region: null,
    backend: "auto",
    mode: "accurate",
  });
});

test("a queued extraction disables a duplicate run and explains the wait", async ({
  page,
}) => {
  await signIn(page);
  const document = await reportDocument(page);
  const queued = fixtureTask({
    document_id: document.id,
    state: "queued",
    result: {},
  });
  await page.route("**/api/v1/documents/*/tasks", (route) =>
    route.fulfill({ json: { items: [queued] } }),
  );

  await page.goto(`/reports/${document.id}/pages/1`);
  await expect(
    page.getByRole("heading", { name: "Extraction queued" }),
  ).toBeVisible();
  const status = page.getByRole("status").filter({
    hasText: "Extraction is queued for this page.",
  });
  await expect(status).toBeVisible();
  const extract = page.locator(".pane-main .action-row.primary-row");
  await expect(extract).toContainText("Extract page");
  await expect(extract).toBeDisabled();

  await status.getByRole("button", { name: "Activity" }).click();
  const activity = page.getByRole("dialog", { name: "Activity" });
  await expect(activity).toContainText("Page 1");
  await expect(activity).toContainText("Queued");
});

test("a manual table needs a source region and valid dimensions", async ({
  page,
}) => {
  await openReport(page, "report.pdf");
  await goToPage(page, 2);
  await page
    .locator(".pane-main")
    .getByRole("button", { name: /^Create a blank table/ })
    .click();
  const dialog = page.getByRole("dialog", { name: "Create a manual table" });
  await expect(dialog).toContainText("Draw a rectangle around the table");
  await dialog.getByRole("button", { name: "Select a region" }).click();
  await expect(page.getByRole("button", { name: "Drawing…" })).toBeVisible();

  const image = page.getByTestId("source-image");
  const box = await image.boundingBox();
  if (!box) throw new Error("Original source image unavailable");
  await page.mouse.move(box.x + 60, box.y + 124);
  await page.mouse.down();
  await page.mouse.move(box.x + 480, box.y + 252, { steps: 8 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Manual table", exact: true }).click();

  const regionDialog = page.getByRole("dialog", {
    name: "Create a manual table",
  });
  await regionDialog.getByLabel("Rows", { exact: true }).fill("0");
  await regionDialog.getByRole("button", { name: "Create grid" }).click();
  await expect(regionDialog.getByRole("alert")).toContainText(
    "Use 1–500 rows and 1–60 columns.",
  );

  await regionDialog.getByLabel("Rows", { exact: true }).fill("2");
  await regionDialog.getByLabel("Columns", { exact: true }).fill("3");
  await regionDialog.getByRole("button", { name: "Create grid" }).click();
  await expect(page).toHaveURL(/\/tables\/[0-9a-f-]+$/);
  const grid = page.getByRole("grid", { name: "Review table" });
  await expect(grid).toHaveAttribute("aria-rowcount", "2");
  await expect(grid).toHaveAttribute("aria-colcount", "3");
  await expect(reviewProgress(page)).toContainText("0/6");
});
