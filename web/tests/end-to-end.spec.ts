import { test, expect } from "@playwright/test";
import { signIn } from "./helpers";

test("acknowledged edit remains saved when review refresh fails", async ({
  page,
}) => {
  await signIn(page);
  const docs = await (await page.request.get("/api/v1/documents")).json();
  const doc = docs.items.find(
    (value: { filename: string }) => value.filename === "review.pdf",
  );
  const csrf = (await page.context().cookies()).find(
    (value) => value.name === "tr_csrf",
  )!.value;
  const created = await page.request.post(
    `/api/v1/documents/${doc.id}/tables`,
    {
      headers: { Origin: "http://127.0.0.1:5173", "X-CSRF-Token": csrf },
      data: {
        rows: 2,
        columns: 2,
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
  const table = await created.json();
  await page.goto(`/?document=${doc.id}&page=2&table=${table.id}`);
  await expect(page.getByRole("grid")).toBeVisible();
  await expect(
    page.getByRole("gridcell").first().locator("xpath=.."),
  ).toHaveAttribute("role", "row");
  await page.getByRole("gridcell").first().getByRole("button").click();
  await page
    .getByLabel("Cell text", { exact: true })
    .fill("saved despite refresh failure");
  await page.route("**/api/v1/revisions/*/review", (route) =>
    route.abort("connectionfailed"),
  );
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(
    page.getByText("Saved; review status unavailable", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "saved despite refresh failure",
  );
  await expect(page.getByRole("alert")).toContainText(
    "Reload to refresh review status",
  );
  const saved = await (
    await page.request.get(`/api/v1/tables/${table.id}`)
  ).json();
  expect(saved.revision.content.cells[0].text).toBe(
    "saved despite refresh failure",
  );
  await page.unrouteAll();
  await page.reload();
  await expect(
    page.getByRole("button", {
      name: "Source: saved despite refresh failure",
      exact: true,
    }),
  ).toBeVisible();
});
