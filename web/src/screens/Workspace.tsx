import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  FilePlus2,
  FileText,
  Grid2X2,
  ScanLine,
  Sparkles,
} from "lucide-react";
import { apiRequest, errorMessage } from "../api/client";
import {
  queryKeys,
  useDocument,
  useOpenDocument,
  usePage,
  useTables,
  useTasks,
  useTableView,
} from "../api/hooks";
import {
  type PageBox,
  type SourceReference,
  type Table,
  type TableView,
} from "../api/models";
import { useNotices } from "../app/notices";
import {
  Badge,
  Button,
  EmptyState,
  IconButton,
  InlineNote,
  Input,
  Spinner,
} from "../ui/primitives";
import { ActivityDrawer } from "../features/source/ActivityDrawer";
import { ExtractDialog } from "../features/source/ExtractDialog";
import { ManualTableDialog } from "../features/source/ManualTableDialog";
import { PdfPreviews } from "../features/source/PdfPreviews";
import { SourcePane } from "../features/source/SourcePane";
import { TablePanel } from "../features/table/TablePanel";
import { ReviewPane } from "../features/review/ReviewPane";
import { useStudy } from "../features/study/StudyRecorder";
import { BootScreen } from "../ui/BootScreen";
import { NotFoundScreen } from "./NotFound";
import { queryClient } from "../app/query";

function firstCell(view: TableView) {
  return view.revision.content.cells.reduce<
    TableView["revision"]["content"]["cells"][number] | undefined
  >(
    (first, cell) =>
      !first ||
      cell.row < first.row ||
      (cell.row === first.row && cell.col < first.col)
        ? cell
        : first,
    undefined,
  );
}

export function WorkspaceScreen() {
  const params = useParams<{
    documentId: string;
    pageNo?: string;
    tableId?: string;
  }>();
  const navigate = useNavigate();
  const { notify, notifyError } = useNotices();
  const documentId = params.documentId ?? "";
  const pageNo = Number(params.pageNo ?? 1);
  const tableId = params.tableId;
  const documentQuery = useDocument(documentId);
  const pageQuery = usePage(documentId, pageNo);
  const tasksQuery = useTasks(documentId);
  const tablesQuery = useTables(documentId, pageNo, false, false);
  const removedQuery = useTables(documentId, pageNo, true);
  const viewQuery = useTableView(tableId);
  const openDocument = useOpenDocument();
  const view = tableId ? viewQuery.data : undefined;
  const study = useStudy();
  const processing = (tasksQuery.data?.items ?? []).some(
    (task) => task.state === "queued" || task.state === "running",
  );
  const tables = tablesQuery.data?.items ?? [];

  const opened = useRef<string | null>(null);
  useEffect(() => {
    if (!documentId || opened.current === documentId) return;
    opened.current = documentId;
    openDocument.mutate(documentId);
    // Opening once per document keeps the retention window honest.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  const previews = useMemo(() => new PdfPreviews(documentId), [documentId]);
  useEffect(() => () => previews.dispose(), [previews]);

  // A trial pins the assigned original extraction. The recorder rejects a
  // mismatched checksum or a jump to another table.
  useEffect(() => {
    if (!study || !view) return;
    try {
      study.observe(view.revision);
    } catch (error) {
      notifyError(error);
    }
  }, [study, view?.revision.id, view, notifyError]);

  // The full-task clock is monotonic; only processing time is separated out.
  useEffect(() => {
    study?.processing(processing);
    return () => study?.processing(false);
  }, [study, processing]);

  const [reference, setReference] = useState<{
    reference: SourceReference;
    label?: string;
  } | null>(null);
  const [sourceSelection, setSourceSelection] = useState<{
    id: string;
    sequence: number;
  } | null>(null);
  const [region, setRegion] = useState<PageBox | null>(null);
  const [selectRequest, setSelectRequest] = useState<{
    sequence: number;
  } | null>(null);
  const [jump, setJump] = useState({ page: pageNo, sequence: 0 });
  const [extractOpen, setExtractOpen] = useState(false);
  const [extractRegion, setExtractRegion] = useState<PageBox | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [reviewDirty, setReviewDirty] = useState(false);
  const [pageInput, setPageInput] = useState(String(pageNo));
  const [mobilePane, setMobilePane] = useState<"source" | "table">(
    tableId ? "table" : "source",
  );

  useEffect(() => {
    setPageInput(String(pageNo));
    setJump((value) => ({ page: pageNo, sequence: value.sequence + 1 }));
    setRegion(null);
  }, [pageNo]);

  useEffect(() => {
    if (!reference) return;
    if (reference.reference.page === pageNo) return;
    setJump((value) => ({
      page: reference.reference.page,
      sequence: value.sequence + 1,
    }));
  }, [reference, pageNo]);

  useEffect(() => {
    if (!view) return;
    setMobilePane("table");
    setReviewDirty(false);
    const first = firstCell(view);
    if (first)
      setReference({
        reference: first.reference,
        label: `${first.row + 1}:${first.col + 1}`,
      });
  }, [view?.table.id]);

  const previousProcessing = useRef(processing);
  useEffect(() => {
    if (previousProcessing.current && !processing) {
      void queryClient.invalidateQueries({
        queryKey: ["tables", documentId],
      });
      void queryClient.invalidateQueries({ queryKey: ["runs", documentId] });
      void queryClient.invalidateQueries({
        queryKey: ["tasks", documentId],
      });
    }
    previousProcessing.current = processing;
  }, [processing, documentId]);

  useEffect(() => {
    if (!documentQuery.data) return;
    window.document.title = `${documentQuery.data.filename} · Table Review`;
    return () => {
      window.document.title = "Table Review";
    };
  }, [documentQuery.data]);

  const onRegion = useCallback((box: PageBox | null) => {
    setRegion(box);
  }, []);

  const requestRegion = useCallback(() => {
    setSelectRequest((value) => ({ sequence: (value?.sequence ?? 0) + 1 }));
  }, []);

  const leaveReview = useCallback(() => {
    if (!reviewDirty) return true;
    notify(
      "Save or discard the open edit before leaving this table.",
      "warning",
    );
    return false;
  }, [reviewDirty, notify]);

  const openTable = useCallback(
    (id: string) => {
      if (!leaveReview()) return;
      navigate(`/reports/${documentId}/pages/${pageNo}/tables/${id}`);
    },
    [navigate, documentId, pageNo, leaveReview],
  );

  function changePage(nextPage: number) {
    if (!documentQuery.data || !leaveReview()) return;
    const clamped = Math.max(
      1,
      Math.min(
        documentQuery.data.page_count,
        Number.isFinite(nextPage) ? nextPage : 1,
      ),
    );
    navigate(`/reports/${documentId}/pages/${clamped}`);
  }

  async function restoreTable(table: Table) {
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
      void queryClient.invalidateQueries({
        queryKey: ["tables", documentId, pageNo],
      });
      notify("Table restored. Open it to review the new draft.", "success");
      openTable(restored.id);
    } catch (error) {
      notifyError(error);
    }
  }

  function onExtractPage() {
    setExtractRegion(null);
    setExtractOpen(true);
  }

  function onExtractSelection() {
    if (!region) return;
    setExtractRegion(region);
    setExtractOpen(true);
  }

  function onManualSelection() {
    setManualOpen(true);
  }

  function onManualCreated(table: Table) {
    setRegion(null);
    void queryClient.invalidateQueries({
      queryKey: ["tables", documentId, pageNo],
    });
    openTable(table.id);
  }

  function onRevision() {
    void queryClient.invalidateQueries({ queryKey: ["table", tableId] });
  }

  if (documentQuery.isError) {
    return (
      <main className="page page-narrow">
        <InlineNote tone="danger" title="This report could not be opened">
          <p>{documentQuery.error.message}</p>
          <Link to="/" className="button button-secondary button-md">
            Back to reports
          </Link>
        </InlineNote>
      </main>
    );
  }

  if (documentQuery.isPending || pageQuery.isPending) return <BootScreen />;
  if (!documentQuery.data || !pageQuery.data) return <NotFoundScreen />;
  if (pageNo < 1 || pageNo > documentQuery.data.page_count)
    return <NotFoundScreen />;

  const report = documentQuery.data;
  const page = pageQuery.data;
  const latestTask = [...(tasksQuery.data?.items ?? [])]
    .filter((task) => {
      const pages = Array.isArray(task.input.pages)
        ? (task.input.pages as number[])
        : [];
      const taskRegion = task.input.region as { page?: number } | null;
      return pages.includes(pageNo) || taskRegion?.page === pageNo;
    })
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    )[0];
  const selectedTableIndex = tables.findIndex((item) => item.id === tableId);

  if (tableId && viewQuery.isError) {
    return (
      <main className="page page-narrow">
        <EmptyState
          title="This table is not available"
          icon={<Grid2X2 size={26} />}
          actions={
            <Link
              to={`/reports/${documentId}/pages/${pageNo}`}
              className="button button-secondary button-md"
            >
              Back to page {pageNo}
            </Link>
          }
        >
          <p className="muted">
            It may have been removed, or the link may be incomplete.{" "}
            {viewQuery.error.message}
          </p>
        </EmptyState>
      </main>
    );
  }

  if (tableId && viewQuery.isPending) return <BootScreen />;

  return (
    <main className={`workspace mobile-${mobilePane}`}>
      <header className="docbar">
        <div className="docbar-main">
          <Link
            to="/"
            className="docbar-back"
            aria-label="Back to reports"
            onClick={(event) => {
              if (!leaveReview()) event.preventDefault();
            }}
          >
            <ChevronLeft size={17} />
          </Link>
          <span className="docbar-file" aria-hidden="true">
            <FileText size={17} />
          </span>
          <div className="docbar-title">
            <h1 title={report.filename}>{report.filename}</h1>
            <p>
              <span className="num">{report.page_count}</span> pages
              <span className="dot-sep" />
              <Badge
                tone={
                  report.preparation_state === "ready"
                    ? "success"
                    : report.preparation_state === "failed"
                      ? "danger"
                      : "warning"
                }
                dot
                className="badge-mini"
              >
                {report.preparation_state === "ready"
                  ? "Ready"
                  : report.preparation_state === "failed"
                    ? "Needs attention"
                    : "Preparing"}
              </Badge>
            </p>
          </div>
        </div>
        <div className="docbar-actions">
          <Button
            size="sm"
            variant="ghost"
            icon={processing ? <Spinner size={14} /> : <Activity size={15} />}
            onClick={() => setActivityOpen(true)}
          >
            Activity
            {!!tasksQuery.data?.items.filter(
              (task) => task.state === "queued" || task.state === "running",
            ).length && (
              <span className="button-count">
                {
                  tasksQuery.data.items.filter(
                    (task) =>
                      task.state === "queued" || task.state === "running",
                  ).length
                }
              </span>
            )}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            icon={<ScanLine size={15} />}
            disabled={page.extraction_supported === false || processing}
            onClick={onExtractPage}
          >
            Extract page
          </Button>
        </div>
      </header>

      <div className="workspace-bar">
        <div className="page-stepper" aria-label="Page navigation">
          <IconButton
            label="Previous page"
            size="sm"
            disabled={pageNo <= 1}
            onClick={() => changePage(pageNo - 1)}
          >
            <ChevronLeft size={15} />
          </IconButton>
          <form
            className="page-jump"
            onSubmit={(event) => {
              event.preventDefault();
              changePage(Number(pageInput));
            }}
          >
            <span className="sr-only">Page number</span>
            <Input
              aria-label="Page number"
              inputMode="numeric"
              value={pageInput}
              onChange={(event) => setPageInput(event.target.value)}
              onBlur={() => changePage(Number(pageInput))}
            />
            <span className="page-total num">/ {report.page_count}</span>
          </form>
          <IconButton
            label="Next page"
            size="sm"
            disabled={pageNo >= report.page_count}
            onClick={() => changePage(pageNo + 1)}
          >
            <ChevronRight size={15} />
          </IconButton>
        </div>

        <div
          className="workspace-tabs"
          role="tablist"
          aria-label="Tables on this page"
        >
          <button
            type="button"
            role="tab"
            aria-selected={!tableId}
            className={!tableId ? "workspace-tab active" : "workspace-tab"}
            onClick={() => {
              if (!leaveReview()) return;
              navigate(`/reports/${documentId}/pages/${pageNo}`);
            }}
          >
            Page overview
          </button>
          {tables.map((table, index) => (
            <button
              key={table.id}
              type="button"
              role="tab"
              aria-selected={tableId === table.id}
              className={
                tableId === table.id ? "workspace-tab active" : "workspace-tab"
              }
              onClick={() => openTable(table.id)}
            >
              Table {index + 1}
            </button>
          ))}
          {tableId && selectedTableIndex < 0 && (
            <button
              type="button"
              role="tab"
              aria-selected
              className="workspace-tab active"
            >
              {view?.table.origin === "manual" ? "Manual table" : "Table"}
            </button>
          )}
          <button
            type="button"
            className="workspace-tab workspace-tab-add"
            aria-label="Create a manual table"
            onClick={() => {
              requestRegion();
              setManualOpen(true);
              setMobilePane("source");
            }}
          >
            <FilePlus2 size={14} />
          </button>
        </div>

        <div
          className="workspace-mobile-toggle"
          role="group"
          aria-label="Workspace view"
        >
          <button
            type="button"
            aria-pressed={mobilePane === "source"}
            onClick={() => setMobilePane("source")}
          >
            Source
          </button>
          <button
            type="button"
            aria-pressed={mobilePane === "table"}
            onClick={() => setMobilePane("table")}
          >
            Table
          </button>
        </div>
      </div>

      {report.preparation_state !== "ready" && (
        <div className="workspace-banner">
          <InlineNote
            tone={report.preparation_state === "failed" ? "danger" : "warning"}
            icon={<Sparkles size={15} />}
          >
            <p>
              {report.preparation_state === "failed"
                ? "Page preparation stopped. Try reopening the report; extraction may be unavailable."
                : "Page previews are still being prepared. Extraction can be queued, but previews may wait."}
            </p>
          </InlineNote>
        </div>
      )}

      <div className="workspace-panes">
        <div className="source-column">
          <SourcePane
            documentId={documentId}
            previews={previews}
            page={page}
            pageCount={report.page_count}
            reference={reference?.reference ?? null}
            referenceLabel={reference?.label}
            cells={view?.revision.content.cells ?? []}
            jump={jump}
            selectRequest={selectRequest}
            onCell={(id) =>
              setSourceSelection((value) => ({
                id,
                sequence: (value?.sequence ?? 0) + 1,
              }))
            }
            onRegion={onRegion}
            selection={region}
            onExtractRegion={onExtractSelection}
            onManualRegion={onManualSelection}
          />
        </div>

        <div className="table-column">
          {tableId && view ? (
            <ReviewPane
              view={view}
              sourceSelection={sourceSelection}
              onSource={(source, label) =>
                setReference({ reference: source, label })
              }
              onRevision={onRevision}
              onDirty={setReviewDirty}
              onRemoved={() => {
                void queryClient.invalidateQueries({
                  queryKey: ["tables", documentId, pageNo],
                });
                navigate(`/reports/${documentId}/pages/${pageNo}`);
              }}
              checksEnabled={
                !study || study.config.condition === "review_with_checks"
              }
              study={!!study}
            />
          ) : (
            <TablePanel
              pageNo={pageNo}
              page={page}
              tables={tables}
              removed={removedQuery.data?.items ?? []}
              latest={latestTask}
              busy={processing}
              onOpenTable={openTable}
              onRestore={(table) => void restoreTable(table)}
              onExtract={onExtractPage}
              onRequestRegion={() => {
                requestRegion();
                setMobilePane("source");
              }}
            />
          )}
        </div>
      </div>

      <ExtractDialog
        open={extractOpen}
        onOpenChange={setExtractOpen}
        documentId={documentId}
        pageNo={pageNo}
        pageCount={report.page_count}
        region={extractRegion}
        onStarted={() => {
          setRegion(null);
          setActivityOpen(true);
          void queryClient.invalidateQueries({
            queryKey: queryKeys.tasks(documentId),
          });
        }}
      />

      <ManualTableDialog
        open={manualOpen}
        onOpenChange={setManualOpen}
        documentId={documentId}
        region={region}
        onCreated={onManualCreated}
        onRequestRegion={() => {
          requestRegion();
          setMobilePane("source");
        }}
      />

      <ActivityDrawer
        open={activityOpen}
        onOpenChange={setActivityOpen}
        document={report}
        tasks={tasksQuery.data?.items ?? []}
        onExtract={() => {
          setActivityOpen(false);
          onExtractPage();
        }}
        onOpenTable={(id) => {
          setActivityOpen(false);
          openTable(id);
        }}
        onAdopted={(targetId) => {
          void queryClient.invalidateQueries({
            queryKey: queryKeys.table(targetId),
          });
          void queryClient.invalidateQueries({
            queryKey: ["tables", documentId],
          });
        }}
      />
    </main>
  );
}
