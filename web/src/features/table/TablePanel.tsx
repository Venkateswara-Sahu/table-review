import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  Crop,
  Grid2X2,
  Info,
  RefreshCw,
  RotateCcw,
  ScanLine,
  Trash2,
} from "lucide-react";
import type { PageInfo, Table, Task } from "../../api/models";
import { Button, InlineNote, Spinner } from "../../ui/primitives";

function extractionNotes(page: PageInfo, latest?: Task) {
  const notes: string[] = [];
  if (latest?.state === "failed") {
    notes.push(
      latest.failure_code === "unsupported_page"
        ? "A selected page has no usable digital text. Select another page or create a manual table."
        : latest.failure_code === "ocr_unavailable"
          ? "Offline OCR models are unavailable. Your saved work is retained."
          : "Extraction stopped on this page. Saved work is retained.",
    );
  }
  if (latest?.result?.conversion_outcome === "partial_success") {
    notes.push(
      "Extraction was incomplete. Check the page, then retry or select a region.",
    );
  }
  if (page.unsupported_reason) notes.push(page.unsupported_reason);
  if (page.requires_ocr)
    notes.push(
      "This page uses offline OCR. Check every figure against the page image.",
    );
  if (page.mixed_image_content)
    notes.push(
      "This page mixes PDF text and images. If a table is missing, select its region and extract it with OCR.",
    );
  if (Array.isArray(latest?.result?.warnings)) {
    for (const warning of latest.result.warnings) {
      if (
        typeof warning === "string" &&
        warning.startsWith(`Page ${page.page_no}:`) &&
        /OCR omitted|Parser omitted|region retry|table recovered|isolated dash|optional mark|currency|reconciliation/.test(
          warning,
        )
      )
        notes.push(warning);
    }
  }
  return [...new Set(notes)];
}

function timeOf(value?: string) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

function failureCause(task?: Task) {
  switch (task?.failure_code) {
    case "unsupported_page":
      return "This page has no usable digital text.";
    case "ocr_unavailable":
      return "Offline OCR models are unavailable.";
    default:
      return "The extractor stopped before producing a table.";
  }
}

function failureLabel(task?: Task) {
  switch (task?.failure_code) {
    case "unsupported_page":
      return "No usable text";
    case "ocr_unavailable":
      return "OCR unavailable";
    default:
      return "Extractor stopped";
  }
}

function pageSize(page: PageInfo) {
  if (!page.width || !page.height) return "—";
  return `${(page.width / 72).toFixed(1)} × ${(page.height / 72).toFixed(1)} in`;
}

export function TablePanel({
  pageNo,
  page,
  tables,
  removed,
  latest,
  tasks,
  busy,
  onOpenTable,
  onRestore,
  onExtract,
  onRequestRegion,
  onManual,
  onActivity,
}: {
  pageNo: number;
  page: PageInfo;
  tables: Table[];
  removed: Table[];
  latest?: Task;
  tasks: Task[];
  busy: boolean;
  onOpenTable: (id: string) => void;
  onRestore: (table: Table) => void;
  onExtract: () => void;
  onRequestRegion: () => void;
  onManual: () => void;
  onActivity: () => void;
}) {
  const empty = !tables.length;
  const processing = latest?.state === "queued" || latest?.state === "running";
  const failed = latest?.state === "failed";
  const regionTask = !!latest?.input.region;
  const outcome = latest?.result?.conversion_outcome;
  const notes = extractionNotes(page, latest);
  const attempts = tasks
    .filter((task) => task.state === "failed")
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );

  const heading = processing
    ? latest?.state === "queued"
      ? "Extraction queued"
      : "Extracting this page"
    : failed
      ? "Extraction stopped"
      : latest?.state === "succeeded" && outcome === "success"
        ? regionTask
          ? "No tables found in the selected region"
          : "No tables found on this page"
        : latest?.state === "succeeded" && outcome === "partial_success"
          ? "Extraction needs another look"
          : "No tables on this page yet";

  const lede = processing
    ? "You can keep reading the source. The draft appears here when extraction finishes."
    : failed
      ? `${notes[0] ?? "Extraction stopped before it finished."} Nothing new was saved${
          tables.length
            ? `; ${tables.length} saved table${tables.length === 1 ? " is" : "s are"} unchanged.`
            : "."
        }`
      : latest?.state === "succeeded"
        ? "If you can see a table, draw around it on the source and run OCR, or create the grid manually."
        : "Extract the whole page, draw around one table, or type the grid yourself. Nothing runs until you ask.";

  const chip = processing
    ? { label: "Extracting", tone: "" }
    : failed
      ? { label: "Extraction failed", tone: "failed" }
      : tables.length
        ? {
            label: `${tables.length} saved table${tables.length === 1 ? "" : "s"}`,
            tone: "",
          }
        : latest?.state === "succeeded"
          ? { label: "No tables found", tone: "" }
          : { label: "Not extracted", tone: "" };

  return (
    <section className="table-panel" aria-label={`Tables on page ${pageNo}`}>
      <header className="pane-head">
        <h2>Page {pageNo}</h2>
        <span className={`chip ${chip.tone}`}>
          <span className="dot" />
          {chip.label}
        </span>
        <span className="spacer" />
      </header>

      <div className="pane-body">
        <main className="pane-main pane-main-center">
          <div className="pane-copy">
            <h3 className="pane-heading">
              {failed && !empty
                ? "Extraction stopped"
                : empty
                  ? heading
                  : "Saved tables on this page"}
            </h3>
            <p className="lede">
              {failed
                ? `${failureCause(latest)} Nothing new was saved; ${
                    tables.length
                      ? `${tables.length} saved table${tables.length === 1 ? " is" : "s are"} unchanged.`
                      : "no table was added."
                  }`
                : empty
                  ? lede
                  : `${tables.length} table${tables.length === 1 ? "" : "s"} extracted from this page. Open one to review every cell, or start another table.`}
            </p>
          </div>

          {failed && (
            <>
              <div className="failure-actions">
                <Button
                  variant="primary"
                  size="sm"
                  icon={<RefreshCw size={14} />}
                  disabled={busy || page.extraction_supported === false}
                  onClick={onExtract}
                >
                  Retry page
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  icon={<Crop size={14} />}
                  disabled={busy}
                  onClick={onRequestRegion}
                >
                  Select region
                </Button>
                <button
                  type="button"
                  className="link-button"
                  disabled={busy}
                  onClick={onManual}
                >
                  Create manual table
                </button>
              </div>

              <p className="saved-note">
                <CheckCircle2
                  className="icon"
                  size={12}
                  aria-hidden="true"
                  style={{ color: "var(--success)" }}
                />
                {tables.length
                  ? `${tables.length} saved table${tables.length === 1 ? " is" : "s are"} unchanged.`
                  : "The original PDF stays unchanged."}
              </p>

              {latest.attempt_count > 1 && (
                <details className="attempts">
                  <summary className="attempt-head">
                    <ChevronDown size={13} aria-hidden="true" />
                    {latest.attempt_count} attempts, same cause
                    <span className="attempt-count">
                      {timeOf(attempts[attempts.length - 1]?.created_at)} –{" "}
                      {timeOf(latest.created_at)}
                    </span>
                  </summary>
                  <div className="timeline">
                    {attempts.slice(0, 6).map((task) => (
                      <div className="timeline-row" key={task.id}>
                        <span className="timeline-dot" />
                        <div className="timeline-main">
                          <strong>Extraction stopped</strong>
                          <span>No new table saved</span>
                        </div>
                        <span className="timeline-time">
                          {timeOf(task.created_at)}
                        </span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}

          {!empty && (
            <div className="table-list" role="list">
              {tables.map((table, index) => (
                <div className="table-row" role="listitem" key={table.id}>
                  <span className="table-glyph" aria-hidden="true">
                    <Grid2X2 size={17} />
                  </span>
                  <div className="table-row-main">
                    <strong>Table {index + 1}</strong>
                    <span>
                      {table.origin === "manual" ? "Manual grid" : "Extracted"}{" "}
                      · revision{" "}
                      <span className="mono">
                        {table.current_revision_id.slice(0, 8)}
                      </span>
                    </span>
                  </div>
                  <button
                    type="button"
                    className="review-link"
                    onClick={() => onOpenTable(table.id)}
                  >
                    Review table
                    <ArrowRight size={12} aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {!failed && empty && (
            <>
              <div className="action-list primary-list">
                <button
                  type="button"
                  className="action-row primary-row"
                  disabled={
                    busy || processing || page.extraction_supported === false
                  }
                  onClick={onExtract}
                >
                  <span className="action-icon primary">
                    {processing ? (
                      <Spinner size={15} />
                    ) : (
                      <ScanLine size={15} />
                    )}
                  </span>
                  <span className="action-copy">
                    <strong>Extract page</strong>
                    <span>
                      {processing
                        ? "Extraction is already running"
                        : page.extraction_supported === false
                          ? "This page has no extractable text layer"
                          : "Find tables automatically on this page"}
                    </span>
                  </span>
                  <ArrowRight className="icon" size={14} aria-hidden="true" />
                </button>
              </div>

              <p className="action-or">Or start another way</p>
              <div className="action-list secondary-list">
                <button
                  type="button"
                  className="action-row"
                  disabled={busy}
                  onClick={onRequestRegion}
                >
                  <span className="action-icon">
                    <Crop size={15} aria-hidden="true" />
                  </span>
                  <span className="action-copy">
                    <strong>Select region</strong>
                    <span>Draw around one table, or run OCR on a scan</span>
                  </span>
                  <ArrowRight className="icon" size={14} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="action-row"
                  disabled={busy}
                  onClick={onManual}
                >
                  <span className="action-icon">
                    <Grid2X2 size={15} aria-hidden="true" />
                  </span>
                  <span className="action-copy">
                    <strong>Create a blank table</strong>
                    <span>Type the rows and columns yourself</span>
                  </span>
                  <ArrowRight className="icon" size={14} aria-hidden="true" />
                </button>
              </div>

              {processing && (
                <p className="saved-note" role="status">
                  <Spinner size={12} />
                  {latest?.state === "queued"
                    ? "Extraction is queued for this page."
                    : "Extraction is running for this page."}{" "}
                  <button
                    type="button"
                    className="link-button"
                    onClick={onActivity}
                  >
                    Activity
                  </button>
                </p>
              )}

              <p className="saved-note">
                <CheckCircle2
                  className="icon"
                  size={12}
                  aria-hidden="true"
                  style={{ color: "var(--success)" }}
                />
                The original PDF stays unchanged. Extraction only creates a
                draft you can review.
              </p>

              {!!notes.length && !processing && (
                <details className="page-notes">
                  <summary>
                    <AlertTriangle size={14} aria-hidden="true" />
                    <span>{notes[0]}</span>
                    {notes.length > 1 && (
                      <span className="page-notes-count">
                        +{notes.length - 1}
                      </span>
                    )}
                  </summary>
                  <ul>
                    {notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}

          {!failed && !empty && (
            <>
              {processing && (
                <p className="saved-note" role="status">
                  <Spinner size={12} />
                  {latest?.state === "queued"
                    ? "Another extraction is queued for this page."
                    : "Another extraction is running for this page."}{" "}
                  <button
                    type="button"
                    className="link-button"
                    onClick={onActivity}
                  >
                    Activity
                  </button>
                </p>
              )}

              <p className="action-or">Add another table</p>
              <div className="action-list secondary-list">
                <button
                  type="button"
                  className="action-row"
                  disabled={
                    busy || processing || page.extraction_supported === false
                  }
                  onClick={onExtract}
                >
                  <span className="action-icon">
                    <ScanLine size={15} aria-hidden="true" />
                  </span>
                  <span className="action-copy">
                    <strong>Extract page again</strong>
                    <span>Look for tables that are still missing</span>
                  </span>
                  <ArrowRight className="icon" size={14} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="action-row"
                  disabled={busy}
                  onClick={onRequestRegion}
                >
                  <span className="action-icon">
                    <Crop size={15} aria-hidden="true" />
                  </span>
                  <span className="action-copy">
                    <strong>Select region</strong>
                    <span>Draw around one table, or run OCR on a scan</span>
                  </span>
                  <ArrowRight className="icon" size={14} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="action-row"
                  disabled={busy}
                  onClick={onManual}
                >
                  <span className="action-icon">
                    <Grid2X2 size={15} aria-hidden="true" />
                  </span>
                  <span className="action-copy">
                    <strong>Create a blank table</strong>
                    <span>Type the rows and columns yourself</span>
                  </span>
                  <ArrowRight className="icon" size={14} aria-hidden="true" />
                </button>
              </div>

              {!!notes.length && !processing && (
                <details className="page-notes">
                  <summary>
                    <AlertTriangle size={14} aria-hidden="true" />
                    <span>{notes[0]}</span>
                    {notes.length > 1 && (
                      <span className="page-notes-count">
                        +{notes.length - 1}
                      </span>
                    )}
                  </summary>
                  <ul>
                    {notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}

          {!!removed.length && (
            <details className="removed-tables">
              <summary>
                <Trash2 size={13} aria-hidden="true" />
                Removed tables ({removed.length})
              </summary>
              <p className="muted small">
                Restoring creates an unchecked draft. Reviews and approval must
                be renewed.
              </p>
              {removed.map((table, index) => (
                <div className="removed-row" key={table.id}>
                  <span>
                    Removed table {index + 1} ·{" "}
                    {table.origin === "manual" ? "Manual" : "Extracted"}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<RotateCcw size={13} />}
                    onClick={() => onRestore(table)}
                  >
                    Restore
                  </Button>
                </div>
              ))}
            </details>
          )}

          {page.extraction_supported === false && (
            <InlineNote tone="warning" icon={<AlertTriangle size={15} />}>
              <p>{page.unsupported_reason}</p>
            </InlineNote>
          )}
        </main>

        <aside className="pane-aside">
          <div className="aside-title">Page details</div>
          <div className="page-facts">
            <div className="fact">
              <span>Text layer</span>
              <b>{page.digital_text_present ? "Yes" : "No"}</b>
            </div>
            <div className="fact">
              <span>OCR</span>
              <b>{page.requires_ocr ? "Required" : "Not needed"}</b>
            </div>
            <div className="fact">
              <span>Size</span>
              <b className="mono">{pageSize(page)}</b>
            </div>
            <div className="fact">
              <span>Saved tables</span>
              <b>{tables.length}</b>
            </div>
            <div className="fact">
              <span>{failed ? "Last attempt" : "Last extraction"}</span>
              <b>{latest ? timeOf(latest.created_at) : "None"}</b>
            </div>
          </div>

          <div className="aside-block">
            <div className="aside-title">
              Recent
              {processing && <Spinner size={12} />}
            </div>
            {latest ? (
              <div className="recent-row">
                <span
                  className={`recent-dot ${
                    failed ? "failed" : processing ? "running" : "ok"
                  }`}
                />
                <div className="recent-copy">
                  <strong>
                    {failed
                      ? "Extraction stopped"
                      : processing
                        ? latest.state === "queued"
                          ? "Extraction queued"
                          : "Extracting this page"
                        : outcome === "partial_success"
                          ? "Extraction needs another look"
                          : tables.length
                            ? `${tables.length} table${tables.length === 1 ? "" : "s"} saved`
                            : "No tables found"}
                  </strong>
                  <span>
                    {timeOf(latest.created_at)}
                    {latest.attempt_count > 1
                      ? ` · ${latest.attempt_count} attempts`
                      : ""}
                  </span>
                </div>
              </div>
            ) : (
              <p className="empty-recent">No extraction on this page yet.</p>
            )}
            <button
              type="button"
              className="evidence-link"
              onClick={onActivity}
            >
              View activity
              <ArrowRight size={10} aria-hidden="true" />
            </button>
          </div>

          {latest && (
            <div className="run-detail">
              <div className="aside-title">This run</div>
              <div className="fact">
                <span>Cause</span>
                <b>
                  {failed
                    ? failureLabel(latest)
                    : processing
                      ? "In progress"
                      : "Completed"}
                </b>
              </div>
              <div className="fact">
                <span>Attempts</span>
                <b>{latest.attempt_count}</b>
              </div>
              <div className="fact">
                <span>Run ID</span>
                <b className="mono">{latest.id.slice(0, 8)}</b>
              </div>
              <button
                type="button"
                className="evidence-link"
                onClick={onActivity}
              >
                Run evidence
                <ArrowRight size={10} aria-hidden="true" />
              </button>
            </div>
          )}

          {removed.length > 0 && (
            <div className="aside-block">
              <div className="aside-title">
                Removed
                <span className="count">{removed.length}</span>
              </div>
              <p className="empty-recent">
                Removed tables can be restored. Reviews and approval start
                again.
              </p>
            </div>
          )}

          <p className="aside-foot">
            <Info size={11} aria-hidden="true" />
            Table records are pinned to the revision you review.
          </p>
        </aside>
      </div>
    </section>
  );
}
