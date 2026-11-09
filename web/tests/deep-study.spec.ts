import { test, expect } from "@playwright/test";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import { signIn } from "./helpers";

test("the spreadsheet baseline validates inputs and records the checked artifact", async ({
  page,
}, info) => {
  test.setTimeout(90_000);
  await signIn(page);
  await page.goto("/study");

  await page.getByLabel("Participant ID").fill("not a valid id");
  await page.getByLabel("Document case").fill("fixture-report-page2");
  await page.getByLabel("Matched block").fill("instrumentation-only");
  await page.getByRole("button", { name: "Start full-task timer" }).click();
  await expect(page.locator(".inline-note-danger")).toContainText(
    "Use opaque study identifiers",
  );

  await page.getByLabel("Participant ID").fill("baseline-1");
  await page.getByLabel("Condition").selectOption("spreadsheet_baseline");
  await expect(page.getByLabel("Frozen extraction checksum")).toBeDisabled();
  await page.getByLabel("Evidence").selectOption("external");
  await page
    .getByLabel("Spreadsheet experience")
    .selectOption("automated_fixture");
  await page.getByRole("button", { name: "Start full-task timer" }).click();
  await expect(page.locator(".inline-note-danger")).toContainText(
    "An automated fixture is not an external participant",
  );

  await page.getByLabel("Spreadsheet experience").selectOption("novice");
  await page.getByRole("button", { name: "Start full-task timer" }).click();
  await expect(
    page.getByRole("heading", { name: "Reports", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: /^Study/ }).click();
  await expect(
    page.getByRole("heading", { name: "Trial in progress" }),
  ).toBeVisible();

  const artifact = Buffer.from("checked spreadsheet, baseline condition\n");
  const expected = crypto.createHash("sha256").update(artifact).digest("hex");
  await page.getByLabel("Checked spreadsheet").setInputFiles({
    name: "checked.csv",
    mimeType: "text/csv",
    buffer: artifact,
  });
  await page.getByRole("button", { name: "Finish as completed" }).click();
  await expect(page.getByText("Trial ended")).toBeVisible();

  const waiting = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download trial record" }).click();
  const download = await waiting;
  expect(download.suggestedFilename()).toBe("study-trial.json");
  await download.saveAs(info.outputPath("baseline-trial.json"));
  const record = JSON.parse(
    await fs.readFile(info.outputPath("baseline-trial.json"), "utf8"),
  ) as {
    condition: string;
    outcome: string;
    final_artifact_sha256: string;
    final_result: unknown;
    clock: string;
  };
  expect(record.condition).toBe("spreadsheet_baseline");
  expect(record.outcome).toBe("completed");
  expect(record.final_artifact_sha256).toBe(expected);
  expect(record.final_result).toBeNull();
  expect(record.clock).toBe("monotonic_duration_anchored_to_UTC_start");
  expect(
    await page.evaluate(() => localStorage.length + sessionStorage.length),
  ).toBe(0);
});
