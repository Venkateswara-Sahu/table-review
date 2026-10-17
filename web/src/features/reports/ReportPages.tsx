import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiRequest, errorMessage } from "../../api/client";
import type {
  Document,
  PageInfo,
  Table,
  TableView,
  SourceReference,
  PageBox,
  Task,
} from "../../api/models";
import { SourceReader } from "../review/SourceReader";
import { PdfPreviews } from "../review/PdfPreviews";
import { ReviewScreen } from "../review/ReviewScreen";
import { useStudy } from "../review/StudyRecorder";
import { ExtractionHistory } from "./ExtractionHistory";

export function ReportPages({
  document,
  onBack,
  onDirty,
}: {
  document: Document;
  onBack: () => void;
  onDirty: (value: boolean) => void;
}) {
  const previews = useMemo(() => new PdfPreviews(document.id), [document.id]);
  useEffect(() => () => previews.dispose(), [previews]);
  const initialPage = Number(
    new URLSearchParams(window.location.search).get("page") ?? "1",
  );
  const startPage =
    Number.isInteger(initialPage) &&
    initialPage >= 1 &&
    initialPage <= document.page_count
      ? initialPage
      : 1;
  const [pageNo, setPageNo] = useState(startPage);
  const selectedPage = useRef(pageNo);
  selectedPage.current = pageNo;
  const [jump, setJump] = useState({ page: startPage, sequence: 0 });
  const [sourceSelection, setSourceSelection] = useState<{
    id: string;
    sequence: number;
  } | null>(null);
  const [mobilePane, setMobilePane] = useState("source");
  const [pageInput, setPageInput] = useState(String(startPage));
  const [dirty, setDirty] = useState(false);
  const dirtyNow = useRef(false);
  const navigationRequest = useRef(0);
  const reportDirty = useCallback(
    (value: boolean) => {
      dirtyNow.current = value;
      setDirty(value);
      onDirty(value);
    },
    [onDirty],
  );
  const leaveTable = () =>
    !dirtyNow.current ||
    window.confirm("Discard unsaved changes before leaving this table?");
  const [pagesInput, setPagesInput] = useState("1");
  const [page, setPage] = useState<PageInfo | null>(null);
  const [tables, setTables] = useState<Table[]>([]);
  const [removedTables, setRemovedTables] = useState<Table[]>([]);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [view, setView] = useState<TableView | null>(null);
  const [adoptionGeneration, setAdoptionGeneration] = useState(0);
  const activeTableId = useRef(view?.table.id);
  activeTableId.current = view?.table.id;
  const [reference, setReference] = useState<SourceReference | null>(null);
  const [region, setRegion] = useState<PageBox | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState("2"),
    [columns, setColumns] = useState("3");
  const active = tasks.filter(
    (task) => task.state === "queued" || task.state === "running",
  );
  const latestExtraction = tasks
    .filter(
      (task) =>
        task.kind === "extract" &&
        Array.isArray(task.input.pages) &&
        task.input.pages.includes(pageNo),
    )
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const pageProcessing =
    latestExtraction?.state === "queued" ||
    latestExtraction?.state === "running";
  const emptyState =
    latestExtraction?.state === "queued"
      ? {
          title: "Extraction queued for this page",
          detail:
            "Your request is waiting to start. You can keep reading the PDF.",
        }
      : latestExtraction?.state === "running"
        ? {
            title: "Extracting this page",
            detail: "You can keep reading while the draft is prepared.",
          }
        : latestExtraction?.state === "failed" ||
            latestExtraction?.state === "cancelled"
          ? {
              title: "Extraction stopped on this page",
              detail:
                "Retry this page or select a table region on the source. Your saved work is retained.",
            }
          : latestExtraction?.result.conversion_outcome === "partial_success"
            ? {
                title: "Extraction needs another look",
                detail:
                  "Processing was incomplete. Retry this page or select a table region on the source.",
              }
            : latestExtraction?.state === "succeeded" &&
                latestExtraction.result.conversion_outcome === "success"
              ? latestExtraction.input.region
                ? {
                    title: "No tables found in the selected region",
                    detail: "Select another region or extract the full page.",
                  }
                : {
                    title: "No tables found on this page",
                    detail:
                      "If you can see a table in the PDF, select its region to try again.",
                  }
              : {
                  title: "Ready to extract this page",
                  detail:
                    "Extract the page to create an editable draft, or select a table region on the source.",
                };
  const recoveryWarnings = Array.isArray(latestExtraction?.result.warnings)
    ? latestExtraction.result.warnings.filter(
        (warning): warning is string =>
          typeof warning === "string" &&
          warning.startsWith(`Page ${pageNo}:`) &&
          (warning.includes("OCR omitted") ||
            warning.includes("Parser omitted") ||
            warning.includes("region retry") ||
            warning.includes("table recovered automatically") ||
            warning.includes("isolated dash marks") ||
            warning.includes("optional mark recovery") ||
            warning.includes("currency markers regrouped") ||
            warning.includes("OCR currency reconciliation")),
      )
    : [];
  const study = useStudy();
  useEffect(() => {
    study?.processing(active.length > 0);
    return () => study?.processing(false);
  }, [study, active.length]);
  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setError("");
    setTables([]);
    setRemovedTables([]);
    setRegion(null);
    setPagesInput(String(pageNo));
    previews
      .metadata(pageNo)
      .then((value) => {
        if (live) setPage(value);
      })
      .catch((error) => {
        if (live) setError(errorMessage(error));
      });
    async function refresh() {
      try {
        const [listed, queued, removed] = await Promise.all([
          apiRequest<{ items: Table[] }>(
            `/api/v1/documents/${document.id}/tables?page_no=${pageNo}`,
            { signal: controller.signal },
          ),
          apiRequest<{ items: Task[] }>(
            `/api/v1/documents/${document.id}/tasks`,
            { signal: controller.signal },
          ),
          apiRequest<{ items: Table[] }>(
            `/api/v1/documents/${document.id}/tables?page_no=${pageNo}&removed=true`,
            { signal: controller.signal },
          ),
        ]);
        if (live) {
          setTables(listed.items);
          setTasks(queued.items);
          setRemovedTables(removed.items);
        }
      } catch (error) {
        if (live) setError(errorMessage(error));
      }
    }
    void refresh();
    const requested =
      !view && new URLSearchParams(window.location.search).get("table");
    const initialRequest = navigationRequest.current;
    if (requested)
      apiRequest<TableView>(`/api/v1/tables/${encodeURIComponent(requested)}`, {
        signal: controller.signal,
      })
        .then((selected) => {
          if (!live || navigationRequest.current !== initialRequest) return;
          if (selected.table.document_id !== document.id) {
            setError("This table does not belong to the selected report page");
            return;
          }
          setView(selected);
          setMobilePane("review");
          setReference(
            selected.revision.content.source_reference ??
              selected.revision.content.cells[0]?.reference ??
              null,
          );
        })
        .catch((error) => {
          if (live) setError(errorMessage(error));
        });
    const interval = window.setInterval(() => void refresh(), 1500);
    return () => {
      live = false;
      controller.abort();
      window.clearInterval(interval);
    };
  }, [document.id, pageNo, previews]);
  async function openTable(table: Pick<Table, "id">, recovery = false) {
    const request = ++navigationRequest.current;
    const requestedPage = selectedPage.current;
    setError("");
    try {
      const selected = await apiRequest<TableView>(
        `/api/v1/tables/${table.id}`,
      );
      if (
        request !== navigationRequest.current ||
        requestedPage !== selectedPage.current
      )
        return;
      if (recovery && dirtyNow.current) {
        setNotice(
          "Table restored. Your current edits were kept; open the restored table when ready.",
        );
        return;
      }
      if (!leaveTable()) return;
      setNotice("");
      setView(selected);
      setMobilePane("review");
      window.history.replaceState(
        null,
        "",
        `/?document=${document.id}&page=${pageNo}&table=${table.id}`,
      );
      setReference(
        selected.revision.content.source_reference ??
          selected.revision.content.cells[0]?.reference ??
          null,
      );
    } catch (error) {
      if (
        request === navigationRequest.current &&
        requestedPage === selectedPage.current
      )
        setError(errorMessage(error));
    }
  }
  function beginAdoption() {
    if (dirtyNow.current) return null;
    const request = navigationRequest.current;
    const requestedPage = selectedPage.current;
    return async (tableId: string) => {
      const changed = () =>
        request !== navigationRequest.current ||
        requestedPage !== selectedPage.current ||
        dirtyNow.current;
      const retained = () =>
        setNotice(
          "Replacement saved. Your current navigation and edits were kept; reopen the earlier table to review its new draft.",
        );
      if (changed()) {
        retained();
        return;
      }
      const selected = await apiRequest<TableView>(`/api/v1/tables/${tableId}`);
      if (changed()) {
        retained();
        return;
      }
      setView(selected);
      setAdoptionGeneration((value) => value + 1);
      setSourceSelection(null);
      setMobilePane("review");
      setReference(selected.revision.content.source_reference ?? null);
      setNotice(
        "Replacement saved as an unchecked draft. Earlier corrections remain in History.",
      );
      window.history.replaceState(
        null,
        "",
        `/?document=${document.id}&page=${pageNo}&table=${tableId}`,
      );
    };
  }
  async function restoreTable(table: Table) {
    if (restoring || busy || !leaveTable()) return;
    const requestedPage = pageNo;
    const requestedSelection = activeTableId.current;
    const requestedNavigation = navigationRequest.current;
    setRestoring(table.id);
    setError("");
    setNotice("");
    try {
      const restored = await apiRequest<Table>(
        `/api/v1/tables/${table.id}/removal`,
        {
          method: "POST",
          body: JSON.stringify({
            removed: false,
            base_revision_id: table.current_revision_id,
            expected_version: table.removal_version,
          }),
        },
      );
      if (selectedPage.current !== requestedPage) return;
      setRemovedTables((value) => value.filter((item) => item.id !== table.id));
      setTables((value) => [
        restored,
        ...value.filter((item) => item.id !== table.id),
      ]);
      if (
        activeTableId.current !== requestedSelection ||
        navigationRequest.current !== requestedNavigation ||
        dirtyNow.current
      ) {
        setNotice(
          "Table restored. Your current edits were kept; open the restored table when ready.",
        );
        return;
      }
      await openTable(restored, true);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setRestoring(null);
    }
  }
  function changePage(next = Number(pageInput)) {
    if (!Number.isInteger(next) || next < 1 || next > document.page_count) {
      setError("Choose an original page number in this report");
      return;
    }
    setError("");
    navigationRequest.current++;
    setPageInput(String(next));
    setPageNo(next);
    setJump((value) => ({ page: next, sequence: value.sequence + 1 }));
    window.history.replaceState(
      null,
      "",
      `/?document=${document.id}&page=${next}${view ? `&table=${view.table.id}` : ""}`,
    );
  }
  async function extract(selectedRegion?: PageBox) {
    setError("");
    const pages = selectedRegion
      ? [selectedRegion.page]
      : pagesInput.split(",").map((value) => Number(value.trim()));
    if (
      pages.length < 1 ||
      pages.length > 5 ||
      pages.some(
        (value) =>
          !Number.isInteger(value) || value < 1 || value > document.page_count,
      ) ||
      new Set(pages).size !== pages.length
    ) {
      setError(
        "Choose 1 to 5 distinct original page numbers, separated by commas",
      );
      return;
    }
    setBusy(true);
    try {
      const task = await apiRequest<Task>(
        `/api/v1/documents/${document.id}/runs`,
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({ pages, region: selectedRegion ?? null }),
        },
      );
      setTasks((value) => [task, ...value]);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function cancel(task: Task) {
    try {
      const updated = await apiRequest<Task>(
        `/api/v1/tasks/${task.id}/cancel`,
        { method: "POST" },
      );
      setTasks((value) =>
        value.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (error) {
      setError(errorMessage(error));
    }
  }
  async function createManual() {
    if (!leaveTable()) return;
    if (!region) return;
    setBusy(true);
    setError("");
    try {
      const table = await apiRequest<Table>(
        `/api/v1/documents/${document.id}/tables`,
        {
          method: "POST",
          body: JSON.stringify({
            region,
            rows: Number(rows),
            columns: Number(columns),
          }),
        },
      );
      setTables((value) => [table, ...value]);
      await openTable(table);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  const observePage = useCallback((number: number) => {
    if (number === selectedPage.current) return;
    navigationRequest.current++;
    setPageNo(number);
    setPageInput(String(number));
  }, []);
  function showSource(value: SourceReference) {
    setReference(value);
    changePage(value.page);
  }
  const tablePage =
    view?.revision.content.source_reference?.page ??
    view?.revision.content.cells[0]?.reference.page;
  return (
    <main className="report-workspace" data-mobile-pane={mobilePane}>
      <div className="workspace-heading">
        <button className="back-link" onClick={onBack}>
          All reports
        </button>
        <h1 title={document.filename}>{document.filename}</h1>
        <span>{document.page_count} pages</span>
        <div className="mobile-pane-switch" aria-label="Workspace view">
          <button
            aria-pressed={mobilePane === "source"}
            onClick={() => setMobilePane("source")}
          >
            Source
          </button>
          <button
            aria-pressed={mobilePane === "review"}
            onClick={() => setMobilePane("review")}
          >
            Review
          </button>
        </div>
      </div>
      <div className="workspace-commandbar">
        <form
          className="page-controls"
          onSubmit={(event) => {
            event.preventDefault();
            changePage();
          }}
        >
          <button
            type="button"
            aria-label="Previous page"
            disabled={pageNo === 1}
            onClick={() => changePage(pageNo - 1)}
          >
            Previous
          </button>
          <label>
            Original page
            <input
              type="number"
              min={1}
              max={document.page_count}
              value={pageInput}
              onChange={(event) => setPageInput(event.target.value)}
            />
          </label>
          <span>of {document.page_count}</span>
          <button type="submit">View page</button>
          <button
            type="button"
            aria-label="Next page"
            disabled={pageNo === document.page_count}
            onClick={() => changePage(pageNo + 1)}
          >
            Next
          </button>
        </form>
        <details className="extract-menu">
          <summary>Extract tables</summary>
          <div className="processing-bar">
            <label>
              Pages to extract
              <input
                value={pagesInput}
                onChange={(event) => setPagesInput(event.target.value)}
                placeholder="e.g. 2, 4"
              />
            </label>
            <button
              className="primary"
              disabled={busy || page?.extraction_supported === false}
              onClick={() => void extract()}
            >
              Extract selected pages
            </button>
            <p className="fine-print">
              Choose 1–5 pages. Existing tables and edits are kept.
            </p>
          </div>
        </details>
      </div>
      {!study && (
        <ExtractionHistory
          documentId={document.id}
          refreshKey={tasks.map((task) => `${task.id}:${task.state}`).join(",")}
          onOpen={(id) => void openTable({ id })}
          adoption={{ blocked: dirty, begin: beginAdoption }}
        />
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="context-note">
          {notice}
        </p>
      )}
      {page?.unsupported_reason && (
        <p role="status" className="warning">
          {page.unsupported_reason}
        </p>
      )}
      {page?.requires_ocr && (
        <p role="status" className="warning">
          This page uses offline OCR. Check extracted figures against the page
          image.
        </p>
      )}
      {page?.mixed_image_content && (
        <p role="status" className="warning">
          This page combines PDF text and images. If an image table is missing,
          choose Select region, draw around the table, then Extract selected
          region to read it with offline OCR.
        </p>
      )}
      {recoveryWarnings.map((warning) => (
        <p role="status" className="warning" key={warning}>
          {warning}
        </p>
      ))}
      {active.map((task) => (
        <div className="job-row" key={task.id}>
          <span role="status">
            {task.kind === "prepare"
              ? "Preparing report"
              : page?.requires_ocr
                ? "Reading page images with offline OCR"
                : "Extracting selected pages"}{" "}
            · {task.state}
          </span>
          <button onClick={() => void cancel(task)}>Cancel task</button>
        </div>
      ))}
      {latestExtraction?.state === "failed" && (
        <p role="status" className="warning">
          {latestExtraction.failure_code === "unsupported_page"
            ? "A selected page has no usable digital text. Select another page or create a manual table."
            : latestExtraction.failure_code === "ocr_unavailable"
              ? "Offline OCR models are unavailable. Your saved work is retained."
              : `Extraction failed for page ${pageNo}. Your saved work is retained. See Extraction history for details, or retry.`}
        </p>
      )}
      <div className="workspace-columns">
        <div className="source-workspace">
          {page ? (
            <SourceReader
              documentId={document.id}
              previews={previews}
              currentPage={pageNo}
              page={page}
              pageCount={document.page_count}
              reference={reference}
              cells={view?.revision.content.cells ?? []}
              jump={jump}
              onPage={observePage}
              onCell={(id) => {
                setSourceSelection((value) => ({
                  id,
                  sequence: (value?.sequence ?? 0) + 1,
                }));
                setMobilePane("review");
              }}
              onRegion={setRegion}
              selection={region}
            />
          ) : (
            <div className="empty" role="status">
              Opening original document…
            </div>
          )}
          {region && (
            <section
              className="manual-panel"
              aria-label="Selected region actions"
            >
              <div>
                <h3>Selected region</h3>
                <p className="fine-print">
                  Page {region.page}. Extract values or create an explicitly
                  blank grid.
                </p>
              </div>
              <button
                className="primary"
                disabled={busy}
                onClick={() => void extract(region)}
              >
                Extract selected region
              </button>
              <details>
                <summary>Create a manual table</summary>
                <div className="manual-dimensions">
                  <label>
                    Manual rows
                    <input
                      type="number"
                      min={1}
                      max={500}
                      value={rows}
                      onChange={(event) => setRows(event.target.value)}
                    />
                  </label>
                  <label>
                    Manual columns
                    <input
                      type="number"
                      min={1}
                      max={60}
                      value={columns}
                      onChange={(event) => setColumns(event.target.value)}
                    />
                  </label>
                </div>
                <button disabled={busy} onClick={() => void createManual()}>
                  Create manual table
                </button>
                <p className="fine-print">
                  No values are extracted. Cells reference this area; exact cell
                  locations are unavailable.
                </p>
              </details>
              <button onClick={() => setRegion(null)}>
                Dismiss region actions
              </button>
            </section>
          )}
        </div>
        <section className="table-panel" aria-label="Table workspace">
          <div className="table-selector">
            <h2>Tables on page {pageNo}</h2>
            <span className="count">{tables.length}</span>
            <div className="table-choices">
              {tables.map((table, index) => (
                <button
                  key={table.id}
                  aria-label={`Open table ${index + 1}`}
                  aria-pressed={view?.table.id === table.id}
                  onClick={() => void openTable(table)}
                >
                  Table {index + 1}
                  <small>
                    {table.origin === "manual" ? "Manual" : "Extracted"}
                    {view?.table.id === table.id
                      ? ` · ${view.revision.content.rows} × ${view.revision.content.columns}`
                      : ""}
                  </small>
                </button>
              ))}
            </div>
          </div>
          {!study && (
            <details className="removed-tables">
              <summary>Removed tables</summary>
              <p className="fine-print">
                Removed tables on this page can be restored while this report is
                retained. Cell reviews and approval must be renewed. Deleting or
                expiring the report removes them.
              </p>
              {!removedTables.length && <p>No removed tables on this page.</p>}
              {removedTables.map((table, index) => (
                <div className="removed-table-row" key={table.id}>
                  <span>
                    Removed table {index + 1} ·{" "}
                    {table.origin === "manual" ? "Manual" : "Extracted"} ·{" "}
                    {table.removed_at
                      ? new Date(table.removed_at).toLocaleString()
                      : ""}
                  </span>
                  <button
                    disabled={!!restoring || busy || dirty}
                    onClick={() => void restoreTable(table)}
                  >
                    {restoring === table.id ? "Restoring…" : "Restore table"}
                  </button>
                </div>
              ))}
            </details>
          )}
          {tablePage && tablePage !== pageNo && (
            <div className="context-note">
              You're reading page {pageNo}. This table belongs to page{" "}
              {tablePage}.
              <button
                aria-label="Return to table source"
                onClick={() => changePage(tablePage)}
              >
                Return to table source
              </button>
            </div>
          )}
          {!view && (
            <div className="empty table-empty">
              <h3>
                {tables.length
                  ? "Choose a table to review"
                  : removedTables.length
                    ? "No active tables on this page"
                    : emptyState.title}
              </h3>
              <p>
                {tables.length
                  ? "Open a table above to compare its values with the original."
                  : removedTables.length
                    ? "Open Removed tables above to restore a table, or extract this page again."
                    : emptyState.detail}
              </p>
              <button
                disabled={
                  busy || pageProcessing || page?.extraction_supported === false
                }
                onClick={() => void extract()}
              >
                Extract this page
              </button>
            </div>
          )}
          {view && (
            <ReviewScreen
              key={`${view.table.id}/${adoptionGeneration}`}
              view={view}
              sourceSelection={sourceSelection}
              onSource={showSource}
              onDirty={reportDirty}
              onRemoved={(table) => {
                if (activeTableId.current !== table.id) return;
                navigationRequest.current++;
                const sourcePage = tablePage ?? pageNo;
                setView((current) =>
                  current?.table.id === table.id ? null : current,
                );
                setTables((current) =>
                  current.filter((item) => item.id !== table.id),
                );
                if (sourcePage === pageNo)
                  setRemovedTables((current) => [
                    table,
                    ...current.filter((item) => item.id !== table.id),
                  ]);
                setReference(null);
                setSourceSelection(null);
                if (sourcePage !== pageNo) changePage(sourcePage);
                window.history.replaceState(
                  null,
                  "",
                  `/?document=${document.id}&page=${sourcePage}`,
                );
              }}
              onRevision={(revision) =>
                setView((value) => (value ? { ...value, revision } : value))
              }
            />
          )}
        </section>
      </div>
    </main>
  );
}
