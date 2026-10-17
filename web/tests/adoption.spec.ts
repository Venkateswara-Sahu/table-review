import { test, expect, type Page } from "@playwright/test";
import { openPageTwoTable } from "./helpers";

async function replacement(page: Page, ambiguous = false) {
  await openPageTwoTable(page);
  await page
    .getByLabel("Cell text", { exact: true })
    .fill("retained earlier correction");
  await page.getByRole("button", { name: "Save text", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  const target = new URL(page.url()).searchParams.get("table")!;
  const doc = new URL(page.url()).searchParams.get("document")!;
  const count = (
    (await (await page.request.get(`/api/v1/documents/${doc}/runs`)).json())
      .items as unknown[]
  ).length;
  await page.getByText("Extract tables", { exact: true }).click();
  await page.getByLabel("Pages to extract").fill("2");
  await page
    .getByRole("button", { name: "Extract selected pages", exact: true })
    .click();
  await page.getByText("Extract tables", { exact: true }).click();
  await page.getByText("Extraction history", { exact: true }).click();
  const history = page.getByRole("region", {
    name: "Extraction runs",
    exact: true,
  });
  await expect(
    history.getByText(`${count + 1} runs`, { exact: true }),
  ).toBeVisible();
  if (ambiguous)
    await page.route("**/api/v1/documents/*/runs/*", async (route) => {
      const response = await route.fetch();
      const value = await response.json();
      const pair = value.manifest.comparison.matches[0];
      value.manifest.comparison.matches = [];
      value.manifest.comparison.ambiguous = [
        { before_id: pair.before_id, candidate_ids: [pair.after_id] },
      ];
      await route.fulfill({ response, json: value });
    });
  await history
    .getByText("View outcome and comparison", { exact: true })
    .first()
    .click();
  return { target, history };
}

test("explicit replacement retains old corrections and opens an unchecked adopted draft", async ({
  page,
}, info) => {
  const { target, history } = await replacement(page);
  const old = (
    await (await page.request.get(`/api/v1/tables/${target}`)).json()
  ).revision;
  await history
    .getByRole("button", { name: "Review replacement", exact: true })
    .click();
  const panel = history.getByRole("region", {
    name: "Adopt extraction candidate",
    exact: true,
  });
  await expect(panel).toContainText("retained earlier correction");
  await expect(
    panel.getByRole("button", { name: "Adopt as new draft", exact: true }),
  ).toBeDisabled();
  await panel
    .getByRole("button", { name: "Keep current draft", exact: true })
    .click();
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "retained earlier correction",
  );
  await panel
    .getByRole("button", { name: "Review replacement", exact: true })
    .click();
  await panel
    .getByLabel(
      "I compared the source areas and these represent the same table",
      { exact: true },
    )
    .check();
  await panel
    .getByLabel(
      "Use the complete candidate; retain my old corrections only in History",
      { exact: true },
    )
    .check();
  await panel.screenshot({ path: info.outputPath("adoption-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await panel.screenshot({ path: info.outputPath("adoption-mobile.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await panel
    .getByRole("button", { name: "Adopt as new draft", exact: true })
    .click();
  await expect(panel).toContainText("Adopted as a new draft");
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "Metric",
  );
  expect(new URL(page.url()).searchParams.get("table")).toBe(target);
  const saved = (
    await (await page.request.get(`/api/v1/tables/${target}`)).json()
  ).revision;
  expect(saved.parent_revision_id).toBe(old.id);
  expect(
    (
      await (
        await page.request.get(`/api/v1/tables/${target}/revisions/${old.id}`)
      ).json()
    ).content.cells.some(
      (cell: { text: string }) => cell.text === "retained earlier correction",
    ),
  ).toBe(true);
});

async function confirmPanel(page: Page) {
  const panel = page.getByRole("region", {
    name: "Adopt extraction candidate",
    exact: true,
  });
  await panel
    .getByRole("button", { name: "Review replacement", exact: true })
    .click();
  await panel
    .getByLabel(
      "I compared the source areas and these represent the same table",
      { exact: true },
    )
    .check();
  await panel
    .getByLabel(
      "Use the complete candidate; retain my old corrections only in History",
      { exact: true },
    )
    .check();
  return panel;
}

test("dirty work blocks adoption while cancellation stays available", async ({
  page,
}) => {
  await replacement(page);
  const panel = await confirmPanel(page);
  await page
    .getByLabel("Cell text", { exact: true })
    .fill("unsaved current correction");
  await expect(
    panel.getByRole("button", { name: "Adopt as new draft", exact: true }),
  ).toBeDisabled();
  await expect(
    panel.getByRole("button", { name: "Keep current draft", exact: true }),
  ).toBeEnabled();
  await panel
    .getByRole("button", { name: "Keep current draft", exact: true })
    .click();
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "unsaved current correction",
  );
});

test("lost adoption response retries exactly once without another revision", async ({
  page,
}) => {
  const { target } = await replacement(page);
  const panel = await confirmPanel(page);
  const keys: string[] = [];
  let saved = "";
  await page.route(`**/api/v1/tables/${target}/adopt`, async (route) => {
    keys.push((await route.request().allHeaders())["idempotency-key"]);
    if (keys.length === 1) {
      const response = await route.fetch();
      saved = (await response.json()).id;
      await route.abort("failed");
    } else await route.continue();
  });
  await panel
    .getByRole("button", { name: "Adopt as new draft", exact: true })
    .click();
  await expect(panel.getByRole("alert")).toBeVisible();
  await panel
    .getByRole("button", { name: "Adopt as new draft", exact: true })
    .click();
  await expect(panel).toContainText("Adopted as a new draft");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(
    (await (await page.request.get(`/api/v1/tables/${target}`)).json()).revision
      .id,
  ).toBe(saved);
  const history = (
    await (await page.request.get(`/api/v1/tables/${target}/history`)).json()
  ).items;
  expect(
    history.filter(
      (item: { operation: { kind: string } }) =>
        item.operation.kind === "adopt_extraction",
    ),
  ).toHaveLength(1);
});

test("a delayed adoption response preserves newer unsaved work", async ({
  page,
}) => {
  const { target } = await replacement(page);
  const panel = await confirmPanel(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let saved = false;
  await page.route(`**/api/v1/tables/${target}/adopt`, async (route) => {
    const response = await route.fetch();
    saved = true;
    await gate;
    await route.fulfill({ response });
  });
  await panel
    .getByRole("button", { name: "Adopt as new draft", exact: true })
    .click();
  await expect.poll(() => saved).toBe(true);
  await page
    .getByLabel("Cell text", { exact: true })
    .fill("new work during replacement");
  release();
  await expect(panel).toContainText("Adopted as a new draft");
  await expect(page.getByLabel("Cell text", { exact: true })).toHaveValue(
    "new work during replacement",
  );
  await expect(
    page.getByText(
      "Replacement saved. Your current navigation and edits were kept; reopen the earlier table to review its new draft.",
      { exact: true },
    ),
  ).toBeVisible();
});

test("an ambiguous candidate needs explicit choice and confirmation", async ({
  page,
}) => {
  await replacement(page, true);
  await expect(
    page.getByRole("button", { name: "Adopt as new draft", exact: true }),
  ).toHaveCount(0);
  await page
    .getByText(/^Inspect possible replacement for earlier table/)
    .click();
  const choice = page.getByRole("combobox", {
    name: "Candidate extraction",
    exact: true,
  });
  await choice.selectOption({ index: 1 });
  const panel = await confirmPanel(page);
  await panel
    .getByRole("button", { name: "Adopt as new draft", exact: true })
    .click();
  await expect(panel).toContainText("Adopted as a new draft");
});
