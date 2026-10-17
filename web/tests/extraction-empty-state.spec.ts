import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

for (const scenario of [
  { name: "not requested", state: null, heading: "Ready to extract this page" },
  {
    name: "queued",
    state: "queued",
    heading: "Extraction queued for this page",
  },
  { name: "running", state: "running", heading: "Extracting this page" },
  {
    name: "failed",
    state: "failed",
    heading: "Extraction stopped on this page",
  },
  {
    name: "complete negative",
    state: "succeeded",
    heading: "No tables found on this page",
  },
  {
    name: "region negative",
    state: "succeeded",
    region: true,
    heading: "No tables found in the selected region",
  },
  {
    name: "partial conversion",
    state: "succeeded",
    partial: true,
    heading: "Extraction needs another look",
  },
]) {
  test(`empty table state distinguishes ${scenario.name}`, async ({ page }) => {
    await page.route("**/api/v1/documents/*/tables?*", (route) =>
      route.fulfill({ json: { items: [] } }),
    );
    await page.route("**/api/v1/documents/*/tasks", (route) =>
      route.fulfill({
        json: {
          items: scenario.state
            ? [
                {
                  id: "display-state-fixture",
                  document_id: "fixture",
                  kind: "extract",
                  state: scenario.state,
                  input: {
                    pages: [1],
                    region: scenario.region ? { page: 1 } : null,
                  },
                  result: {
                    conversion_outcome: scenario.partial
                      ? "partial_success"
                      : "success",
                    warnings: [],
                  },
                  attempt_count: 1,
                  cancellation_requested: false,
                  failure_code: null,
                  created_at: "2026-10-16T14:00:00Z",
                },
              ]
            : [],
        },
      }),
    );
    await signIn(page);
    await page
      .getByRole("button", { name: "Open report.pdf", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: scenario.heading, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: "No tables detected on this page",
        exact: true,
      }),
    ).toHaveCount(0);
  });
}
