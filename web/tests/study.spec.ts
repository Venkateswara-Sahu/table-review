import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import type { TableView } from "../src/api/models";
import {
  goToPage,
  openFirstTable,
  openReport,
  reportRow,
  resetTable,
  signIn,
  tableIdFromUrl,
} from "./helpers";

test("the recorder measures processing time, failed attempts and keeps no browser storage", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const modulePath = "/src/features/study/StudyRecorder.ts";
    const { StudyRecorder } = await import(modulePath);
    let now = 0;
    const recorder = new StudyRecorder(
      {
        participant_id: "rehearsal-1",
        document_case_id: "case-1",
        matched_block: "block-1",
        condition: "review_with_checks",
        evidence_kind: "developer_rehearsal",
        experience: "automated_fixture",
        extraction_sha256: "a".repeat(64),
      },
      () => now,
      () => new Date("2026-10-06T00:00:00Z"),
    );
    now = 1000;
    recorder.processing(true);
    now = 6000;
    recorder.processing(false);
    now = 11000;
    const record = recorder.finish("failed");
    let missingCapture = false;
    try {
      new StudyRecorder(
        {
          participant_id: "rehearsal-2",
          document_case_id: "case-1",
          matched_block: "block-1",
          condition: "review_with_checks",
          evidence_kind: "developer_rehearsal",
          experience: "automated_fixture",
          extraction_sha256: "a".repeat(64),
        },
        () => 0,
      ).finish("completed");
    } catch {
      missingCapture = true;
    }
    return { record, missingCapture };
  });
  expect(result.record.processing_time).toBe(5);
  expect(
    (Date.parse(result.record.end) - Date.parse(result.record.start)) / 1000,
  ).toBe(11);
  expect(result.record.outcome).toBe("failed");
  expect(result.record.evidence_kind).toBe("developer_rehearsal");
  expect(result.record.final_result).toBeNull();
  expect(result.missingCapture).toBe(true);
  expect(
    await page.evaluate(() => localStorage.length + sessionStorage.length),
  ).toBe(0);
});

test("capture rejects an unmatched extraction and preserves the actual final table", async ({
  page,
}) => {
  await page.goto("/");
  const value = await page.evaluate(async () => {
    const modulePath = "/src/features/study/StudyRecorder.ts";
    const { StudyRecorder } = await import(modulePath);
    const config = {
      participant_id: "rehearsal-1",
      document_case_id: "case-1",
      matched_block: "block-1",
      condition: "review_without_checks" as const,
      evidence_kind: "developer_rehearsal" as const,
      experience: "automated_fixture" as const,
      extraction_sha256: "a".repeat(64),
    };
    const content = {
      rows: 1,
      columns: 1,
      cells: [],
      header_rows: [],
      row_label_columns: [],
      unit_notes: [],
    };
    const recorder = new StudyRecorder(config);
    let mismatch = false;
    try {
      recorder.observe({
        table_id: "table",
        id: "revision",
        checksum: "b".repeat(64),
        content,
      });
    } catch {
      mismatch = true;
    }
    const matching = new StudyRecorder(config);
    matching.observe({
      table_id: "table",
      id: "revision",
      checksum: "a".repeat(64),
      content,
    });
    matching.observe({
      table_id: "table",
      id: "edit",
      checksum: "c".repeat(64),
      content: { ...content, unit_notes: ["USD millions"] },
    });
    return { mismatch, result: matching.finish("completed") };
  });
  expect(value.mismatch).toBe(true);
  expect(value.result.final_result?.unit_notes).toEqual(["USD millions"]);
  expect(value.result.final_revision_sha256).toBe("c".repeat(64));
});

for (const condition of [
  "review_without_checks",
  "review_with_checks",
] as const) {
  test(`study workflow ${condition} hides comparison controls and downloads the real trial`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(150_000);
    await signIn(page);
    // Freeze the actual fixture extraction before recording its checksum, so
    // the trial reviews exactly the revision it claims to study.
    await openReport(page, "report.pdf");
    await goToPage(page, 2);
    await openFirstTable(page);
    await resetTable(page);
    const initial = (await (
      await page.request.get(`/api/v1/tables/${tableIdFromUrl(page)}`)
    ).json()) as TableView;

    await page.goto("/study");
    await page.getByLabel("Participant ID").fill(`automation-${condition}`);
    await page.getByLabel("Document case").fill("fixture-report-page2");
    await page.getByLabel("Matched block").fill("instrumentation-only");
    await page.getByLabel("Condition").selectOption(condition);
    await page
      .getByLabel("Spreadsheet experience")
      .selectOption("automated_fixture");
    await page
      .getByLabel("Frozen extraction checksum")
      .fill(initial.revision.checksum);
    await page.getByRole("button", { name: "Start full-task timer" }).click();
    await expect(
      page.getByRole("heading", { name: "Reports", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: /^Study/ })).toBeVisible();

    await page.getByRole("link", { name: "Table Review home" }).click();
    await reportRow(page, "report.pdf")
      .first()
      .getByRole("button", { name: "Open", exact: true })
      .click();
    await goToPage(page, 2);
    await openFirstTable(page);
    await expect(
      page.getByRole("grid", { name: "Review table" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Finish", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("tab", { name: "Details", exact: true }),
    ).toHaveCount(0);
    const checks = page.getByRole("tab", { name: /^Checks/ });
    if (condition === "review_with_checks") {
      await checks.click();
      await expect(
        page.getByRole("region", { name: "Source checks" }),
      ).toBeVisible();
    } else {
      await expect(checks).toHaveCount(0);
    }

    await page.getByRole("link", { name: /^Study/ }).click();
    await expect(
      page.getByRole("heading", { name: "Trial in progress" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Finish as failed" }).click();
    const waiting = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download trial record" }).click();
    const download = await waiting;
    expect(download.suggestedFilename()).toBe("study-trial.json");
    await download.saveAs(testInfo.outputPath("trial.json"));
    const record = JSON.parse(
      await fs.readFile(testInfo.outputPath("trial.json"), "utf8"),
    ) as {
      condition: string;
      extraction_sha256: string;
      final_revision_sha256: string;
      final_result: Record<string, unknown>;
      outcome: string;
      start: string;
      end: string;
      processing_time: number;
    };

    expect(record.condition).toBe(condition);
    expect(record.extraction_sha256).toBe(initial.revision.checksum);
    expect(record.final_revision_sha256).toBe(initial.revision.checksum);
    expect(record.final_result).toEqual(initial.revision.content);
    expect(record.outcome).toBe("failed");
    expect(Date.parse(record.end)).toBeGreaterThanOrEqual(
      Date.parse(record.start),
    );
    expect(record.processing_time).toBeGreaterThanOrEqual(0);
  });
}
