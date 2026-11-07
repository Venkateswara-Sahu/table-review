import type { TableView } from "../../api/models";
import { useSyncExternalStore } from "react";

export type StudyConfig = {
  participant_id: string;
  document_case_id: string;
  matched_block: string;
  condition:
    "spreadsheet_baseline" | "review_without_checks" | "review_with_checks";
  evidence_kind: "developer_rehearsal" | "external";
  experience: "frequent" | "occasional" | "novice" | "automated_fixture";
  extraction_sha256: string | null;
};

type Capture = Pick<
  TableView["revision"],
  "id" | "table_id" | "checksum" | "content"
>;

export class StudyRecorder {
  private started: number;
  private start: Date;
  private processingStart: number | null = null;
  private processingMs = 0;
  private capture: Capture | null = null;
  private artifact: string | null = null;
  private finished = false;
  private invalid = false;

  constructor(
    readonly config: StudyConfig,
    private now = () => performance.now(),
    date = () => new Date(),
  ) {
    for (const id of [
      config.participant_id,
      config.document_case_id,
      config.matched_block,
    ])
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id))
        throw new Error("Use opaque study identifiers");
    if (
      config.condition !== "spreadsheet_baseline" &&
      !/^[a-f0-9]{64}$/.test(config.extraction_sha256 ?? "")
    )
      throw new Error("A frozen extraction checksum is required");
    if (
      config.evidence_kind === "external" &&
      config.experience === "automated_fixture"
    )
      throw new Error("An automated fixture is not an external participant");
    this.started = now();
    this.start = date();
  }

  processing(active: boolean) {
    if (this.finished) return;
    if (active && this.processingStart === null)
      this.processingStart = this.now();
    if (!active && this.processingStart !== null) {
      this.processingMs += this.now() - this.processingStart;
      this.processingStart = null;
    }
  }

  observe(revision: Capture) {
    if (this.finished || this.config.condition === "spreadsheet_baseline")
      return;
    if (!this.capture && revision.checksum !== this.config.extraction_sha256) {
      this.invalid = true;
      throw new Error(
        "This table does not match the assigned frozen extraction",
      );
    }
    if (this.capture && revision.table_id !== this.capture.table_id) {
      this.invalid = true;
      throw new Error("A trial must stay on its assigned table");
    }
    this.capture = structuredClone(revision);
  }

  baselineArtifact(sha256: string) {
    if (
      this.config.condition !== "spreadsheet_baseline" ||
      !/^[a-f0-9]{64}$/.test(sha256)
    )
      throw new Error("Record the checked spreadsheet's SHA-256");
    this.artifact = sha256;
  }

  finish(outcome: "completed" | "failed" | "abandoned") {
    if (this.finished) throw new Error("This trial has already ended");
    if (outcome === "completed" && this.invalid)
      throw new Error(
        "The assigned extraction was not preserved; record failure",
      );
    if (outcome === "completed" && !this.capture && !this.artifact)
      throw new Error(
        "Capture the assigned table before completing this trial",
      );
    this.processing(false);
    const elapsed = this.now() - this.started;
    this.finished = true;
    return {
      ...this.config,
      start: this.start.toISOString(),
      end: new Date(this.start.getTime() + elapsed).toISOString(),
      processing_time: this.processingMs / 1000,
      final_result: this.capture?.content ?? null,
      final_revision_sha256: this.capture?.checksum ?? null,
      final_artifact_sha256: this.artifact,
      final_audit: null,
      outcome,
      clock: "monotonic_duration_anchored_to_UTC_start",
    };
  }
}

let active: StudyRecorder | null = null;

export function activeStudy() {
  return active;
}

export function setActiveStudy(value: StudyRecorder | null) {
  active = value;
  window.dispatchEvent(new Event("study-changed"));
}

export function useStudy() {
  return useSyncExternalStore((callback) => {
    window.addEventListener("study-changed", callback);
    return () => window.removeEventListener("study-changed", callback);
  }, activeStudy);
}
