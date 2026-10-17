import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test("native draft gaps explain that blank positions still need source review", async ({
  page,
}) => {
  await page.route("**/api/v1/documents/*/tasks", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: "native-gap-fixture",
            kind: "extract",
            state: "succeeded",
            input: { pages: [1] },
            result: {
              conversion_outcome: "success",
              warnings: [
                "Page 1: Parser omitted 2 table positions; blank cells added for manual image review",
              ],
            },
            created_at: "2026-10-17T10:00:00Z",
          },
        ],
      },
    }),
  );
  await signIn(page);
  await page
    .getByRole("button", { name: "Open report.pdf", exact: true })
    .click();
  await expect(
    page.getByText("Parser omitted 2 table positions", { exact: false }),
  ).toBeVisible();
});

test("mixed-page guidance leads to an explicit region extraction without hiding the source", async ({
  page,
}) => {
  await page.route("**/api/v1/documents/*/pages/1", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: { ...(await response.json()), mixed_image_content: true },
    });
  });
  await signIn(page);
  await page
    .getByRole("button", { name: "Open report.pdf", exact: true })
    .click();
  await expect(
    page.getByText("This page combines PDF text and images.", { exact: false }),
  ).toBeVisible();
  const guidance = page.getByText("This page combines PDF text and images.", {
    exact: false,
  });
  await guidance.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../artifacts/product-readiness/mixed-page-desktop.png",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await guidance.scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../artifacts/product-readiness/mixed-page-mobile.png",
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .getByRole("button", { name: "Select region", exact: true })
    .click();
  const source = page.getByTestId("source-image");
  await source.scrollIntoViewIfNeeded();
  const box = await source.boundingBox();
  if (!box) throw new Error("Original page image is unavailable");
  await page.mouse.move(box.x + 35, box.y + 100);
  await page.mouse.down();
  await page.mouse.move(box.x + 220, box.y + 230, { steps: 5 });
  await page.mouse.up();
  await expect(
    page.getByRole("button", { name: "Extract selected region", exact: true }),
  ).toBeVisible();
  const requested = page.waitForRequest(
    (request) => request.method() === "POST" && /\/runs$/.test(request.url()),
  );
  await page
    .getByRole("button", { name: "Extract selected region", exact: true })
    .click();
  const payload = (await requested).postDataJSON();
  expect(payload.pages).toEqual([1]);
  expect(payload.region.page).toBe(1);
  expect(payload.region.right).toBeGreaterThan(payload.region.left);
  await expect(source).toBeVisible();
});
