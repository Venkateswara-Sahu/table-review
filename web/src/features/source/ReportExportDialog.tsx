import { useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { Download, FileSpreadsheet, LoaderCircle, XCircle } from "lucide-react";
import { apiBlob, apiRequest, idempotencyKey } from "../../api/client";
import { queryKeys, useAllTables, useReportExports } from "../../api/hooks";
import type { ReportExportRecord, TableView } from "../../api/models";
import { useNotices } from "../../app/notices";
import { Dialog, DialogContent } from "../../ui/dialog";
import { Badge, Button, InlineNote } from "../../ui/primitives";

export function ReportExportDialog({
  open,
  onOpenChange,
  documentId,
  filename,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string;
  filename: string;
}) {
  const client = useQueryClient();
  const { notify, notifyError } = useNotices();
  const tablesQuery = useAllTables(documentId, open);
  const exportsQuery = useReportExports(documentId, open);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [includeFormulas, setIncludeFormulas] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const timers = useRef<number[]>([]);

  const activeTables = tablesQuery.data?.items ?? [];
  const tableViews = useQueries({
    queries: activeTables.map((table) => ({
      queryKey: queryKeys.table(table.id),
      queryFn: () => apiRequest<TableView>(`/api/v1/tables/${table.id}`),
      enabled: open,
      staleTime: 30_000,
    })),
  });

  const orderedEntries = useMemo(() => {
    return activeTables
      .map((table, index) => {
        const view = tableViews[index]?.data;
        const pageNo =
          view?.revision.content.source_reference?.page ??
          view?.revision.content.cells[0]?.reference.page ??
          null;
        const title = view?.revision.content.title;
        const shape = view
          ? `${view.revision.content.rows}×${view.revision.content.columns}`
          : "";
        return {
          table,
          pageNo,
          label: title || `Table ${index + 1}`,
          shape,
        };
      })
      .sort((a, b) => (a.pageNo ?? 9999) - (b.pageNo ?? 9999));
  }, [activeTables, tableViews]);

  useEffect(() => {
    if (open && activeTables.length) {
      setSelectedIds(orderedEntries.map((item) => item.table.id));
    }
    // Initialize defaults when the dialog opens or table list populates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeTables.length]);

  useEffect(
    () => () => {
      for (const timer of timers.current) window.clearTimeout(timer);
    },
    [],
  );

  function toggleTable(id: string) {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  }

  async function buildWorkbook() {
    if (!selectedIds.length) return;
    setSubmitting(true);
    try {
      await apiRequest<ReportExportRecord>(
        `/api/v1/documents/${encodeURIComponent(documentId)}/exports`,
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey() },
          body: JSON.stringify({
            table_ids: selectedIds,
            include_formulas: includeFormulas,
          }),
        },
      );
      notify("Multi-table Excel export started", "info");
      await client.invalidateQueries({
        queryKey: queryKeys.reportExports(documentId),
      });
    } catch (error) {
      notifyError(error);
    } finally {
      setSubmitting(false);
    }
  }

  async function download(record: ReportExportRecord) {
    setDownloading(record.id);
    try {
      const blob = await apiBlob(`/api/v1/exports/${record.id}/download`);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      const stem = filename.replace(/\.pdf$/i, "") || "report";
      anchor.href = url;
      anchor.download = `${stem}-tables.xlsx`;
      anchor.click();
      timers.current.push(
        window.setTimeout(() => URL.revokeObjectURL(url), 1000),
      );
    } catch (error) {
      notifyError(error);
    } finally {
      setDownloading(null);
    }
  }

  const exportsList = exportsQuery.data?.items ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Export report workbook (.xlsx)"
        description="Combine multiple tables from this PDF into one Excel file with a linked Overview sheet, clean tab names, and a consolidated audit trail."
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Close
            </Button>
            <Button
              variant="primary"
              icon={<FileSpreadsheet size={15} />}
              disabled={!selectedIds.length || submitting}
              loading={submitting}
              onClick={() => void buildWorkbook()}
            >
              Export {selectedIds.length} table
              {selectedIds.length === 1 ? "" : "s"}
            </Button>
          </>
        }
      >
        <div className="report-export-dialog">
          {tablesQuery.isPending ? (
            <p className="small muted" role="status">
              Loading report tables…
            </p>
          ) : !orderedEntries.length ? (
            <InlineNote tone="info">
              <p>
                No extracted or manual tables exist in this document yet.
                Extract a table first before exporting a combined workbook.
              </p>
            </InlineNote>
          ) : (
            <>
              <div className="row spread">
                <span className="field-label">
                  Included tables ({selectedIds.length} of{" "}
                  {orderedEntries.length})
                </span>
                <div className="row">
                  <button
                    type="button"
                    className="link-button small"
                    onClick={() =>
                      setSelectedIds(
                        orderedEntries.map((item) => item.table.id),
                      )
                    }
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    className="link-button small"
                    onClick={() => setSelectedIds([])}
                  >
                    Clear
                  </button>
                </div>
              </div>

              <ul className="multi-table-checklist">
                {orderedEntries.map((entry) => {
                  const checked = selectedIds.includes(entry.table.id);
                  return (
                    <li key={entry.table.id}>
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleTable(entry.table.id)}
                        />
                        <span className="multi-table-label">{entry.label}</span>
                        <span className="tiny muted mono">
                          {entry.pageNo ? `p.${entry.pageNo}` : ""}{" "}
                          {entry.shape}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>

              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={includeFormulas}
                  onChange={(event) => setIncludeFormulas(event.target.checked)}
                />
                Include live Excel =SUM() formulas on verified subtotals
              </label>
            </>
          )}

          {!!exportsList.length && (
            <section
              className="finish-exports"
              aria-label="Generated workbooks"
            >
              <h4>Recent report workbooks</h4>
              <ul className="export-list">
                {exportsList.slice(0, 5).map((record) => {
                  const count =
                    typeof record.manifest?.table_count === "number"
                      ? record.manifest.table_count
                      : null;
                  return (
                    <li key={record.id}>
                      <span className="export-icon" aria-hidden="true">
                        <FileSpreadsheet size={16} />
                      </span>
                      <div className="export-main">
                        <p>
                          Report workbook (.xlsx)
                          {count !== null ? ` · ${count} tables` : ""}
                        </p>
                        <p className="tiny muted mono">
                          {record.id.slice(0, 8)}
                        </p>
                      </div>
                      {record.state === "succeeded" ? (
                        <Button
                          size="sm"
                          variant="secondary"
                          icon={<Download size={13} />}
                          loading={downloading === record.id}
                          onClick={() => void download(record)}
                        >
                          Download
                        </Button>
                      ) : record.state === "failed" ? (
                        <span className="tiny export-error">
                          <XCircle size={13} aria-hidden="true" />
                          Export failed
                        </span>
                      ) : (
                        <span className="tiny muted export-pending">
                          <LoaderCircle className="button-spin" size={13} />
                          Preparing
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
