import { useEffect, useState, type ReactNode } from "react";
import { apiRequest, errorMessage } from "../../api/client";
import type { Run } from "../../api/models";
import {
  AdoptionReview,
  CandidateChoice,
  type AdoptionActions,
} from "./AdoptionReview";

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value))
    : {};
}
function items(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
function literal(value: unknown): string {
  return value === null || value === undefined
    ? "Unavailable"
    : typeof value === "string"
      ? value
      : JSON.stringify(value);
}
function outcome(run: Run) {
  if (run.manifest.parser_executed === false) return "Synthetic test fixture";
  switch (run.manifest.conversion_outcome) {
    case "success":
      return "Conversion completed";
    case "partial_success":
      return "Partial result — inspect warnings";
    case "failure":
      return "Conversion failed";
    default:
      return "Earlier run — outcome not recorded";
  }
}
function download(run: Run) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(run, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `extraction-${run.id}.json`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Disclosure({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className={className}
      open={open}
      onToggle={(event) => {
        if (event.target === event.currentTarget)
          setOpen(event.currentTarget.open);
      }}
    >
      <summary>{title}</summary>
      {open && children}
    </details>
  );
}

function Difference({ value, label }: { value: unknown; label: string }) {
  const diff = record(value);
  const changed = items(diff.changed_cells);
  const [offset, setOffset] = useState(0);
  return (
    <Disclosure
      className="run-difference"
      title={`${label}: ${diff.equal === true ? "no differences" : `${changed.length} changed cells; ${Object.keys(record(diff.table_changes)).length} table changes`}`}
    >
      <p className="fine-print">
        Cells align by row and column. A moved row does not establish the same
        financial meaning.
      </p>
      {Object.keys(record(diff.table_changes)).length > 0 && (
        <details>
          <summary>Table structure, context or source changes</summary>
          <pre>{JSON.stringify(diff.table_changes, null, 2)}</pre>
        </details>
      )}
      {changed.length > 0 && (
        <div>
          <div className="run-actions">
            <span>
              Showing {offset + 1}–{Math.min(offset + 50, changed.length)} of{" "}
              {changed.length} changed cells
            </span>
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 50))}
            >
              Previous changes
            </button>
            <button
              disabled={offset + 50 >= changed.length}
              onClick={() => setOffset(offset + 50)}
            >
              Next changes
            </button>
          </div>
          <div className="run-diff-scroll">
            <table>
              <thead>
                <tr>
                  <th>Cell / changed fields</th>
                  <th>Earlier value</th>
                  <th>New extraction</th>
                </tr>
              </thead>
              <tbody>
                {changed.slice(offset, offset + 50).map((item, index) => {
                  const cell = record(item);
                  return (
                    <tr key={index}>
                      <th scope="row">
                        Row {Number(cell.row) + 1}, col {Number(cell.col) + 1}
                        <small>{items(cell.fields).join(", ")}</small>
                        <details>
                          <summary>Inspect changed fields</summary>
                          <pre>
                            {JSON.stringify(
                              Object.fromEntries(
                                items(cell.fields).map((field) => [
                                  String(field),
                                  {
                                    before:
                                      record(cell.before)[String(field)] ??
                                      null,
                                    after:
                                      record(cell.after)[String(field)] ?? null,
                                  },
                                ]),
                              ),
                              null,
                              2,
                            )}
                          </pre>
                        </details>
                      </th>
                      <td>
                        {cell.before === null
                          ? "No cell"
                          : literal(record(cell.before).text)}
                      </td>
                      <td>
                        {cell.after === null
                          ? "No cell"
                          : literal(record(cell.after).text)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Disclosure>
  );
}

function RunComparison({
  run,
  onOpen,
  adoption,
}: {
  run: Run;
  onOpen: (id: string) => void;
  adoption: AdoptionActions;
}) {
  const comparison = record(run.manifest.comparison);
  if (comparison.status !== "compared")
    return (
      <p className="fine-print">
        No comparable baseline was recorded. Earlier runs without scope metadata
        are searched only within the latest 20 runs.
      </p>
    );
  const matches = items(comparison.matches);
  return (
    <section aria-label="Rerun comparison">
      <p>
        Compared with run {literal(comparison.baseline_run_id).slice(0, 8)}.
        Earlier tables and review marks are retained. New tables start
        unchecked.
      </p>
      <p className="fine-print">
        Source overlap proposes these pairs; identity is not confirmed.
        Saved-work differences capture the revision when this rerun finished.
      </p>
      <p>
        {matches.length} candidate pairs; {items(comparison.ambiguous).length}{" "}
        ambiguous; {items(comparison.unmatched_before).length} earlier and{" "}
        {items(comparison.unmatched_after).length} new tables unmatched.
      </p>
      {matches.map((item, index) => {
        const match = record(item);
        return (
          <div className="run-pair" key={index}>
            <h4>Candidate pair {index + 1}</h4>
            <div className="run-actions">
              {typeof match.before_id === "string" && (
                <button onClick={() => onOpen(String(match.before_id))}>
                  Open earlier table
                </button>
              )}
              {typeof match.after_id === "string" && (
                <button onClick={() => onOpen(String(match.after_id))}>
                  Open new table
                </button>
              )}
            </div>
            <Difference
              value={match.reviewed_diff}
              label="Saved work vs new extraction"
            />
            <Difference
              value={match.original_diff}
              label="Original extraction vs new extraction"
            />
            {typeof match.before_id === "string" &&
              typeof match.after_id === "string" && (
                <AdoptionReview
                  targetId={match.before_id}
                  candidateId={match.after_id}
                  actions={adoption}
                />
              )}
          </div>
        );
      })}
      {items(comparison.ambiguous).length > 0 && (
        <p className="warning">
          Some source areas have multiple possible matches. Inspect their tables
          individually; no pair was chosen.
        </p>
      )}
      {items(comparison.ambiguous).map((value, index) => {
        const pair = record(value);
        return typeof pair.before_id === "string" ? (
          <CandidateChoice
            key={`ambiguous-${index}`}
            targetId={pair.before_id}
            candidates={items(pair.candidate_ids).filter(
              (id): id is string => typeof id === "string",
            )}
            actions={adoption}
            onOpen={onOpen}
          />
        ) : null;
      })}
      {items(comparison.unmatched_before).length > 0 &&
        items(comparison.unmatched_after).length > 0 && (
          <>
            <p>
              No automatic correspondence was proposed for the following tables.
              Inspect both sources before choosing; disjoint or coarse source
              areas cannot be adopted.
            </p>
            {items(comparison.unmatched_before)
              .filter((id): id is string => typeof id === "string")
              .map((id) => (
                <CandidateChoice
                  key={id}
                  targetId={id}
                  candidates={items(comparison.unmatched_after).filter(
                    (value): value is string => typeof value === "string",
                  )}
                  actions={adoption}
                  onOpen={onOpen}
                />
              ))}
          </>
        )}
    </section>
  );
}

function RunDetails({
  documentId,
  runId,
  onOpen,
  adoption,
}: {
  documentId: string;
  runId: string;
  onOpen: (id: string) => void;
  adoption: AdoptionActions;
}) {
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    apiRequest<Run>(`/api/v1/documents/${documentId}/runs/${runId}`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setRun(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => controller.abort();
  }, [documentId, runId, retry]);
  if (error)
    return (
      <p role="alert">
        Could not load this run: {error}.{" "}
        <button onClick={() => setRetry(retry + 1)}>Retry run details</button>
      </p>
    );
  if (!run) return <p role="status">Loading run details…</p>;
  return (
    <>
      {run.warnings.length > 0 && (
        <ul>
          {run.warnings.map((warning, index) => (
            <li key={index}>{warning}</li>
          ))}
        </ul>
      )}
      {items(run.manifest.page_outcomes).map((item, index) => {
        const page = record(item);
        return (
          <details key={index}>
            <summary>
              Page {literal(page.original_page)}: {literal(page.status)}
              {page.adapter_status === "failure" ? " — grid rejected" : ""}
            </summary>
            <p className="fine-print">
              Parser scores and grades are uncalibrated diagnostics, not cell
              accuracy or permission to skip review. Unavailable scores remain
              null. Error messages are redacted to protect source content.
            </p>
            <pre>{JSON.stringify(page, null, 2)}</pre>
          </details>
        );
      })}
      <RunComparison run={run} onOpen={onOpen} adoption={adoption} />
      <button onClick={() => download(run)}>Download run evidence</button>
      <p className="fine-print">
        Includes available parser outcomes, dependencies, model/source hashes,
        configuration and comparison. Earlier runs may lack this metadata.
      </p>
    </>
  );
}

export function ExtractionHistory({
  documentId,
  refreshKey,
  onOpen,
  adoption,
}: {
  documentId: string;
  refreshKey: string;
  onOpen: (id: string) => void;
  adoption: AdoptionActions;
}) {
  const [open, setOpen] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    apiRequest<{ items: Run[] }>(
      `/api/v1/documents/${documentId}/runs?include_details=false&limit=20`,
      {
        signal: controller.signal,
      },
    )
      .then((value) => setRuns(value.items))
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [documentId, open, refreshKey, retry]);
  return (
    <section className="extraction-history">
      <button
        aria-expanded={open}
        aria-controls="extraction-runs"
        onClick={() => setOpen(!open)}
      >
        Extraction history
      </button>
      {open && (
        <div
          id="extraction-runs"
          className="extraction-runs"
          role="region"
          aria-label="Extraction runs"
          tabIndex={0}
        >
          <div className="run-actions">
            <strong>{runs.length} runs</strong>
            <span className="fine-print">Latest 20 runs</span>
            <button disabled={loading} onClick={() => setRetry(retry + 1)}>
              Refresh runs
            </button>
          </div>
          {loading && <p role="status">Loading extraction outcomes…</p>}
          {error && (
            <p role="alert">
              Could not load extraction history: {error}. Use Refresh runs to
              retry.
            </p>
          )}
          {!loading && !error && runs.length === 0 && (
            <p>No extraction runs yet. Choose Extract tables to create one.</p>
          )}
          {runs.map((run) => (
            <article className="extraction-run" key={run.id}>
              <h3>{outcome(run)}</h3>
              <p className="fine-print">
                Pages{" "}
                {items(record(run.manifest.request).pages).join(", ") ||
                  "unavailable"}
                ; {run.table_ids.length} tables; run {run.id.slice(0, 8)}
              </p>
              <Disclosure title="View outcome and comparison">
                <RunDetails
                  documentId={documentId}
                  runId={run.id}
                  onOpen={onOpen}
                  adoption={adoption}
                />
              </Disclosure>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
