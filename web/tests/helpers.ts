import { expect, type Page } from "@playwright/test";
import path from "node:path";
import type { Document, Table, TableView } from "../src/api/models";

export const fixturePath = (name = "report.pdf") =>
  path.resolve("../.cache/browser-fixtures", name);

const origin = "http://127.0.0.1:5173";

export async function signIn(page: Page, username = "browser-a") {
  await page.goto("/");
  const usernameInput = page.getByLabel("Username");
  const reports = page.getByRole("heading", { name: "Reports", exact: true });
  // The app boots through a session probe before it decides between the
  // sign-in screen and the library, so wait for either destination.
  await expect(usernameInput.or(reports).first()).toBeVisible();
  if (await usernameInput.isVisible()) {
    await usernameInput.fill(username);
    await page
      .getByLabel("Password", { exact: true })
      .fill("browser-fixture-password-123");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
  }
  await expect(reports).toBeVisible();
}

export function reportRow(page: Page, filename: string) {
  return page.getByRole("listitem").filter({
    has: page.getByRole("heading", { name: filename, exact: true }),
  });
}

/** The gridcell wrapper that carries aria-selected; the nested button is `cell`. */
export function gridCell(page: Page, row: number, col: number) {
  return page.locator(
    `[role="gridcell"]:has([data-row="${row}"][data-col="${col}"])`,
  );
}

/** The fixture's original extracted table, independent of tab order and leftovers. */
async function fixtureTableId(page: Page) {
  const parts = new URL(page.url()).pathname.split("/").filter(Boolean);
  const documentId = parts[1];
  const pageNo = parts[3] ?? "2";
  const listed = (await (
    await page.request.get(
      `/api/v1/documents/${documentId}/tables?page_no=${pageNo}`,
    )
  ).json()) as { items: Table[] };
  // Tables are listed newest first; the fixture extraction is the oldest one.
  const extraction = listed.items.filter(
    (table) => table.origin === "extraction",
  );
  const table =
    extraction[extraction.length - 1] ?? listed.items[listed.items.length - 1];
  if (!table) throw new Error("The fixture extraction table is missing");
  return {
    documentId,
    pageNo,
    tableId: table.id,
    revisionId: table.current_revision_id,
  };
}

export async function openReport(page: Page, filename: string) {
  await signIn(page);
  const row = reportRow(page, filename).first();
  await row.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page.locator(".docbar-title h1")).toHaveText(filename);
}

export async function goToPage(page: Page, pageNo: number) {
  const input = page.getByLabel("Page number");
  await input.fill(String(pageNo));
  await input.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/pages/${pageNo}(?:/|$)`));
  await expect(page.locator(".docbar-title h1")).toBeVisible();
}

export async function openFirstTable(page: Page) {
  const { documentId, pageNo, tableId, revisionId } =
    await fixtureTableId(page);
  const card = page
    .getByRole("listitem")
    .filter({ hasText: revisionId.slice(0, 8) })
    .first();
  const review = card.getByRole("button", { name: "Review", exact: true });
  if (await review.isVisible().catch(() => false)) await review.click();
  else {
    // Already inside a table view: keep the SPA (and study state) alive.
    const path = `/reports/${documentId}/pages/${pageNo}/tables/${tableId}`;
    await page.evaluate((next) => {
      window.history.pushState({}, "", next);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }, path);
  }
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
}

export function tableIdFromUrl(page: Page) {
  const parts = new URL(page.url()).pathname.split("/").filter(Boolean);
  return parts[parts.length - 1];
}

export function cell(page: Page, row: number, col: number) {
  return page.locator(`[data-row="${row}"][data-col="${col}"]`);
}

export function reviewProgress(page: Page) {
  return page.locator(".review-progress-copy");
}

export async function expectSaved(page: Page) {
  await expect(page.locator(".review-dirty-chip")).toContainText("Saved");
}

async function csrf(page: Page) {
  const token = (await page.context().cookies()).find(
    (item) => item.name === "tr_csrf",
  );
  if (!token) throw new Error("The signed-in session has no CSRF cookie");
  return token.value;
}

export async function apiHeaders(
  page: Page,
  extra: Record<string, string> = {},
) {
  return {
    Origin: origin,
    "X-CSRF-Token": await csrf(page),
    ...extra,
  };
}

async function documentNamed(page: Page, filename: string) {
  const docs = (await (await page.request.get("/api/v1/documents")).json()) as {
    items: Document[];
  };
  const document = docs.items.find((item) => item.filename === filename);
  if (!document) throw new Error(`Fixture report ${filename} is missing`);
  return document;
}

/** Shared fixture server: put a table back to its immutable extraction. */
export async function resetTable(page: Page) {
  const tableId = tableIdFromUrl(page);
  const view = (await (
    await page.request.get(`/api/v1/tables/${tableId}`)
  ).json()) as TableView;
  const restored = await page.request.post(
    `/api/v1/tables/${tableId}/restore`,
    {
      headers: await apiHeaders(page, {
        "Idempotency-Key": crypto.randomUUID(),
      }),
      data: {
        base_revision_id: view.revision.id,
        historical_revision_id: view.table.original_revision_id,
      },
    },
  );
  expect(restored.status()).toBe(200);
  await page.reload();
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  return (await restored.json()) as TableView["revision"];
}

export async function openPageTwoTable(page: Page) {
  await openReport(page, "report.pdf");
  await goToPage(page, 2);
  await openFirstTable(page);
  await resetTable(page);
  // B2 holds the known source token 1,234.50, so geometry checks remain stable.
  await cell(page, 1, 1).click();
  if ((page.viewportSize()?.width ?? 1440) > 900)
    await expect(page.getByTestId("source-highlight")).toBeVisible();
  else await expect(page.getByTestId("source-highlight")).toHaveCount(1);
}

/** Create a blank manual table through the real API, without navigation. */
export async function createManualTable(
  page: Page,
  rows = 2,
  columns = 3,
  filename = "review.pdf",
) {
  await signIn(page);
  const document = await documentNamed(page, filename);
  const response = await page.request.post(
    `/api/v1/documents/${document.id}/tables`,
    {
      headers: await apiHeaders(page),
      data: {
        rows,
        columns,
        region: {
          page: 2,
          left: 60,
          top: 124,
          right: 480,
          bottom: 252,
          page_width: 612,
          page_height: 792,
        },
      },
    },
  );
  expect(response.status()).toBe(201);
  return (await response.json()) as Table;
}

export async function openBlankManualTable(page: Page, columns = 3, rows = 2) {
  const table = await createManualTable(page, rows, columns);
  await page.goto(`/reports/${table.document_id}/pages/2/tables/${table.id}`);
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  return table;
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
