import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Info } from "lucide-react";
import { useExports } from "../../api/hooks";
import type {
  Revision,
  SourceReference,
  Table,
  TableView,
} from "../../api/models";
import { Button, InlineNote } from "../../ui/primitives";
import { TabBar } from "../../ui/TabBar";
import { Dialog, DialogContent } from "../../ui/dialog";
import type { PdfPreviews } from "../source/PdfPreviews";
import { TableGrid } from "../table/TableGrid";
import { CellRail } from "./CellRail";
import { ChecksPanel } from "./ChecksPanel";
import { FinishDialog } from "./FinishDialog";
import { MorePanel } from "./MorePanel";
import { SheetToolbar } from "./SheetToolbar";
import { useTableReview } from "./useTableReview";
import { VerdictTray } from "./VerdictTray";

type Tool = "cell" | "checks" | "more";

export function ReviewPane({
  view,
  tableLabel,
  tableOptions,
  previews,
  onOpenTable,
  onSource,
  onRevision,
  onDirty,
  sourceSelection,
  onRemoved,
  checksEnabled = true,
}: {
  view: TableView;
  tableLabel: string;
  tableOptions: Array<{ id: string; label: string }>;
  previews: PdfPreviews;
  onOpenTable: (id: string) => void;
  onSource: (reference: SourceReference, label?: string) => void;
  onRevision: (revision: Revision) => void;
  onDirty: (dirty: boolean) => void;
  sourceSelection: { id: string; sequence: number } | null;
  onRemoved: (table: Table) => void;
  checksEnabled?: boolean;
}) {
  const screen = useRef<HTMLDivElement>(null);
  const [tool, setTool] = useState<Tool>("cell");
  const [finishOpen, setFinishOpen] = useState(false);
  const [exceptionsOnly, setExceptionsOnly] = useState(false);
  const [railOpen, setRailOpen] = useState(() => {
    try {
      return window.localStorage.getItem("tr.railOpen") !== "false";
    } catch {
      return true;
    }
  });

  const state = useTableReview({
    view,
    onSource,
    onRevision,
    onDirty,
    sourceSelection,
    onRemoved,
    checksEnabled,
  });

  const exports = useExports(view.table.id, finishOpen);

  useEffect(() => {
    try {
      window.localStorage.setItem("tr.railOpen", railOpen ? "true" : "false");
    } catch {
      /* Ignore storage quota issues in restricted browsers. */
    }
  }, [railOpen]);

  function openTool(next: Tool) {
    setTool(next);
    setRailOpen(true);
  }

  const review = state.review;
  const content = state.content;
  const approval = review?.current_approval;
  const reviewed = review?.checked_cell_ids.length ?? 0;
  const total = content.cells.length;
  const unresolved = review?.unresolved_count ?? 0;
  const unreadable = review?.unreadable_count ?? 0;
  const hasExceptions = state.issueCells.size > 0 || unreadable > 0;
  const exceptionRowSet = useMemo(() => {
    const rows = new Set<number>();
    const cellReviews = review?.cell_reviews ?? {};
    for (const item of content.cells) {
      const verdict = cellReviews[item.id]?.verdict;
      const subtotal = state.subtotalByTotalCell.get(item.id);
      if (
        state.issueCells.has(item.id) ||
        verdict === "unreadable" ||
        subtotal?.state === "difference"
      ) {
        rows.add(item.row);
      }
    }
    return rows;
  }, [
    content.cells,
    review?.cell_reviews,
    state.issueCells,
    state.subtotalByTotalCell,
  ]);

  useEffect(() => {
    if (exceptionsOnly && exceptionRowSet.size === 0) {
      setExceptionsOnly(false);
    }
  }, [exceptionsOnly, exceptionRowSet.size]);

  const readyForApproval =
    !!review && total > 0 && unresolved === 0 && unreadable === 0;
  const dirtyLabel = state.subtotalDirty
    ? "Unsaved subtotal setup"
    : state.status || (state.dirty ? "Unsaved changes" : "");

  const tabs = useMemo(() => {
    const items = [{ id: "cell", label: "Cell" }];
    if (checksEnabled) items.push({ id: "checks", label: "Checks" });
    items.push({ id: "more", label: "Details" });
    return items;
  }, [checksEnabled]);

  return (
    <div
      className={`review-pane ${railOpen ? "" : "rail-collapsed"}`}
      ref={screen}
    >
      <div className="table-area">
        <article className="sheet">
          <SheetToolbar
            tableId={view.table.id}
            tableLabel={tableLabel}
            tableOptions={tableOptions}
            content={content}
            approval={approval}
            dirtyLabel={dirtyLabel}
            busy={state.busy}
            dirty={state.dirty}
            subtotalDirty={state.subtotalDirty}
            reviewLoaded={!!review}
            readyForApproval={readyForApproval}
            canUndo={state.canUndo}
            canRedo={state.canRedo}
            railOpen={railOpen}
            exceptionsOnly={exceptionsOnly}
            exceptionRowCount={exceptionRowSet.size}
            onToggleExceptionsOnly={() => setExceptionsOnly((prev) => !prev)}
            onOpenTable={onOpenTable}
            onSaveTitle={(title) =>
              void state.save(
                { kind: "set_title", title },
                { message: "Title updated" },
              )
            }
            onSaveTableContext={(ctx) =>
              void state.save(
                { kind: "set_table_context", ...ctx },
                { message: "Table context updated" },
              )
            }
            onUndo={() => void state.undo()}
            onRedo={() => void state.redo()}
            onBulkConfirm={(scope) => void state.bulkConfirm(scope)}
            onCopy={(format) => void state.copyAs(format)}
            onFinish={() => setFinishOpen(true)}
            onOpenDetails={() => openTool("more")}
            onToggleRail={() => setRailOpen((prev) => !prev)}
          />

          {(state.error ||
            state.conflict ||
            state.invalidation ||
            state.reviewQuery.isError) && (
            <div className="review-alerts" aria-live="assertive">
              {state.error && (
                <InlineNote tone="danger" title={state.errorTitle}>
                  <p>{state.error}</p>
                  {state.conflict && (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => void state.reload()}
                    >
                      Load latest revision
                    </Button>
                  )}
                </InlineNote>
              )}
              {state.invalidation && !state.error && (
                <InlineNote tone="warning" icon={<AlertTriangle size={15} />}>
                  <p>{state.invalidation}</p>
                </InlineNote>
              )}
              {state.reviewQuery.isError && !state.error && (
                <InlineNote tone="warning" icon={<Info size={15} />}>
                  <p>
                    Review status could not be loaded.{" "}
                    {state.reviewQuery.error.message}
                  </p>
                  <Button
                    size="sm"
                    onClick={() => void state.reviewQuery.refetch()}
                  >
                    Retry
                  </Button>
                </InlineNote>
              )}
            </div>
          )}

          <div className="review-grid-area">
            {exceptionsOnly && (
              <div className="exception-filter-banner" role="status">
                <span>
                  Showing <strong>{exceptionRowSet.size}</strong> exception{" "}
                  {exceptionRowSet.size === 1 ? "row" : "rows"} of{" "}
                  {content.rows - content.header_rows.length}
                </span>
                <button
                  type="button"
                  className="grid-check-link"
                  onClick={() => setExceptionsOnly(false)}
                >
                  Show all rows
                </button>
              </div>
            )}
            <TableGrid
              tableKey={view.table.id}
              content={content}
              selection={state.selection}
              checked={new Set(review?.checked_cell_ids ?? [])}
              cellReviews={review?.cell_reviews ?? {}}
              onSelect={(position, extend) =>
                state.selectPosition(position, extend)
              }
              issueCells={state.issueCells}
              highlightedCells={state.highlightedCells}
              onActivate={() => {
                if (state.cell) state.startInlineEdit(state.cell);
              }}
              editingCellId={state.editingCellId}
              inlineDraft={state.inlineDraft}
              onStartEdit={state.startInlineEdit}
              onInlineDraftChange={state.setInlineDraft}
              onCommitInline={(move) => void state.commitInlineEdit(move)}
              onCancelInline={state.cancelInlineEdit}
              subtotalByTotalCell={state.subtotalByTotalCell}
              tracedTotalCellId={state.activeSubtotalTrace?.totalCellId ?? null}
              tracedAddendCells={state.activeSubtotalTrace?.addendCellIds}
              onHoverSubtotalCell={state.setHoveredSubtotalCellId}
              visibleRowIndices={exceptionsOnly ? exceptionRowSet : null}
            />
            {state.activeSubtotalTrace && (
              <div
                className={`formula-trace-bar trace-${state.activeSubtotalTrace.state}`}
                role="status"
                aria-live="polite"
              >
                <span className="formula-trace-glyph" aria-hidden="true">
                  Σ
                </span>
                <strong className="truncate">
                  {state.activeSubtotalTrace.label}
                </strong>
                <span className="muted">
                  ({state.activeSubtotalTrace.addendCellIds.size} cells ·{" "}
                  {state.activeSubtotalTrace.direction})
                </span>
                {state.activeSubtotalTrace.inputSum != null && (
                  <span className="formula-trace-math mono num">
                    Sum {state.activeSubtotalTrace.inputSum} vs Reported{" "}
                    {state.activeSubtotalTrace.reportedTotal}
                    {state.activeSubtotalTrace.difference &&
                    state.activeSubtotalTrace.state === "difference"
                      ? ` · Δ ${state.activeSubtotalTrace.difference}`
                      : state.activeSubtotalTrace.state === "rounding_match"
                        ? " · Rounding match"
                        : " · Exact match"}
                  </span>
                )}
              </div>
            )}
            <div className="grid-footnote">
              <span className="grid-footnote-selection">
                <span className="mono">
                  {state.cell
                    ? `${state.cell.row + 1}:${state.cell.col + 1}`
                    : "—"}
                </span>{" "}
                · {state.selected.length} selected
              </span>
              <span className="grid-footnote-progress">
                <span className="review-progress-copy">
                  <span className="num">
                    {reviewed}/{total}
                  </span>{" "}
                  reviewed
                  {!!unreadable && (
                    <span className="review-unreadable">
                      {" "}
                      · {unreadable} unreadable
                    </span>
                  )}
                </span>
                {unresolved > 0 && (
                  <button
                    type="button"
                    className="grid-check-link"
                    onClick={() => openTool(checksEnabled ? "checks" : "more")}
                  >
                    Review {unresolved} open check{unresolved === 1 ? "" : "s"}
                  </button>
                )}
              </span>
              <span className="grid-footnote-hint">
                Double-click or Enter edits · E next issue · N next cell
              </span>
            </div>
          </div>
        </article>

        <div className="mat-note">
          <span className="dot" aria-hidden="true" />
          Source-backed draft, pinned to revision{" "}
          <span className="mono">{state.revision.id.slice(0, 8)}</span>
        </div>

        <VerdictTray
          cell={state.cell}
          content={content}
          selected={state.selected}
          review={review}
          draft={state.draft}
          onDraft={state.setDraft}
          onDiscard={() => {
            state.setDraft(state.baseline);
            state.setStatus("");
            state.setError("");
          }}
          onConfirm={() => void state.confirmValue()}
          onVerdict={(verdict, reason) =>
            void state.markVerdict(verdict, reason)
          }
          onNextUnchecked={state.nextUnchecked}
          onNextException={state.nextException}
          hasExceptions={hasExceptions}
          busy={state.busy}
          dirty={state.dirty}
        />
      </div>

      {railOpen && (
        <aside className="rail" aria-label="Cell and checks">
          <div className="rail-head">
            <TabBar
              value={tool}
              onChange={(value) => openTool(value as Tool)}
              items={tabs.map((tab) =>
                tab.id === "checks"
                  ? {
                      ...tab,
                      badge:
                        review && review.unresolved_count ? (
                          <span className="tab-count">
                            {review.unresolved_count}
                          </span>
                        ) : undefined,
                    }
                  : tab,
              )}
              label="Cell, checks and details"
            />
          </div>
          <div
            className="rail-body"
            role="tabpanel"
            aria-label={`${tool} panel`}
          >
            {tool === "cell" && (
              <CellRail
                cell={state.cell}
                content={content}
                review={review}
                previews={previews}
                draft={state.draft}
                onDraft={state.setDraft}
                onSaveCell={() => void state.saveCellChanges()}
                busy={state.busy}
                onSource={(reference) =>
                  onSource(
                    reference,
                    state.cell
                      ? `${state.cell.row + 1}:${state.cell.col + 1}`
                      : undefined,
                  )
                }
              />
            )}
            {tool === "checks" && review && (
              <ChecksPanel
                review={review}
                busy={state.busy}
                dirty={state.dirty}
                onDecision={(id, kind, reason) =>
                  void state.decideIssue(id, kind, reason)
                }
                onInspect={(issue) => {
                  const ok = state.inspectIssue(issue);
                  if (ok && issue.affected_cell_ids.length) setTool("cell");
                  return ok;
                }}
                selectedIssue={state.focusedIssue}
              />
            )}
            {tool === "checks" && !review && !state.reviewQuery.isError && (
              <p className="small muted" role="status">
                Loading review state…
              </p>
            )}
            <MorePanel
              hidden={tool !== "more"}
              view={view}
              revision={state.revision}
              review={review}
              cell={state.cell}
              selected={state.selected}
              selection={state.selection}
              busy={state.busy}
              dirty={state.dirty}
              subtotalDirty={state.subtotalDirty}
              onSave={(operation) => void state.save(operation)}
              onSource={(reference) =>
                onSource(
                  reference,
                  state.cell
                    ? `${state.cell.row + 1}:${state.cell.col + 1}`
                    : undefined,
                )
              }
              onSelect={(id) => {
                const target = content.cells.find((item) => item.id === id);
                if (target)
                  state.selectPosition(
                    { row: target.row, col: target.col },
                    false,
                    target,
                  );
              }}
              onRestore={(id) => void state.restore(id)}
              onRemove={() => void state.removeTable()}
              onSubtotalDirty={state.setSubtotalDirty}
              onPreview={(value) => state.setPreviewRevision(value)}
            />
          </div>
        </aside>
      )}

      {review && (
        <FinishDialog
          open={finishOpen}
          onOpenChange={setFinishOpen}
          tableId={view.table.id}
          revisionId={state.revision.id}
          content={content}
          review={review}
          busy={state.busy}
          exports={exports.data?.items ?? []}
          onApprove={state.approve}
        />
      )}

      <Dialog
        open={!!state.previewRevision}
        onOpenChange={(open) => {
          if (!open) state.setPreviewRevision(null);
        }}
      >
        {state.previewRevision && (
          <DialogContent
            title={`Historical revision ${state.previewRevision.id.slice(0, 8)}`}
            description="Read-only content from an earlier revision. Restoring creates a new draft."
            size="lg"
            footer={
              <>
                <Button
                  variant="ghost"
                  onClick={() => state.setPreviewRevision(null)}
                >
                  Close
                </Button>
                <Button
                  variant="ink"
                  disabled={state.busy || state.dirty || state.subtotalDirty}
                  onClick={() => void state.restore(state.previewRevision!.id)}
                >
                  Restore as new draft
                </Button>
              </>
            }
          >
            <p className="small muted mono break">
              checksum {state.previewRevision.checksum}
            </p>
            <div className="revision-preview">
              {state.previewRevision.content.cells.map((item) => (
                <div key={item.id}>
                  <span className="mono tiny muted">
                    {item.row + 1}:{item.col + 1}
                  </span>
                  <span>{item.text || "(blank)"}</span>
                </div>
              ))}
            </div>
          </DialogContent>
        )}
      </Dialog>
    </div>
  );
}
