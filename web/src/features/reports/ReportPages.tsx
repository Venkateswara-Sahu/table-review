import { useCallback, useEffect, useState } from "react";
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
import { SourcePage } from "../review/SourcePage";
import { ReviewScreen } from "../review/ReviewScreen";
import { useStudy } from "../review/StudyRecorder";

export function ReportPages({
  document,
  onBack,
  onDirty,
}: {
  document: Document;
  onBack: () => void;
  onDirty: (value: boolean) => void;
}) {
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
  const [pageInput, setPageInput] = useState(String(startPage));
  const [dirty, setDirty] = useState(false);
  const reportDirty = useCallback(
    (value: boolean) => {
      setDirty(value);
      onDirty(value);
    },
    [onDirty],
  );
  const leaveTable = () =>
    !dirty ||
    window.confirm("Discard unsaved changes before leaving this table?");
  const [pagesInput, setPagesInput] = useState("1");
  const [page, setPage] = useState<PageInfo | null>(null);
  const [tables, setTables] = useState<Table[]>([]);
  const [view, setView] = useState<TableView | null>(null);
  const [reference, setReference] = useState<SourceReference | null>(null);
  const [region, setRegion] = useState<PageBox | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState("2"),
    [columns, setColumns] = useState("3");
  const active = tasks.filter(
    (task) => task.state === "queued" || task.state === "running",
  );
  const study = useStudy();
  useEffect(() => {
    study?.processing(active.length > 0);
    return () => study?.processing(false);
  }, [study, active.length]);
  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setPage(null);
    setView(null);
    setReference(null);
    setTables([]);
    setRegion(null);
    setPagesInput(String(pageNo));
    apiRequest<PageInfo>(`/api/v1/documents/${document.id}/pages/${pageNo}`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (live) setPage(value);
      })
      .catch((error) => {
        if (live) setError(errorMessage(error));
      });
    async function refresh() {
      try {
        const [listed, queued] = await Promise.all([
          apiRequest<{ items: Table[] }>(
            `/api/v1/documents/${document.id}/tables?page_no=${pageNo}`,
            { signal: controller.signal },
          ),
          apiRequest<{ items: Task[] }>(
            `/api/v1/documents/${document.id}/tasks`,
            { signal: controller.signal },
          ),
        ]);
        if (live) {
          setTables(listed.items);
          setTasks(queued.items);
        }
      } catch (error) {
        if (live) setError(errorMessage(error));
      }
    }
    void refresh();
    const requested = new URLSearchParams(window.location.search).get("table");
    if (requested)
      apiRequest<TableView>(`/api/v1/tables/${encodeURIComponent(requested)}`, {
        signal: controller.signal,
      })
        .then((selected) => {
          if (!live) return;
          const sourcePage =
            selected.revision.content.source_reference?.page ??
            selected.revision.content.cells[0]?.reference.page;
          if (
            selected.table.document_id !== document.id ||
            sourcePage !== pageNo
          ) {
            setError("This table does not belong to the selected report page");
            return;
          }
          setView(selected);
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
  }, [document.id, pageNo]);
  async function openTable(table: Table) {
    if (!leaveTable()) return;
    setError("");
    try {
      const selected = await apiRequest<TableView>(
        `/api/v1/tables/${table.id}`,
      );
      setView(selected);
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
      setError(errorMessage(error));
    }
  }
  function changePage() {
    if (!leaveTable()) return;
    const next = Number(pageInput);
    if (!Number.isInteger(next) || next < 1 || next > document.page_count) {
      setError("Choose an original page number in this report");
      return;
    }
    setError("");
    setPageNo(next);
    window.history.replaceState(
      null,
      "",
      `/?document=${document.id}&page=${next}`,
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
  return (
    <main className="report-workspace">
      <button className="back-link" onClick={onBack}>
        <span aria-hidden="true">← </span>All reports
      </button>
      <div className="section-heading">
        <div>
          <p className="eyebrow">REPORT WORKSPACE</p>
          <h1>{document.filename}</h1>
          <p className="muted">
            {document.page_count} original pages · Choose the source before
            checking the table.
          </p>
        </div>
        <div className="page-controls">
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
          <button onClick={changePage}>View page</button>
        </div>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
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
          Select 1–5 pages. Reruns keep existing work.
        </p>
      </div>
      {page?.unsupported_reason && (
        <p role="status" className="warning">
          {page.unsupported_reason}
        </p>
      )}
      {active.map((task) => (
        <div className="job-row" key={task.id}>
          <span role="status">
            {task.kind === "prepare"
              ? "Preparing report"
              : "Extracting selected pages"}{" "}
            · {task.state}
          </span>
          <button onClick={() => void cancel(task)}>Cancel task</button>
        </div>
      ))}
      {tasks
        .filter((task) => task.state === "failed")
        .map((task) => (
          <p role="status" className="warning" key={task.id}>
            {task.failure_code === "unsupported_page"
              ? "A selected page has no usable digital text. Select another page or create a manual table."
              : "Processing stopped. Your saved work is retained; retry or select a region."}
          </p>
        ))}
      <div className="workspace-columns">
        <div>
          {page && (
            <SourcePage
              key={`${document.id}-${pageNo}`}
              documentId={document.id}
              page={page}
              reference={reference}
              tableBox={view?.revision.content.source_reference?.box}
              onRegion={setRegion}
            />
          )}
        </div>
        <section className="table-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">TABLES / PAGE {pageNo}</p>
              <h2>{view ? "Selected table" : "Choose a table"}</h2>
            </div>
            <span className="count">{tables.length}</span>
          </div>
          <div className="table-choices">
            {tables.map((table, index) => (
              <button
                key={table.id}
                aria-pressed={view?.table.id === table.id}
                onClick={() => void openTable(table)}
              >
                Open table {index + 1}
              </button>
            ))}
          </div>
          {tables.length === 0 && (
            <div className="empty">
              <h3>No tables on this page</h3>
              <p>
                Select a different page, extract this page or draw a region
                around a missed table.
              </p>
            </div>
          )}
          {view && (
            <ReviewScreen
              key={view.table.id}
              view={view}
              onSource={setReference}
              onDirty={reportDirty}
              onRevision={(revision) =>
                setView((value) => (value ? { ...value, revision } : value))
              }
            />
          )}
          {region && (
            <div className="manual-panel">
              <h3>Selected region</h3>
              <p>
                Page {region.page} · {Math.round(region.right - region.left)} ×{" "}
                {Math.round(region.bottom - region.top)} points
              </p>
              <button
                disabled={busy || page?.extraction_supported === false}
                onClick={() => void extract(region)}
              >
                Extract selected region
              </button>
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
                Manual cells retain your region reference. They have no detected
                cell location.
              </p>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
