import { test, expect } from "@playwright/test";

test("timer includes processing, failed attempts and no browser persistence", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    // Vite resolves the actual production module in the browser.
    const modulePath = "/src/features/review/StudyRecorder.ts";
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
    return recorder.finish("failed");
  });
  expect(result.processing_time).toBe(5);
  expect((Date.parse(result.end) - Date.parse(result.start)) / 1000).toBe(11);
  expect(result.outcome).toBe("failed");
  expect(result.evidence_kind).toBe("developer_rehearsal");
  expect(result.final_result).toBeNull();
  expect(
    await page.evaluate(() => localStorage.length + sessionStorage.length),
  ).toBe(0);
});

test("capture refuses unmatched extraction and preserves the actual final table", async ({
  page,
}) => {
  await page.goto("/");
  const value = await page.evaluate(async () => {
    const modulePath = "/src/features/review/StudyRecorder.ts";
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
