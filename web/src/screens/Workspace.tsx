import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  FileSpreadsheet,
  FileText,
  Grid2X2,
  Keyboard,
  Layers,
  Sparkles,
} from "lucide-react";
import { apiRequest } from "../api/client";
import {
  queryKeys,
  tableSourcePage,
  useAllTables,
  useDocument,
  useOpenDocument,
  usePage,
  useTables,
  useTasks,
  useTableView,
} from "../api/hooks";
import {
  type PageBox,
  type PageInfo,
  type SourceReference,
  type Table,
  type TableView,
} from "../api/models";
import { useNotices } from "../app/notices";
import { AccountMenu } from "../app/AccountMenu";
import { useShortcuts } from "../app/shortcuts";
import {
  IconButton,
  EmptyState,
  InlineNote,
  Input,
  Spinner,
} from "../ui/primitives";
import { ActivityDrawer } from "../features/source/ActivityDrawer";
import { ExtractDialog } from "../features/source/ExtractDialog";
import { ManualTableDialog } from "../features/source/ManualTableDialog";
import { PdfPreviews } from "../features/source/PdfPreviews";
import { ReportExportDialog } from "../features/source/ReportExportDialog";
import { SourcePane } from "../features/source/SourcePane";
import { StitchTablesDialog } from "../features/source/StitchTablesDialog";
import { firstReviewableCell } from "../features/table/selection";
import { TablePanel } from "../features/table/TablePanel";
import { ReviewPane } from "../features/review/ReviewPane";
import { BootScreen } from "../ui/BootScreen";
import { NotFoundScreen } from "./NotFound";
import { queryClient } from "../app/query";

export function WorkspaceScreen() {
  const params = useParams<{
    documentId: string;
    pageNo?: string;
    tableId?: string;
  }>();
  const navigate = useNavigate();
  const { notify, notifyError } = useNotices();
  const { open: openShortcuts } = useShortcuts();
  const documentId = params.documentId ?? "";
  const pageNo = Number(params.pageNo ?? 1);
  const tableId = params.tableId;
  const documentQuery = useDocument(documentId);
  const pageQuery = usePage(documentId, pageNo);
  const tasksQuery = useTasks(documentId);
  const tablesQuery = useTables(documentId, pageNo, false, false);
  const allTablesQuery = useAllTables(documentId, true);
  const removedQuery = useTables(documentId, pageNo, true);
  const viewQuery = useTableView(tableId);
  const openDocument = useOpenDocument();
  const view = tableId ? viewQuery.data : undefined;
  const processing = (tasksQuery.data?.items ?? []).some(
    (task) => task.state === "queued" || task.state === "running",
  );
  const tables = tablesQuery.data?.items ?? [];
  const allTablesCount = allTablesQuery.data?.items.length ?? tables.length;

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

  const deskRef = useRef<HTMLDivElement>(null);
  const draggingSplit = useRef(false);
  const [sourceRatio, setSourceRatio] = useState(() => {
    try {
      const saved = Number(window.localStorage.getItem("tr.splitRatio"));
      return Number.isFinite(saved) && saved >= 0.22 && saved <= 0.68
        ? saved
        : 0.42;
    } catch {
      return 0.42;
    }
  });
  const [nightPaper, setNightPaper] = useState(() => {
    try {
      return window.localStorage.getItem("tr.nightPaper") === "true";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem("tr.splitRatio", String(sourceRatio));
    } catch {
      /* Ignore restricted storage environments. */
    }
  }, [sourceRatio]);
  useEffect(() => {
    try {
      window.localStorage.setItem(
        "tr.nightPaper",
        nightPaper ? "true" : "false",
      );
    } catch {
      /* Ignore restricted storage environments. */
    }
  }, [nightPaper]);

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
  const [extractPageOverride, setExtractPageOverride] = useState<number | null>(
    null,
  );
  const [extractRegion, setExtractRegion] = useState<PageBox | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [reportExportOpen, setReportExportOpen] = useState(false);
  const [stitchOpen, setStitchOpen] = useState(false);
  const [reviewDirty, setReviewDirty] = useState(false);
  const [pageInput, setPageInput] = useState(String(pageNo));
  const [visibleSourcePage, setVisibleSourcePage] = useState(pageNo);
  const [mobilePane, setMobilePane] = useState<"source" | "table">(
    tableId ? "table" : "source",
  );
  const scrollSyncedPageRef = useRef<number | null>(null);
  const defaultPageRef = useRef<{ documentId: string; page: PageInfo } | null>(
    null,
  );

  useEffect(() => {
    const fromScroll = scrollSyncedPageRef.current === pageNo;
    scrollSyncedPageRef.current = null;
    setPageInput(String(pageNo));
    setVisibleSourcePage(pageNo);
    if (!fromScroll) {
      // A page change leaves the previous table's source reference behind.
      // Keeping it would drag the preview back to the old page.
      setReference(null);
      setJump((value) => ({ page: pageNo, sequence: value.sequence + 1 }));
      setRegion(null);
    }
  }, [pageNo]);

  // In page overview the source scroll position selects the page. While a
  // table is open the preview stays free to inspect other pages or references.
  useEffect(() => {
    if (tableId || visibleSourcePage === pageNo) return;
    const timer = window.setTimeout(() => {
      const report = documentQuery.data;
      if (!report) return;
      const clamped = Math.max(
        1,
        Math.min(report.page_count, visibleSourcePage),
      );
      if (clamped === pageNo) return;
      scrollSyncedPageRef.current = clamped;
      setPageInput(String(clamped));
      navigate(`/reports/${documentId}/pages/${clamped}`, { replace: true });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [
    tableId,
    visibleSourcePage,
    pageNo,
    documentQuery.data,
    documentId,
    navigate,
  ]);

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
    const first = firstReviewableCell(view.revision.content);
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

  const onVisiblePage = useCallback(
    (next: number) => {
      if (tableId) return;
      setVisibleSourcePage(next);
    },
    [tableId],
  );

  const leaveReview = useCallback(() => {
    if (!reviewDirty) return true;
    notify(
      "Save or discard the open edit before leaving this table.",
      "warning",
    );
    return false;
  }, [reviewDirty, notify]);

  const openTable = useCallback(
    (id: string, page = pageNo) => {
      if (!leaveReview()) return;
      navigate(`/reports/${documentId}/pages/${page}/tables/${id}`);
    },
    [navigate, documentId, pageNo, leaveReview],
  );

  // Activity spans the whole report, so a run's table may live on another page.
  // Resolve the table's own source page instead of inheriting the current one.
  const openActivityTable = useCallback(
    async (id: string) => {
      if (!leaveReview()) return;
      try {
        const view = await apiRequest<TableView>(`/api/v1/tables/${id}`);
        navigate(
          `/reports/${documentId}/pages/${tableSourcePage(view)}/tables/${id}`,
        );
      } catch (error) {
        notifyError(error);
      }
    },
    [documentId, leaveReview, navigate, notifyError],
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
    scrollSyncedPageRef.current = null;
    // A jump can clamp to the page already open, so no route change follows.
    // Reflect the real page immediately instead of leaving the typed value.
    setPageInput(String(clamped));
    if (clamped === pageNo && !tableId) {
      setJump((value) => ({ page: clamped, sequence: value.sequence + 1 }));
      return;
    }
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
    setExtractPageOverride(null);
    setExtractOpen(true);
  }

  function onExtractSelection() {
    if (!region) return;
    setExtractPageOverride(null);
    setExtractRegion(region);
    setExtractOpen(true);
  }

  function onManualSelection() {
    setManualOpen(true);
  }

  function onManualCreated(table: Table) {
    const sourcePage = region?.page ?? pageNo;
    setRegion(null);
    void queryClient.invalidateQueries({
      queryKey: ["tables", documentId, pageNo],
    });
    void queryClient.invalidateQueries({
      queryKey: ["tables", documentId, sourcePage],
    });
    openTable(table.id, sourcePage);
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

  if (documentQuery.isPending) return <BootScreen />;
  if (!documentQuery.data) return <NotFoundScreen />;
  if (
    !Number.isInteger(pageNo) ||
    pageNo < 1 ||
    pageNo > documentQuery.data.page_count
  )
    return <NotFoundScreen />;
  if (!pageQuery.data) {
    if (pageQuery.isError) return <NotFoundScreen />;
    return <BootScreen />;
  }

  const report = documentQuery.data;
  const page = pageQuery.data;
  if (defaultPageRef.current?.documentId !== documentId) {
    defaultPageRef.current = { documentId, page };
  }
  const fallbackPage = defaultPageRef.current.page;
  const pageTasks = [...(tasksQuery.data?.items ?? [])]
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
    );
  const latestTask = pageTasks[0];
  const selectedTableIndex = tables.findIndex((item) => item.id === tableId);

  return (
    <main className={`workspace mobile-${mobilePane}`}>
      <header className="command">
        <div className="command-left">
          <Link
            to="/"
            className="command-back"
            aria-label="Back to reports"
            onClick={(event) => {
              if (!leaveReview()) event.preventDefault();
            }}
          >
            <ChevronLeft size={17} />
          </Link>
          <span className="command-file" aria-hidden="true">
            <FileText size={16} />
          </span>
          <div className="docbar-title">
            <h1 title={report.filename}>{report.filename}</h1>
            <span
              className={`status ${
                report.preparation_state === "ready"
                  ? tableId
                    ? "in-review"
                    : "ready"
                  : report.preparation_state === "failed"
                    ? "failed"
                    : "preparing"
              }`}
            >
              <span className="dot" />
              <span className="status-text">
                {report.preparation_state === "ready"
                  ? tableId
                    ? "In review"
                    : "Ready"
                  : report.preparation_state === "failed"
                    ? "Needs attention"
                    : "Preparing"}
              </span>
            </span>
          </div>
        </div>

        <div className="command-center">
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
          <button
            type="button"
            className={`icon-button command-grid ${!tableId ? "is-current" : ""}`}
            aria-label="Page overview"
            aria-current={!tableId ? "page" : undefined}
            onClick={() => {
              if (!leaveReview()) return;
              navigate(`/reports/${documentId}/pages/${pageNo}`);
            }}
          >
            <Grid2X2 size={15} />
          </button>
        </div>

        <div className="command-right">
          <div
            className="workspace-mobile-toggle"
            role="group"
            aria-label="Workspace view"
          >
            <button
              type="button"
              aria-label="Source"
              aria-pressed={mobilePane === "source"}
              onClick={() => setMobilePane("source")}
            >
              <FileText size={13} aria-hidden="true" />
              <span>Source</span>
            </button>
            <button
              type="button"
              aria-label="Table"
              aria-pressed={mobilePane === "table"}
              onClick={() => setMobilePane("table")}
            >
              <Grid2X2 size={13} aria-hidden="true" />
              <span>Table</span>
            </button>
          </div>
          <IconButton
            label="Stitch multi-page tables"
            disabled={allTablesCount < 2}
            onClick={() => setStitchOpen(true)}
          >
            <Layers size={15} />
          </IconButton>
          <IconButton
            label="Export combined Excel workbook"
            disabled={allTablesCount < 1}
            onClick={() => setReportExportOpen(true)}
          >
            <FileSpreadsheet size={15} />
          </IconButton>
          <IconButton label="Activity" onClick={() => setActivityOpen(true)}>
            {processing ? <Spinner size={14} /> : <Activity size={16} />}
          </IconButton>
          <IconButton
            label="Keyboard shortcuts"
            className="command-shortcuts"
            onClick={openShortcuts}
          >
            <Keyboard size={16} />
          </IconButton>
          <span className="command-account">
            <AccountMenu />
          </span>
        </div>
      </header>

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

      <div
        ref={deskRef}
        className="workspace-desk"
        style={
          {
            "--source-col": `${Math.round(sourceRatio * 1000) / 10}%`,
          } as CSSProperties
        }
      >
        <div className="source-column">
          <SourcePane
            documentId={documentId}
            previews={previews}
            page={page}
            fallbackPage={fallbackPage}
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
            onVisiblePage={onVisiblePage}
            onRegion={onRegion}
            selection={region}
            onExtractRegion={onExtractSelection}
            onManualRegion={onManualSelection}
            nightPaper={nightPaper}
            onToggleNightPaper={() => setNightPaper((prev) => !prev)}
            onJumpPage={(target) => {
              if (!tableId) {
                changePage(target);
              } else {
                setJump((value) => ({
                  page: target,
                  sequence: value.sequence + 1,
                }));
              }
            }}
            onQuickExtractPage={(target) => {
              setExtractRegion(null);
              setExtractPageOverride(target);
              setExtractOpen(true);
            }}
          />
        </div>

        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize source and table panes"
          aria-valuenow={Math.round(sourceRatio * 100)}
          aria-valuemin={22}
          aria-valuemax={68}
          tabIndex={0}
          className="workspace-divider"
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            draggingSplit.current = true;
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (!draggingSplit.current || !deskRef.current) return;
            const rect = deskRef.current.getBoundingClientRect();
            if (rect.width <= 0) return;
            const raw = (event.clientX - rect.left) / rect.width;
            setSourceRatio(Math.max(0.22, Math.min(0.68, raw)));
          }}
          onPointerUp={() => {
            draggingSplit.current = false;
          }}
          onPointerCancel={() => {
            draggingSplit.current = false;
          }}
          onDoubleClick={() => setSourceRatio(0.42)}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft") {
              event.preventDefault();
              setSourceRatio((r) => Math.max(0.22, r - 0.02));
            } else if (event.key === "ArrowRight") {
              event.preventDefault();
              setSourceRatio((r) => Math.min(0.68, r + 0.02));
            }
          }}
        />

        <div className="table-column">
          {tableId && viewQuery.isError ? (
            <div className="table-panel-loading">
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
            </div>
          ) : tableId && viewQuery.isPending ? (
            <div className="table-panel-loading" role="status">
              <Spinner size={18} />
              <span className="small muted">Loading table…</span>
            </div>
          ) : tableId && view ? (
            <ReviewPane
              key={view.table.id}
              view={view}
              previews={previews}
              nightPaper={nightPaper}
              tableLabel={
                selectedTableIndex >= 0
                  ? `Table ${selectedTableIndex + 1}`
                  : view.table.origin === "manual"
                    ? "Manual table"
                    : "Table"
              }
              tableOptions={tables.map((table, index) => ({
                id: table.id,
                label: `Table ${index + 1}`,
              }))}
              onOpenTable={openTable}
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
                void queryClient.invalidateQueries({
                  queryKey: queryKeys.allTables(documentId),
                });
                navigate(`/reports/${documentId}/pages/${pageNo}`);
              }}
              checksEnabled
            />
          ) : (
            <TablePanel
              pageNo={pageNo}
              page={page}
              tables={tables}
              removed={removedQuery.data?.items ?? []}
              latest={latestTask}
              tasks={pageTasks}
              busy={processing}
              onOpenTable={openTable}
              onRestore={(table) => void restoreTable(table)}
              onExtract={onExtractPage}
              onManual={onManualSelection}
              onActivity={() => setActivityOpen(true)}
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
        onOpenChange={(open) => {
          setExtractOpen(open);
          if (!open) setExtractPageOverride(null);
        }}
        documentId={documentId}
        pageNo={extractPageOverride ?? pageNo}
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

      <ReportExportDialog
        open={reportExportOpen}
        onOpenChange={setReportExportOpen}
        documentId={documentId}
        filename={report.filename}
      />

      <StitchTablesDialog
        open={stitchOpen}
        onOpenChange={setStitchOpen}
        documentId={documentId}
        onStitched={(created, targetPage) => {
          openTable(created.id, targetPage);
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
          void openActivityTable(id);
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
