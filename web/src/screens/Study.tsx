import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Download, FlaskConical, RotateCcw } from "lucide-react";
import {
  setActiveStudy,
  StudyRecorder,
  useStudy,
  type StudyConfig,
} from "../features/study/StudyRecorder";
import {
  Badge,
  Button,
  Field,
  Input,
  InlineNote,
  Select,
} from "../ui/primitives";

const initial: StudyConfig = {
  participant_id: "",
  document_case_id: "",
  matched_block: "",
  condition: "review_with_checks",
  evidence_kind: "developer_rehearsal",
  experience: "novice",
  extraction_sha256: "",
};

export function StudyScreen() {
  const navigate = useNavigate();
  const active = useStudy();
  const [config, setConfig] = useState<StudyConfig>(initial);
  const [error, setError] = useState("");
  const [record, setRecord] = useState<ReturnType<
    StudyRecorder["finish"]
  > | null>(null);

  function start(event: FormEvent) {
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
      setRecord(null);
      setError("");
      navigate("/");
    } catch (caught) {
      setError((caught as Error).message);
    }
  }

  function finish(outcome: "completed" | "failed" | "abandoned") {
    try {
      if (!active) return;
      setRecord(active.finish(outcome));
      setError("");
    } catch (caught) {
      setError((caught as Error).message);
    }
  }

  function download() {
    if (!record) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(record, null, 2)], {
        type: "application/json",
      }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "study-trial.json";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function reset() {
    setActiveStudy(null);
    setRecord(null);
    setError("");
    setConfig(initial);
  }

  return (
    <main className="page study-page">
      <div className="page-head">
        <div>
          <p className="page-kicker">Research instrument</p>
          <h1>Review study</h1>
          <p className="page-description">
            Time a full review task against a frozen extraction. The trial
            record stays on this machine until you download it.
          </p>
        </div>
        {active && (
          <Badge tone="warning" dot>
            Trial running
          </Badge>
        )}
      </div>

      {!active ? (
        <form className="panel study-form" onSubmit={start}>
          <div className="panel-head">
            <div>
              <h2>Start a trial</h2>
              <p className="muted small">
                Use opaque identifiers. Do not enter a participant's name.
              </p>
            </div>
          </div>
          <div className="form-grid form-grid-3">
            <Field label="Participant ID" htmlFor="study-participant">
              <Input
                id="study-participant"
                required
                value={config.participant_id}
                onChange={(event) =>
                  setConfig({ ...config, participant_id: event.target.value })
                }
              />
            </Field>
            <Field label="Document case" htmlFor="study-document">
              <Input
                id="study-document"
                required
                value={config.document_case_id}
                onChange={(event) =>
                  setConfig({ ...config, document_case_id: event.target.value })
                }
              />
            </Field>
            <Field label="Matched block" htmlFor="study-block">
              <Input
                id="study-block"
                required
                value={config.matched_block}
                onChange={(event) =>
                  setConfig({ ...config, matched_block: event.target.value })
                }
              />
            </Field>
          </div>
          <div className="form-grid form-grid-2">
            <Field label="Condition" htmlFor="study-condition">
              <Select
                id="study-condition"
                value={config.condition}
                onChange={(event) =>
                  setConfig({
                    ...config,
                    condition: event.target.value as StudyConfig["condition"],
                  })
                }
              >
                <option value="review_with_checks">Review with checks</option>
                <option value="review_without_checks">
                  Review without checks
                </option>
                <option value="spreadsheet_baseline">
                  Spreadsheet baseline
                </option>
              </Select>
            </Field>
            <Field
              label="Frozen extraction checksum"
              htmlFor="study-checksum"
              hint={
                config.condition === "spreadsheet_baseline"
                  ? "Not used for the spreadsheet baseline."
                  : "The table must match this checksum."
              }
            >
              <Input
                id="study-checksum"
                value={config.extraction_sha256 ?? ""}
                disabled={config.condition === "spreadsheet_baseline"}
                onChange={(event) =>
                  setConfig({
                    ...config,
                    extraction_sha256: event.target.value,
                  })
                }
              />
            </Field>
            <Field label="Evidence" htmlFor="study-evidence">
              <Select
                id="study-evidence"
                value={config.evidence_kind}
                onChange={(event) =>
                  setConfig({
                    ...config,
                    evidence_kind: event.target
                      .value as StudyConfig["evidence_kind"],
                  })
                }
              >
                <option value="developer_rehearsal">Developer rehearsal</option>
                <option value="external">
                  Authorized external participant
                </option>
              </Select>
            </Field>
            <Field label="Spreadsheet experience" htmlFor="study-experience">
              <Select
                id="study-experience"
                value={config.experience}
                onChange={(event) =>
                  setConfig({
                    ...config,
                    experience: event.target.value as StudyConfig["experience"],
                  })
                }
              >
                <option value="frequent">Frequent</option>
                <option value="occasional">Occasional</option>
                <option value="novice">Novice</option>
                <option value="automated_fixture">Automated fixture</option>
              </Select>
            </Field>
          </div>
          {error && (
            <InlineNote tone="danger">
              <p>{error}</p>
            </InlineNote>
          )}
          <div className="form-actions">
            <Button
              type="submit"
              variant="primary"
              icon={<FlaskConical size={15} />}
            >
              Start full-task timer
            </Button>
          </div>
        </form>
      ) : (
        <section className="panel study-active">
          <div className="panel-head">
            <div>
              <h2>Trial in progress</h2>
              <p className="muted small">
                The full-task timer includes opening, setup and waiting.
              </p>
            </div>
            <Badge tone="warning" dot>
              {active.config.condition.replaceAll("_", " ")}
            </Badge>
          </div>
          <dl className="key-values">
            <div>
              <dt>Participant</dt>
              <dd className="mono">{active.config.participant_id}</dd>
            </div>
            <div>
              <dt>Document case</dt>
              <dd className="mono">{active.config.document_case_id}</dd>
            </div>
            <div>
              <dt>Evidence</dt>
              <dd>{active.config.evidence_kind.replaceAll("_", " ")}</dd>
            </div>
          </dl>
          {active.config.condition === "spreadsheet_baseline" && !record && (
            <Field
              label="Checked spreadsheet"
              hint="Record the SHA-256 of the finished workbook or CSV package."
            >
              <Input
                type="file"
                accept=".xlsx,.csv,.zip"
                onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  try {
                    const digest = await crypto.subtle.digest(
                      "SHA-256",
                      await file.arrayBuffer(),
                    );
                    active.baselineArtifact(
                      Array.from(new Uint8Array(digest), (byte) =>
                        byte.toString(16).padStart(2, "0"),
                      ).join(""),
                    );
                    setError("");
                  } catch (caught) {
                    setError((caught as Error).message);
                  }
                }}
              />
            </Field>
          )}
          {error && (
            <InlineNote tone="danger">
              <p>{error}</p>
            </InlineNote>
          )}
          {!record ? (
            <div className="finish-actions">
              <Button onClick={() => finish("completed")}>
                Finish as completed
              </Button>
              <Button onClick={() => finish("failed")}>Finish as failed</Button>
              <Button variant="ghost" onClick={() => finish("abandoned")}>
                Finish as abandoned
              </Button>
            </div>
          ) : (
            <InlineNote tone="success" icon={<Download size={16} />}>
              <p>Trial ended. Download the record before leaving this page.</p>
            </InlineNote>
          )}
          <div className="form-actions">
            {record && (
              <Button
                variant="primary"
                icon={<Download size={15} />}
                onClick={download}
              >
                Download trial record
              </Button>
            )}
            <Button
              variant="ghost"
              icon={<RotateCcw size={15} />}
              onClick={reset}
            >
              Start another trial
            </Button>
          </div>
        </section>
      )}
    </main>
  );
}
