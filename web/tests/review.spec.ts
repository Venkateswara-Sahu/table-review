import { test, expect, type Page } from "@playwright/test";
import { signIn } from "./helpers";
import type {
  Document,
  Table,
  TableView,
  ReviewState,
} from "../src/api/models";

// Each test edits its own real manual table. Successful writes use PostgreSQL,
// revision CAS and the real API; the failure test aborts transport only.
async function manual(page: Page, rows = 2, columns = 3) {
  await signIn(page);
  const docs = (await (await page.request.get("/api/v1/documents")).json()) as {
    items: Document[];
  };
  const doc = docs.items.find((item) => item.filename === "review.pdf")!;
  const csrf = (await page.context().cookies()).find(
    (item) => item.name === "tr_csrf",
  )!.value;
  const response = await page.request.post(
    `/api/v1/documents/${doc.id}/tables`,
    {
      headers: { Origin: "http://127.0.0.1:5173", "X-CSRF-Token": csrf },
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
  const table = (await response.json()) as Table;
  await page.goto(`/?document=${doc.id}&page=2&table=${table.id}`);
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  return table;
}

async function cell(page: Page, row: number, col: number) {
  const target = page
    .getByRole("gridcell")
    .filter({ has: page.locator(`[data-row="${row}"][data-col="${col}"]`) });
  await target.getByRole("button").click();
  return target;
}
async function saveText(page: Page, value: string) {
  await page.getByLabel("Cell text", { exact: true }).fill(value);
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
}

test("explicit saved correction survives reopening and original stays immutable", async ({
  page,
}) => {
  const table = await manual(page);
  await cell(page, 0, 0);
  await page.getByLabel("Cell text", { exact: true }).fill("(1,234.50)");
  const before = (await (
    await page.request.get(`/api/v1/tables/${table.id}`)
  ).json()) as TableView;
  expect(before.revision.content.cells[0].text).toBe("");
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Source: (1,234.50)", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Revision history", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Restore original as new draft", exact: true })
    .click();
  await expect(
    page.getByText("Restored as a new draft", { exact: true }),
  ).toBeVisible();
  const after = (await (
    await page.request.get(`/api/v1/tables/${table.id}`)
  ).json()) as TableView;
  expect(after.revision.content.cells[0].text).toBe("");
  expect(after.revision.id).not.toBe(before.revision.id);
});

test("failed save retains unsaved text and leaving needs a decision", async ({
  page,
}) => {
  const table = await manual(page);
  await cell(page, 0, 0);
  await page.getByLabel("Cell text", { exact: true }).fill("keep this draft");
  await page.route(`**/api/v1/tables/${table.id}/edits`, (route) =>
    route.abort("connectionfailed"),
  );
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "keep this draft",
  );
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "All reports", exact: true }).click();
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "keep this draft",
  );
  await page.unrouteAll();
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
});

test("two tabs preserve a conflicting draft until explicit reload and save", async ({
  page,
  context,
}) => {
  const table = await manual(page);
  const second = await context.newPage();
  await second.goto(page.url());
  await expect(
    second.getByRole("grid", { name: "Review table" }),
  ).toBeVisible();
  await cell(page, 0, 0);
  await cell(second, 0, 0);
  await second.getByLabel("Cell text", { exact: true }).fill("second draft");
  await saveText(page, "first save");
  await second.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(second.getByRole("alert")).toContainText(
    "changed in another tab",
  );
  await expect(second.getByLabel("Cell text", { exact: true })).toHaveValue(
    "second draft",
  );
  await second
    .getByRole("button", { name: "Reload latest revision", exact: true })
    .click();
  await expect(second.getByLabel("Cell text", { exact: true })).toHaveValue(
    "second draft",
  );
  await second.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(second.getByText("Saved", { exact: true })).toBeVisible();
  const result = (await (
    await page.request.get(`/api/v1/tables/${table.id}`)
  ).json()) as TableView;
  expect(result.revision.content.cells[0].text).toBe("second draft");
  await second.close();
});

test("blank cells support keyboard range checking and topology invalidates review", async ({
  page,
}) => {
  const table = await manual(page);
  await cell(page, 0, 0);
  await page.keyboard.press("Shift+ArrowRight");
  await expect(
    page.getByText("2 cells selected", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Mark selection checked", exact: true })
    .click();
  await expect(
    page.getByText("2 / 6 cells checked", { exact: true }),
  ).toBeVisible();
  await page.getByText("Table structure", { exact: true }).click();
  await page
    .getByRole("button", { name: "Merge selection", exact: true })
    .click();
  await expect(page.getByRole("gridcell").first()).toHaveAttribute(
    "aria-colspan",
    "2",
  );
  await expect(
    page.getByText("0 / 5 cells checked", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Split cell", exact: true }).click();
  await expect(page.getByRole("gridcell")).toHaveCount(6);
  await page
    .getByRole("button", { name: "Insert row after", exact: true })
    .click();
  await expect(page.getByRole("grid")).toHaveAttribute("aria-rowcount", "3");
  await page.getByRole("button", { name: "Delete row", exact: true }).click();
  await page
    .getByRole("button", { name: "Insert column after", exact: true })
    .click();
  await expect(page.getByRole("grid")).toHaveAttribute("aria-colcount", "4");
  await page
    .getByRole("button", { name: "Delete column", exact: true })
    .click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  const result = (await (
    await page.request.get(`/api/v1/tables/${table.id}`)
  ).json()) as TableView;
  expect([
    result.revision.content.rows,
    result.revision.content.columns,
  ]).toEqual([2, 3]);
});

test("header roles, raw notes and interpretation persist with review invalidation", async ({
  page,
}) => {
  const table = await manual(page);
  await cell(page, 0, 0);
  await saveText(page, "03");
  await page.getByLabel("Value interpretation").selectOption("text");
  await page
    .getByRole("button", { name: "Save interpretation", exact: true })
    .click();
  await page.getByText("Headers and units", { exact: true }).click();
  await page.getByLabel("Header rows").fill("1");
  await page.getByLabel("Row label columns").fill("1");
  await page
    .getByRole("button", { name: "Save header roles", exact: true })
    .click();
  await page
    .getByLabel("Raw unit notes", { exact: true })
    .fill("USD in millions\nUnaudited");
  await page
    .getByRole("button", { name: "Save unit notes", exact: true })
    .click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  const result = (await (
    await page.request.get(`/api/v1/tables/${table.id}`)
  ).json()) as TableView;
  expect(result.revision.content.header_rows).toEqual([0]);
  expect(result.revision.content.row_label_columns).toEqual([0]);
  expect(result.revision.content.unit_notes).toEqual([
    "USD in millions",
    "Unaudited",
  ]);
  expect(result.revision.content.cells[0].interpretation).toBe("text");
});

test("approval explicitly warns about checks not run and an edit reopens the draft", async ({
  page,
}) => {
  const table = await manual(page, 1, 1);
  await cell(page, 0, 0);
  await page
    .getByRole("button", { name: "Mark selection checked", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Approve with warnings", exact: true })
    .click();
  await expect(
    page.getByText("Reviewed with warnings", { exact: true }),
  ).toBeVisible();
  await saveText(page, "corrected");
  await expect(page.getByText("Draft", { exact: true })).toBeVisible();
  const result = (await (
    await page.request.get(`/api/v1/tables/${table.id}`)
  ).json()) as TableView;
  const review = (await (
    await page.request.get(`/api/v1/revisions/${result.revision.id}/review`)
  ).json()) as ReviewState;
  expect(review.current_approval).toBeNull();
  expect(review.checked_cell_ids).toEqual([]);
});

test("large grids virtualize without losing keyboard access to final cells", async ({
  page,
}) => {
  await manual(page, 500, 20);
  const grid = page.getByRole("grid", { name: "Review table" });
  await expect(grid).toHaveAttribute("aria-rowcount", "500");
  expect(await page.getByRole("gridcell").count()).toBeLessThan(1000);
  await cell(page, 0, 0);
  await page.keyboard.press("Control+End");
  await expect(page.locator('[data-row="499"][data-col="19"]')).toBeFocused();
  await saveText(page, "last cell");
  await expect(
    page.getByRole("button", { name: "Source: last cell", exact: true }),
  ).toBeVisible();
});

test("a pending save prevents editing fields that its response would overwrite", async ({
  page,
}) => {
  const table = await manual(page);
  await cell(page, 0, 0);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/v1/tables/${table.id}/edits`, async (route) => {
    await gate;
    await route.continue();
  });
  await page.getByLabel("Cell text", { exact: true }).fill("pending text");
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Saving" }),
  ).toBeVisible();
  try {
    await expect(
      page.getByRole("textbox", { name: "Cell text", exact: true }),
    ).toBeDisabled();
  } finally {
    release();
  }
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
});

test("a lost successful insertion response retries the exact saved structural revision", async ({
  page,
}) => {
  const table = await manual(page);
  await cell(page, 0, 0);
  await page.getByText("Table structure", { exact: true }).click();
  const keys: string[] = [];
  let first = true;
  await page.route(`**/api/v1/tables/${table.id}/edits`, async (route) => {
    keys.push(route.request().headers()["idempotency-key"]);
    if (first) {
      first = false;
      const committed = await route.fetch();
      expect(committed.status()).toBe(200);
      await route.abort("connectionfailed");
    } else await route.continue();
  });
  await page
    .getByRole("button", { name: "Insert row after", exact: true })
    .click();
  await expect(page.getByRole("alert")).toBeVisible();
  const saved = await (
    await page.request.get(`/api/v1/tables/${table.id}`)
  ).json();
  expect(saved.revision.content.rows).toBe(3);
  await page
    .getByRole("button", { name: "Insert row after", exact: true })
    .click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  const reopened = await (
    await page.request.get(`/api/v1/tables/${table.id}`)
  ).json();
  expect(reopened.revision.id).toBe(saved.revision.id);
  expect(reopened.revision.content).toEqual(saved.revision.content);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});
