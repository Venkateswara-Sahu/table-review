import { useState } from "react";
import {
  StudyRecorder,
  setActiveStudy,
  useStudy,
  type StudyConfig,
} from "./StudyRecorder";

export function StudyPanel() {
  const active = useStudy();
  const [config, setConfig] = useState<StudyConfig>({
    participant_id: "",
    document_case_id: "",
    matched_block: "",
    condition: "review_with_checks",
    evidence_kind: "developer_rehearsal",
    experience: "novice",
    extraction_sha256: "",
  });
  const [error, setError] = useState("");
  const [ended, setEnded] = useState(false);
  const [record, setRecord] = useState<ReturnType<
    StudyRecorder["finish"]
  > | null>(null);
  function finish(outcome: "completed" | "failed" | "abandoned") {
    try {
      if (active) {
        setRecord(active.finish(outcome));
        setEnded(true);
        setError("");
      }
    } catch (error) {
      setError((error as Error).message);
    }
  }
  function download() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(record, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "study-trial.json";
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section aria-label="Review study" className="manual-panel">
      <h2>Review study</h2>
      <p>
        Compare the assigned table with its source and produce a checked result.
        Time includes opening, setup and waiting. Download the trial record
        before leaving.
      </p>
      {!active ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            try {
              setActiveStudy(
                new StudyRecorder({
                  ...config,
                  extraction_sha256:
                    config.condition === "spreadsheet_baseline"
                      ? null
                      : config.extraction_sha256,
                }),
              );
              setEnded(false);
              setRecord(null);
              setError("");
            } catch (error) {
              setError((error as Error).message);
            }
          }}
        >
          {(
            ["participant_id", "document_case_id", "matched_block"] as const
          ).map((field) => (
            <label key={field}>
              {field.replaceAll("_", " ")}
              <input
                required
                value={config[field]}
                onChange={(e) =>
                  setConfig({ ...config, [field]: e.target.value })
                }
              />
            </label>
          ))}
          <label>
            Study condition
            <select
              value={config.condition}
              onChange={(e) =>
                setConfig({
                  ...config,
                  condition: e.target.value as StudyConfig["condition"],
                })
              }
            >
              <option value="spreadsheet_baseline">Spreadsheet baseline</option>
              <option value="review_without_checks">
                Review without checks
              </option>
              <option value="review_with_checks">Review with checks</option>
            </select>
          </label>
          <label>
            Frozen extraction checksum
            <input
              value={config.extraction_sha256 ?? ""}
              disabled={config.condition === "spreadsheet_baseline"}
              onChange={(e) =>
                setConfig({ ...config, extraction_sha256: e.target.value })
              }
            />
          </label>
          <label>
            Study evidence
            <select
              value={config.evidence_kind}
              onChange={(e) =>
                setConfig({
                  ...config,
                  evidence_kind: e.target.value as StudyConfig["evidence_kind"],
                })
              }
            >
              <option value="developer_rehearsal">Developer rehearsal</option>
              <option value="external">Authorized external participant</option>
            </select>
          </label>
          <label>
            Spreadsheet experience
            <select
              value={config.experience}
              onChange={(e) =>
                setConfig({
                  ...config,
                  experience: e.target.value as StudyConfig["experience"],
                })
              }
            >
              {["frequent", "occasional", "novice", "automated_fixture"].map(
                (v) => (
                  <option key={v}>{v}</option>
                ),
              )}
            </select>
          </label>
          <button>Start full-task timer</button>
        </form>
      ) : (
        <>
          <p role="status">
            {ended ? "Trial ended" : "Full-task timer running"} ·{" "}
            {active.config.condition} · {active.config.evidence_kind}
          </p>
          {active.config.condition === "spreadsheet_baseline" && !ended && (
            <label>
              Checked spreadsheet
              <input
                type="file"
                accept=".xlsx,.csv,.zip"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    const digest = await crypto.subtle.digest(
                      "SHA-256",
                      await file.arrayBuffer(),
                    );
                    active.baselineArtifact(
                      Array.from(new Uint8Array(digest), (b) =>
                        b.toString(16).padStart(2, "0"),
                      ).join(""),
                    );
                  }
                }}
              />
            </label>
          )}
          {!ended && (
            <>
              {(["completed", "failed", "abandoned"] as const).map((value) => (
                <button key={value} onClick={() => finish(value)}>
                  Finish as {value}
                </button>
              ))}
            </>
          )}
          {record && <button onClick={download}>Download trial record</button>}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
