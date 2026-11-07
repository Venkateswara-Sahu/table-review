import {
  AlertTriangle,
  ArrowRight,
  Grid2X2,
  Plus,
  RotateCcw,
  ScanLine,
  Table2,
  Trash2,
} from "lucide-react";
import type { PageInfo, Table, Task } from "../../api/models";
import {
  Badge,
  Button,
  EmptyState,
  InlineNote,
  Spinner,
} from "../../ui/primitives";

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

export function TablePanel({
  pageNo,
  page,
  tables,
  removed,
  latest,
  busy,
  onOpenTable,
  onRestore,
  onExtract,
  onRequestRegion,
}: {
  pageNo: number;
  page: PageInfo;
  tables: Table[];
  removed: Table[];
  latest?: Task;
  busy: boolean;
  onOpenTable: (id: string) => void;
  onRestore: (table: Table) => void;
  onExtract: () => void;
  onRequestRegion: () => void;
}) {
  const notes = extractionNotes(page, latest);
  const processing = latest?.state === "queued" || latest?.state === "running";
  const empty = !tables.length;
  const regionTask = !!latest?.input.region;
  const outcome = latest?.result?.conversion_outcome;

  return (
    <section className="table-panel" aria-label={`Tables on page ${pageNo}`}>
      <div className="table-panel-head">
        <div>
          <h2>Tables on page {pageNo}</h2>
          <p className="muted small">
            {tables.length
              ? `${tables.length} ${tables.length === 1 ? "table" : "tables"} ready to review`
              : "Nothing extracted from this page yet"}
          </p>
        </div>
        <Button
          size="sm"
          variant="secondary"
          icon={<Plus size={14} />}
          disabled={busy || page.extraction_supported === false}
          onClick={onExtract}
        >
          Extract page
        </Button>
      </div>

      {!!notes.length && (
        <details
          className={`page-notes ${latest?.state === "failed" ? "page-notes-danger" : ""}`}
        >
          <summary>
            <AlertTriangle size={14} aria-hidden="true" />
            <span>{notes[0]}</span>
            {notes.length > 1 && (
              <span className="page-notes-count">+{notes.length - 1}</span>
            )}
          </summary>
          <ul>
            {notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </details>
      )}

      {processing && (
        <div className="task-inline" role="status">
          <Spinner size={15} />
          <span>
            {latest?.state === "queued"
              ? "Extraction queued for this page"
              : "Extracting this page"}
          </span>
          <span className="muted small">You can keep reading the source.</span>
        </div>
      )}

      {empty ? (
        <div className="table-empty panel">
          <EmptyState
            icon={<Table2 size={28} />}
            title={
              processing
                ? latest?.state === "queued"
                  ? "Extraction queued"
                  : "Extracting this page"
                : latest?.state === "failed"
                  ? "Extraction stopped"
                  : latest?.state === "succeeded" && outcome === "success"
                    ? regionTask
                      ? "No tables found in the selected region"
                      : "No tables found on this page"
                    : latest?.state === "succeeded" &&
                        outcome === "partial_success"
                      ? "Extraction needs another look"
                      : "No tables on this page yet"
            }
            actions={
              <>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy || processing}
                  onClick={onExtract}
                >
                  {latest?.state === "failed"
                    ? "Retry extraction"
                    : "Extract page"}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  icon={<ScanLine size={14} />}
                  disabled={busy}
                  onClick={onRequestRegion}
                >
                  Select region
                </Button>
              </>
            }
          >
            <p>
              {processing
                ? "The draft will appear here when Docling finishes. Your saved work is untouched."
                : latest?.state === "succeeded"
                  ? "If you can see a table, select its region on the source page and extract it with OCR."
                  : "Extract the page, or draw around the table on the source to use OCR."}
            </p>
          </EmptyState>
        </div>
      ) : (
        <div className="table-list" role="list">
          {tables.map((table, index) => (
            <div className="table-row" role="listitem" key={table.id}>
              <span className="table-glyph" aria-hidden="true">
                <Grid2X2 size={18} />
              </span>
              <div className="table-row-main">
                <h3>Table {index + 1}</h3>
                <p>
                  <Badge tone="neutral" className="badge-mini">
                    {table.origin === "manual" ? "Manual grid" : "Extracted"}
                  </Badge>
                  <span className="muted small mono">
                    {table.current_revision_id.slice(0, 8)}
                  </span>
                </p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                icon={<ArrowRight size={14} />}
                onClick={() => onOpenTable(table.id)}
              >
                Review
              </Button>
            </div>
          ))}
        </div>
      )}

      {!!removed.length && (
        <details className="removed-tables">
          <summary>
            <Trash2 size={13} aria-hidden="true" />
            Removed tables ({removed.length})
          </summary>
          <p className="muted small">
            Restoring creates an unchecked draft. Reviews and approval must be
            renewed.
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
    </section>
  );
}
