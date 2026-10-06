import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";

for (const condition of [
  "review_without_checks",
  "review_with_checks",
] as const) {
  test(`study workflow ${condition} preserves frozen input and downloads real rehearsal`, async ({
    page,
  }, testInfo) => {
    await page.goto("/?study=1");
    await page.getByLabel("Username").fill("browser-a");
    await page
      .getByLabel("Password", { exact: true })
      .fill("browser-fixture-password-123");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Your reports" }),
    ).toBeVisible();
    const initial = await page.evaluate(async () => {
      const docs = await (await fetch("/api/v1/documents")).json();
      const doc = docs.items.find(
        (d: { filename: string }) => d.filename === "report.pdf",
      );
      const tables = await (
        await fetch(`/api/v1/documents/${doc.id}/tables?page_no=2`)
      ).json();
      return (await fetch(`/api/v1/tables/${tables.items[0].id}`)).json();
    });
    await page.getByLabel("participant id").fill("automation-" + condition);
    await page.getByLabel("document case id").fill("fixture-report-page2");
    await page.getByLabel("matched block").fill("instrumentation-only");
    await page.getByLabel("Study condition").selectOption(condition);
    await page
      .getByLabel("Spreadsheet experience")
      .selectOption("automated_fixture");
    await page
      .getByLabel("Frozen extraction checksum")
      .fill(initial.revision.checksum);
    await page.getByRole("button", { name: "Start full-task timer" }).click();
    await page
      .getByRole("button", { name: "Open report.pdf", exact: true })
      .click();
    await page.getByLabel("Original page").fill("2");
    await page.getByRole("button", { name: "View page", exact: true }).click();
    await page
      .getByRole("button", { name: "Open table 1", exact: true })
      .click();
    await expect(page.getByRole("grid")).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Source checks" }),
    ).toHaveCount(condition === "review_with_checks" ? 1 : 0);
    await expect(
      page.getByRole("button", { name: "Approve with warnings", exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole("button", { name: "Finish as failed", exact: true })
      .click();
    const waiting = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Download trial record", exact: true })
      .click();
    const download = await waiting;
    await download.saveAs(testInfo.outputPath("trial.json"));
    const record = JSON.parse(
      await fs.readFile(testInfo.outputPath("trial.json"), "utf8"),
    );
    expect(record.extraction_sha256).toBe(initial.revision.checksum);
    expect(record.final_revision_sha256).toBe(initial.revision.checksum);
    expect(record.final_result).toEqual(initial.revision.content);
    expect(record.outcome).toBe("failed");
    expect(Date.parse(record.end)).toBeGreaterThan(Date.parse(record.start));
    await fs.mkdir("../artifacts/study-rehearsal", { recursive: true });
    await fs.writeFile(
      `../artifacts/study-rehearsal/${condition}.json`,
      JSON.stringify(record, null, 2),
    );
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(
      page.getByRole("region", { name: "Review study" }),
    ).toHaveCount(0);
  });
}
