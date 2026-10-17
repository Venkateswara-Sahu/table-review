import { test, expect } from "@playwright/test";
import { openPageTwoTable, signIn } from "./helpers";
import type { Document } from "../src/api/models";

async function openBlankManualTable(page: import("@playwright/test").Page) {
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
        rows: 2,
        columns: 3,
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
  const table = await response.json();
  await page.goto(`/?document=${doc.id}&page=2&table=${table.id}`);
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  return table;
}

for (const stage of ["restore", "read"]) {
  test(`delayed recovery ${stage} keeps newer edits in the active table`, async ({
    page,
  }) => {
    const active = await openBlankManualTable(page);
    const csrf = (await page.context().cookies()).find(
      (item) => item.name === "tr_csrf",
    )!.value;
    const headers = { Origin: "http://127.0.0.1:5173", "X-CSRF-Token": csrf };
    const created = await page.request.post(
      `/api/v1/documents/${active.document_id}/tables`,
      {
        headers,
        data: {
          rows: 1,
          columns: 1,
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
    expect(created.status()).toBe(201);
    const removed = await created.json();
    expect(
      (
        await page.request.post(`/api/v1/tables/${removed.id}/removal`, {
          headers,
          data: {
            removed: true,
            base_revision_id: removed.current_revision_id,
            expected_version: 0,
          },
        })
      ).status(),
    ).toBe(200);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let held = false;
    await page.route(
      stage === "restore"
        ? `**/tables/${removed.id}/removal`
        : `**/tables/${removed.id}`,
      async (route) => {
        held = true;
        await gate;
        await route.continue();
      },
    );
    await page.getByText("Removed tables", { exact: true }).click();
    await page
      .getByRole("button", { name: "Restore table", exact: true })
      .click();
    await expect.poll(() => held).toBeTruthy();
    try {
      await page
        .getByLabel("Cell text", { exact: true })
        .fill("keep my newer draft");
    } finally {
      release();
    }
    await expect(
      page.getByRole("status").filter({ hasText: "Table restored" }),
    ).toBeVisible();
    await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
      "keep my newer draft",
    );
    expect(new URL(page.url()).searchParams.get("table")).toBe(active.id);
    expect(
      (await page.request.get(`/api/v1/tables/${removed.id}`)).status(),
    ).toBe(200);
  });
}

test("previous/next issues track position, wrap and protect dirty edits", async ({
  page,
}) => {
  await openBlankManualTable(page);
  await page.getByRole("tab", { name: /^Checks/ }).click();
  const previous = page.getByRole("button", {
    name: "Previous unresolved issue",
    exact: true,
  });
  const next = page.getByRole("button", {
    name: "Next unresolved issue",
    exact: true,
  });
  await expect(previous).toBeVisible();
  await next.click();
  await expect(page.getByTestId("issue-progress")).toContainText(
    "Unresolved issue 1 of",
  );
  const first = await page.locator(".active-issue").getAttribute("id");
  await previous.click();
  const last = await page.locator(".active-issue").getAttribute("id");
  await next.click();
  expect(await page.locator(".active-issue").getAttribute("id")).toBe(first);
  await page.getByRole("tab", { name: "Edit cell", exact: true }).click();
  await page
    .getByLabel("Cell text", { exact: true })
    .fill("unsaved correction");
  await page.getByRole("tab", { name: /^Checks/ }).click();
  page.once("dialog", (dialog) => dialog.dismiss());
  await previous.click();
  expect(await page.locator(".active-issue").getAttribute("id")).toBe(first);
  await page.getByRole("tab", { name: "Edit cell", exact: true }).click();
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "unsaved correction",
  );
  await page.getByLabel("Cell text", { exact: true }).press("Escape");
  await page.getByRole("tab", { name: /^Checks/ }).click();
  await previous.click();
  expect(await page.locator(".active-issue").getAttribute("id")).toBe(last);
});

test("keyboard help describes working grid and editor controls", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page.getByText("Keyboard help", { exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Keyboard controls" }),
  ).toContainText("F2");
  await page.locator('[data-row="1"][data-col="1"]').focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.locator('[data-row="1"][data-col="2"]')).toBeFocused();
  await page.keyboard.press("F2");
  await expect(page.getByLabel("Cell text", { exact: true })).toBeFocused();
  await page.getByLabel("Cell text", { exact: true }).fill("discard this");
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "1,100.00",
  );
});

test("cancelling dirty keyboard navigation keeps focus on the selected cell", async ({
  page,
}) => {
  await openPageTwoTable(page);
  await page
    .getByLabel("Cell text", { exact: true })
    .fill("keep this unsaved text");
  const selected = page.locator('[data-row="1"][data-col="1"]');
  await selected.focus();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.keyboard.press("ArrowRight");
  await expect(selected).toBeFocused();
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "keep this unsaved text",
  );
});

test("remove table can be cancelled and recovered with renewed review", async ({
  page,
}, info) => {
  await openPageTwoTable(page);
  await page
    .getByRole("button", { name: "Confirm values", exact: true })
    .click();
  await page.getByRole("button", { name: "Remove table", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Remove this table" }),
  ).toContainText("Restore");
  await page.getByRole("button", { name: "Keep table", exact: true }).click();
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  await page.getByRole("button", { name: "Remove table", exact: true }).click();
  await page.screenshot({
    path: info.outputPath("removal-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("removal-mobile.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Remove from active tables", exact: true })
    .click();
  await expect(page.getByRole("grid", { name: "Review table" })).toHaveCount(0);
  await page.getByText("Removed tables", { exact: true }).click();
  await page
    .getByRole("button", { name: "Restore table", exact: true })
    .click();
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
  await expect(
    page.getByText("0 / 12 cells reviewed", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByRole("grid", { name: "Review table" })).toBeVisible();
});
