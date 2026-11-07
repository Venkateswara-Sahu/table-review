import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCheck,
  ChevronRight,
  Download,
  FileText,
  GripVertical,
  Info,
  ShieldCheck,
} from "lucide-react";
import {
  ApiError,
  apiRequest,
  errorMessage,
  idempotencyKey,
} from "../../api/client";
import { queryKeys, useExports, useReview } from "../../api/hooks";
import type {
  Cell,
  CellVerdict,
  EditOperation,
  Issue,
  IssueDecision,
  Revision,
  SourceReference,
  Table,
  TableView,
} from "../../api/models";
import { useConfirm } from "../../app/confirm";
import { useNotices } from "../../app/notices";
import {
  Badge,
  Button,
  IconButton,
  InlineNote,
  Progress,
} from "../../ui/primitives";
import { TabBar } from "../../ui/TabBar";
import { Dialog, DialogContent } from "../../ui/dialog";
import { TableGrid } from "../table/TableGrid";
import {
  cellAt,
  selectedCells,
  type Position,
  type Selection,
} from "../table/selection";
import { CellInspector } from "./CellInspector";
import { ChecksPanel } from "./ChecksPanel";
import { draftFor, type EditDraft } from "./draft";
import { FinishDialog } from "./FinishDialog";
import { MorePanel } from "./MorePanel";

type Tool = "cell" | "checks" | "more";

function firstCell(view: TableView): Cell | undefined {
  return view.revision.content.cells.reduce<Cell | undefined>(
    (first, cell) =>
      !first ||
      cell.row < first.row ||
      (cell.row === first.row && cell.col < first.col)
        ? cell
        : first,
    undefined,
  );
}

export function ReviewPane({
  view,
  onSource,
  onRevision,
  onDirty,
  sourceSelection,
  onRemoved,
  checksEnabled,
  study = false,
}: {
  view: TableView;
  onSource: (reference: SourceReference, label?: string) => void;
  onRevision: (revision: Revision) => void;
  onDirty: (dirty: boolean) => void;
  sourceSelection: { id: string; sequence: number } | null;
  onRemoved: (table: Table) => void;
  checksEnabled: boolean;
  study?: boolean;
}) {
  const client = useQueryClient();
  const confirm = useConfirm();
  const { notify } = useNotices();
  const screen = useRef<HTMLDivElement>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const [revision, setRevision] = useState(view.revision);
  const initial = firstCell(view);
  const [selection, setSelection] = useState<Selection>({
    anchor: { row: initial?.row ?? 0, col: initial?.col ?? 0 },
    focus: { row: initial?.row ?? 0, col: initial?.col ?? 0 },
  });
  const [draft, setDraft] = useState<EditDraft>(() =>
    draftFor(view.revision.content, initial),
  );
  const [tool, setTool] = useState<Tool>("cell");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [invalidation, setInvalidation] = useState("");
  const [busy, setBusy] = useState(false);
  const [subtotalDirty, setSubtotalDirty] = useState(false);
  const [focusedIssue, setFocusedIssue] = useState<string | null>(null);
  const [finishOpen, setFinishOpen] = useState(false);
  const [previewRevision, setPreviewRevision] = useState<Revision | null>(null);

  const reviewQuery = useReview(revision.id);
  const review = reviewQuery.data;
  const exports = useExports(view.table.id, finishOpen);

  const content = revision.content;
  const selected = useMemo(
    () => selectedCells(content, selection),
    [content, selection],
  );
  const cell = cellAt(content, selection.focus);
  const baseline = useMemo(() => draftFor(content, cell), [content, cell]);
  const dirty = JSON.stringify(baseline) !== JSON.stringify(draft);

  const showChecks = checksEnabled;

  useEffect(() => {
    if (view.revision.id === revision.id) return;
    setRevision(view.revision);
    const position = {
      row: Math.min(selection.focus.row, view.revision.content.rows - 1),
      col: Math.min(selection.focus.col, view.revision.content.columns - 1),
    };
    const target = cellAt(view.revision.content, position);
    if (target) {
      setSelection({
        anchor: { row: target.row, col: target.col },
        focus: { row: target.row, col: target.col },
      });
      setDraft(draftFor(view.revision.content, target));
      onSource(target.reference, `${target.row + 1}:${target.col + 1}`);
    }
    // Only react to a revision arriving from outside this pane.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.revision.id]);

  useEffect(() => {
    onDirty(dirty || busy || subtotalDirty);
  }, [dirty, busy, subtotalDirty, onDirty]);

  useEffect(() => () => onDirty(false), [onDirty]);

  useEffect(() => {
    if (!dirty && !busy && !subtotalDirty) return;
    const warning = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warning);
    return () => window.removeEventListener("beforeunload", warning);
  }, [dirty, busy, subtotalDirty]);

  useEffect(() => {
    if (!sourceSelection) return;
    const target = content.cells.find(
      (value) => value.id === sourceSelection.id,
    );
    if (!target) return;
    if (dirty) {
      notify(
        "Save or discard the open edit before jumping to another cell.",
        "warning",
      );
      return;
    }
    selectPosition({ row: target.row, col: target.col }, false, target);
    setTool("cell");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceSelection?.sequence]);

  function selectPosition(
    position: Position,
    extend: boolean,
    known?: Cell,
  ): boolean {
    if (busy) return false;
    if (dirty) {
      notify(
        "Save or discard the open edit before selecting another cell.",
        "warning",
      );
      return false;
    }
    const target =
      known ??
      content.cells.find(
        (value) =>
          value.row <= position.row &&
          value.row + value.row_span > position.row &&
          value.col <= position.col &&
          value.col + value.col_span > position.col,
      );
    setSelection((value) => ({
      anchor: extend ? value.anchor : position,
      focus: position,
    }));
    setDraft(draftFor(content, target));
    if (target)
      onSource(target.reference, `${target.row + 1}:${target.col + 1}`);
    setStatus("");
    setError("");
    return true;
  }

  function openTool(next: Tool) {
    setTool(next);
  }

  function nextUnchecked() {
    const ordered = [...content.cells].sort(
      (a, b) => a.row - b.row || a.col - b.col,
    );
    const current = ordered.findIndex((value) => value.id === cell?.id);
    const target = [
      ...ordered.slice(current + 1),
      ...ordered.slice(0, current + 1),
    ].find(
      (value) =>
        !review?.checked_cell_ids.includes(value.id) ||
        review.cell_reviews?.[value.id]?.verdict === "unreadable",
    );
    if (target) {
      selectPosition({ row: target.row, col: target.col }, false, target);
      setTool("cell");
      requestAnimationFrame(() =>
        screen.current
          ?.querySelector<HTMLTextAreaElement>(
            "textarea[data-cell-text-editor]",
          )
          ?.focus(),
      );
    } else {
      notify("Every cell has a recorded decision.", "success");
    }
  }

  function inspectIssue(issue: Issue): boolean {
    if (busy) return false;
    const affected = content.cells.filter((value) =>
      issue.affected_cell_ids.includes(value.id),
    );
    if (affected.length) {
      const first = affected[0];
      if (!selectPosition({ row: first.row, col: first.col }, false, first))
        return false;
      setTool("cell");
    } else if (dirty) {
      notify(
        "Save or discard the open edit before inspecting another issue.",
        "warning",
      );
      return false;
    }
    if (issue.reference)
      onSource(issue.reference, issue.kind.replaceAll("_", " "));
    setFocusedIssue(issue.id);
    if (!issue.reference && !affected.length)
      setStatus(
        "This check has no source location or affected cells. Compare the original page manually.",
      );
    return true;
  }

  function setReviewState(next: import("../../api/models").ReviewState) {
    client.setQueryData(queryKeys.review(revision.id), next);
  }

  async function markVerdict(verdict: CellVerdict, reason = "") {
    if (busy || dirty) return;
    const ids = selected.map((item) => item.id);
    if (!ids.length) return;
    setBusy(true);
    setError("");
    try {
      const next = await apiRequest<import("../../api/models").ReviewState>(
        `/api/v1/revisions/${revision.id}/cell-review`,
        {
          method: "POST",
          body: JSON.stringify({
            cell_ids: ids,
            verdict,
            reason,
          }),
        },
      );
      setReviewState(next);
      const words =
        verdict === "confirmed_value"
          ? "confirmed against source"
          : verdict === "confirmed_blank"
            ? "confirmed blank"
            : verdict === "unreadable"
              ? "recorded unreadable"
              : "returned to review";
      setStatus(`${ids.length} cell${ids.length === 1 ? "" : "s"} ${words}.`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function decideIssue(
    id: string,
    kind: IssueDecision["kind"],
    reason: string,
  ) {
    setBusy(true);
    setError("");
    try {
      const next = await apiRequest<import("../../api/models").ReviewState>(
        `/api/v1/revisions/${revision.id}/issues/${id}/decision`,
        { method: "POST", body: JSON.stringify({ kind, reason }) },
      );
      setReviewState(next);
      setStatus(`Issue ${kind === "reopen" ? "reopened" : kind}.`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  function accept(next: Revision, message: string, preserveDraft = false) {
    if (review?.checked_cell_ids.length || review?.current_approval)
      setInvalidation(
        "This edit changed the review evidence. Affected decisions and any approval must be renewed.",
      );
    const position = {
      row: Math.min(selection.focus.row, next.content.rows - 1),
      col: Math.min(selection.focus.col, next.content.columns - 1),
    };
    const target = cellAt(next.content, position);
    setRevision(next);
    onRevision(next);
    const focus = target
      ? { row: target.row, col: target.col }
      : { row: 0, col: 0 };
    setSelection({ anchor: focus, focus });
    if (!preserveDraft) setDraft(draftFor(next.content, target));
    if (target)
      onSource(target.reference, `${target.row + 1}:${target.col + 1}`);
    setStatus(message);
    setError("");
    setConflict(false);
    pending.current = null;
    void client.invalidateQueries({ queryKey: ["subtotals"] });
    void client.invalidateQueries({
      queryKey: ["tables", view.table.document_id],
    });
    void client.invalidateQueries({ queryKey: ["table", view.table.id] });
  }

  async function save(operation: EditOperation) {
    if (busy) return;
    if (
      subtotalDirty &&
      operation.kind !== "confirm_subtotal" &&
      operation.kind !== "remove_subtotal"
    ) {
      notify(
        "Save or discard the subtotal setup before saving this change.",
        "warning",
      );
      return;
    }
    const expected = { ...baseline };
    if (operation.kind === "set_text") expected.text = draft.text;
    if (operation.kind === "set_interpretation")
      expected.interpretation = draft.interpretation;
    if (JSON.stringify(expected) !== JSON.stringify(draft)) {
      setError(
        "Save one edited field at a time, or discard the other changes first.",
      );
      return;
    }
    setBusy(true);
    setStatus("Saving…");
    setError("");
    const signature = JSON.stringify({
      base_revision_id: revision.id,
      operation,
    });
    if (pending.current?.signature !== signature)
      pending.current = { signature, key: idempotencyKey() };
    try {
      const next = await apiRequest<Revision>(
        `/api/v1/tables/${view.table.id}/edits`,
        {
          method: "POST",
          headers: { "Idempotency-Key": pending.current.key },
          body: signature,
        },
      );
      accept(next, "Saved");
    } catch (caught) {
      setStatus("");
      if (caught instanceof ApiError && caught.status === 409) {
        setConflict(true);
        setError(
          "This table changed in another tab. Your unsaved fields are retained. Load the latest revision before saving again.",
        );
      } else setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function reload() {
    setBusy(true);
    setError("");
    try {
      const latest = await apiRequest<TableView>(
        `/api/v1/tables/${view.table.id}`,
      );
      const sameCell = latest.revision.content.cells.find(
        (value) => value.id === cell?.id,
      );
      if (!sameCell) {
        setError(
          "The selected cell was removed. Copy your unsaved text before discarding changes and reopening this table.",
        );
        return;
      }
      const position = { row: sameCell.row, col: sameCell.col };
      setSelection({ anchor: position, focus: position });
      setRevision(latest.revision);
      onRevision(latest.revision);
      client.setQueryData(queryKeys.table(view.table.id), latest);
      await client.invalidateQueries({
        queryKey: queryKeys.review(latest.revision.id),
      });
      setConflict(false);
      setStatus("Latest revision loaded; unsaved fields retained");
      pending.current = null;
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function approve(warnings: boolean): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/api/v1/revisions/${revision.id}/approval`, {
        method: "POST",
        body: JSON.stringify({ allow_warnings: warnings }),
      });
      const next = await apiRequest<import("../../api/models").ReviewState>(
        `/api/v1/revisions/${revision.id}/review`,
      );
      setReviewState(next);
      setStatus(warnings ? "Approved with warnings." : "Table approved.");
      return true;
    } catch (caught) {
      setError(errorMessage(caught));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function restore(historicalId: string) {
    if (busy || dirty || subtotalDirty) return;
    setBusy(true);
    setError("");
    try {
      const next = await apiRequest<Revision>(
        `/api/v1/tables/${view.table.id}/restore`,
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey() },
          body: JSON.stringify({
            base_revision_id: revision.id,
            historical_revision_id: historicalId,
          }),
        },
      );
      accept(next, "Restored as a new draft. Review every cell again.");
      setPreviewRevision(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function removeTable() {
    if (busy || dirty || subtotalDirty) return;
    const ok = await confirm({
      title: "Remove this table from active work?",
      confirmLabel: "Remove table",
      tone: "danger",
      body: (
        <div className="confirm-copy">
          <p>
            Cell reviews, issue decisions and the approval are cleared. Saved
            content, history and existing exports stay with the report.
          </p>
          <p>
            You can restore it from Removed tables while the report is retained.
            Deleting or expiring the report removes that option.
          </p>
        </div>
      ),
    });
    if (!ok) return;
    setBusy(true);
    setError("");
    try {
      const table = await apiRequest<Table>(
        `/api/v1/tables/${view.table.id}/removal`,
        {
          method: "POST",
          body: JSON.stringify({
            removed: true,
            base_revision_id: revision.id,
            expected_version: view.table.removal_version,
          }),
        },
      );
      if (!table.removed_at)
        throw new Error(
          "This table was already restored. Reload before removing it again.",
        );
      onRemoved(table);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const approval = review?.current_approval;
  const reviewed = review?.checked_cell_ids.length ?? 0;
  const total = content.cells.length;
  const issueCells = useMemo(
    () =>
      new Set(
        showChecks
          ? (review?.issues
              .filter(
                (issue) =>
                  !review.issue_decisions?.[issue.fingerprint] ||
                  review.issue_decisions[issue.fingerprint].kind === "reopen",
              )
              .flatMap((issue) => issue.affected_cell_ids) ?? [])
          : [],
      ),
    [review, showChecks],
  );
  const highlightedCells = useMemo(
    () =>
      new Set(
        review?.issues.find((issue) => issue.id === focusedIssue)
          ?.affected_cell_ids ?? [],
      ),
    [review, focusedIssue],
  );
  const dirtyLabel = subtotalDirty
    ? "Unsaved subtotal setup"
    : status || (dirty ? "Unsaved changes" : "");

  const tabs = useMemo(() => {
    const items = [{ id: "cell", label: "Cell" }];
    if (showChecks)
      items.push({
        id: "checks",
        label: "Checks",
      });
    if (!study) items.push({ id: "more", label: "More" });
    return items;
  }, [showChecks, study]);

  return (
    <div className="review-pane" ref={screen}>
      <header className="review-pane-head">
        <div className="review-identity">
          <span className="review-table-glyph" aria-hidden="true">
            <GripVertical size={14} />
          </span>
          <div>
            <h2>
              {view.table.origin === "manual"
                ? "Manual table"
                : "Extracted table"}
            </h2>
            <p className="tiny muted">
              Revision <span className="mono">{revision.id.slice(0, 8)}</span>
              {approval ? (
                <>
                  {" "}
                  ·{" "}
                  <span className="review-approved-inline">
                    <ShieldCheck size={11} aria-hidden="true" />
                    {approval.label === "reviewed"
                      ? "Approved"
                      : "Approved with warnings"}
                  </span>
                </>
              ) : (
                " · Draft"
              )}
            </p>
          </div>
        </div>
        <div className="review-progress-inline">
          <div className="review-progress-copy">
            <span className="num">
              {reviewed}/{total}
            </span>{" "}
            reviewed
            {!!review?.unreadable_count && (
              <span className="review-unreadable">
                {" "}
                · {review.unreadable_count} unreadable
              </span>
            )}
          </div>
          <Progress
            value={reviewed}
            max={total}
            label="Cells reviewed"
            hideLabel
            tone={reviewed === total && total > 0 ? "success" : "accent"}
          />
        </div>
        <div className="review-head-actions">
          {dirtyLabel && (
            <span
              className={`review-dirty-chip ${
                subtotalDirty || status || dirty ? "is-active" : ""
              }`}
              role="status"
            >
              {dirtyLabel}
            </span>
          )}
          {!study && (
            <Button
              size="sm"
              variant="primary"
              icon={<CheckCheck size={15} />}
              disabled={busy || dirty || subtotalDirty || !review}
              onClick={() => setFinishOpen(true)}
            >
              Finish
            </Button>
          )}
        </div>
      </header>

      {(error || conflict || invalidation || reviewQuery.isError) && (
        <div className="review-alerts">
          {error && (
            <InlineNote tone="danger" title="Change not saved">
              <p>{error}</p>
              {conflict && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void reload()}
                >
                  Load latest revision
                </Button>
              )}
            </InlineNote>
          )}
          {invalidation && !error && (
            <InlineNote tone="warning" icon={<AlertTriangle size={15} />}>
              <p>{invalidation}</p>
            </InlineNote>
          )}
          {reviewQuery.isError && !error && (
            <InlineNote tone="warning" icon={<Info size={15} />}>
              <p>
                Review status could not be loaded. {reviewQuery.error.message}
              </p>
              <Button size="sm" onClick={() => void reviewQuery.refetch()}>
                Retry
              </Button>
            </InlineNote>
          )}
        </div>
      )}

      <div className="review-grid-area">
        <TableGrid
          content={content}
          selection={selection}
          checked={new Set(review?.checked_cell_ids ?? [])}
          cellReviews={review?.cell_reviews ?? {}}
          onSelect={(position, extend) => selectPosition(position, extend)}
          issueCells={issueCells}
          highlightedCells={highlightedCells}
          onActivate={() => {
            setTool("cell");
            requestAnimationFrame(() =>
              screen.current
                ?.querySelector<HTMLTextAreaElement>(
                  "textarea[data-cell-text-editor]",
                )
                ?.focus(),
            );
          }}
        />
        <div className="grid-footnote">
          <span>
            <span className="mono">
              {cell ? `${cell.row + 1}:${cell.col + 1}` : "—"}
            </span>{" "}
            · {selected.length} selected
          </span>
          <span className="grid-footnote-hint">
            Arrows move · Shift selects · Enter edits · Ctrl + Enter saves
          </span>
        </div>
      </div>

      <section className="inspector">
        <div className="inspector-bar">
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
            label="Cell, checks and more"
          />
          <div className="inspector-context tiny muted">
            {cell ? (
              <>
                <span className="mono">
                  {cell.row + 1}:{cell.col + 1}
                </span>
                {cell.text ? ` · ${cell.text.slice(0, 42)}` : " · blank"}
              </>
            ) : (
              "No cell selected"
            )}
          </div>
        </div>
        <div
          className="inspector-body"
          role="tabpanel"
          aria-label={`${tool} panel`}
        >
          {tool === "cell" && (
            <CellInspector
              cell={cell}
              content={content}
              selected={selected}
              draft={draft}
              onDraft={setDraft}
              onSave={(operation) => void save(operation)}
              onDiscard={() => {
                setDraft(baseline);
                setStatus("");
                setError("");
              }}
              busy={busy}
              dirty={dirty}
              review={review}
              onVerdict={(verdict, reason) => void markVerdict(verdict, reason)}
              onSource={(reference) =>
                onSource(
                  reference,
                  cell ? `${cell.row + 1}:${cell.col + 1}` : undefined,
                )
              }
              onNextUnchecked={nextUnchecked}
            />
          )}
          {tool === "checks" && review && (
            <ChecksPanel
              review={review}
              busy={busy}
              dirty={dirty}
              onDecision={(id, kind, reason) =>
                void decideIssue(id, kind, reason)
              }
              onInspect={inspectIssue}
              selectedIssue={focusedIssue}
            />
          )}
          {tool === "checks" && !review && !reviewQuery.isError && (
            <p className="small muted" role="status">
              Loading review state…
            </p>
          )}
          {tool === "more" && (
            <MorePanel
              view={view}
              revision={revision}
              review={review}
              cell={cell}
              selected={selected}
              selection={selection}
              busy={busy}
              dirty={dirty}
              subtotalDirty={subtotalDirty}
              onSave={(operation) => void save(operation)}
              onSource={(reference) =>
                onSource(
                  reference,
                  cell ? `${cell.row + 1}:${cell.col + 1}` : undefined,
                )
              }
              onSelect={(id) => {
                const target = content.cells.find((item) => item.id === id);
                if (target)
                  selectPosition(
                    { row: target.row, col: target.col },
                    false,
                    target,
                  );
              }}
              onRestore={(id) => void restore(id)}
              onRemove={() => void removeTable()}
              onSubtotalDirty={setSubtotalDirty}
              onPreview={(value) => setPreviewRevision(value)}
            />
          )}
        </div>
      </section>

      {!study && review && (
        <FinishDialog
          open={finishOpen}
          onOpenChange={setFinishOpen}
          tableId={view.table.id}
          revisionId={revision.id}
          content={content}
          review={review}
          busy={busy}
          exports={exports.data?.items ?? []}
          onApprove={approve}
        />
      )}

      <Dialog
        open={!!previewRevision}
        onOpenChange={(open) => {
          if (!open) setPreviewRevision(null);
        }}
      >
        {previewRevision && (
          <DialogContent
            title={`Historical revision ${previewRevision.id.slice(0, 8)}`}
            description="Read-only content from an earlier revision. Restoring creates a new unchecked draft."
            size="lg"
            footer={
              <>
                <Button
                  variant="ghost"
                  onClick={() => setPreviewRevision(null)}
                >
                  Close
                </Button>
                <Button
                  variant="primary"
                  disabled={busy || dirty || subtotalDirty}
                  onClick={() => void restore(previewRevision.id)}
                >
                  Restore as new draft
                </Button>
              </>
            }
          >
            <p className="small muted mono break">
              checksum {previewRevision.checksum}
            </p>
            <div className="revision-preview">
              {previewRevision.content.cells.map((item) => (
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
