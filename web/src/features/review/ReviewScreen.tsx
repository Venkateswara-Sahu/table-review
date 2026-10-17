import { useEffect, useRef, useState } from "react";
import { ApiError, apiRequest, errorMessage } from "../../api/client";
import type {
  CellContext,
  EditOperation,
  Revision,
  ReviewState,
  Snapshot,
  SourceReference,
  TableView,
  Table,
} from "../../api/models";
import { draftFor, EditControls, type EditDraft } from "./EditControls";
import {
  TableGrid,
  selectedCells,
  type Position,
  type Selection,
} from "./TableGrid";
import { ReviewActions } from "./ReviewActions";
import { History } from "./History";
import { IssueList } from "./IssueList";
import { ExportControls } from "../exports/ExportControls";
import type { components } from "../../api/types";
import { useStudy } from "./StudyRecorder";
import { CellContextPanel } from "./CellContextPanel";
import { cellAddress } from "./cellContext";
import { SubtotalsPanel } from "./SubtotalsPanel";

function firstCell(view: TableView) {
  return view.revision.content.cells.reduce<
    (typeof view.revision.content.cells)[number] | undefined
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
export function ReviewScreen({
  view,
  onSource,
  onDirty,
  onRevision,
  sourceSelection,
  onRemoved,
}: {
  view: TableView;
  onSource: (reference: SourceReference) => void;
  onDirty: (dirty: boolean) => void;
  onRevision: (revision: Revision) => void;
  sourceSelection: { id: string; sequence: number } | null;
  onRemoved?: (table: Table) => void;
}) {
  const screen = useRef<HTMLDivElement>(null);
  const inspector = useRef<HTMLDivElement>(null);
  const [tool, setTool] = useState("cell");
  function openTool(next: string) {
    setTool(next);
    requestAnimationFrame(() => {
      if (inspector.current) inspector.current.scrollTop = 0;
    });
  }
  const [invalidation, setInvalidation] = useState("");
  const [focusedIssue, setFocusedIssue] = useState<string | null>(null);
  const [revision, setRevision] = useState(view.revision);
  const study = useStudy();
  useEffect(() => {
    try {
      study?.observe(revision);
    } catch (error) {
      setError((error as Error).message);
    }
  }, [study, revision]);
  const initial = firstCell(view);
  const position = { row: initial?.row ?? 0, col: initial?.col ?? 0 };
  const [selection, setSelection] = useState<Selection>({
    anchor: position,
    focus: position,
  });
  const [draft, setDraft] = useState<EditDraft>(() =>
    draftFor(view.revision.content, initial),
  );
  const [review, setReview] = useState<ReviewState | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [subtotalDirty, setSubtotalDirty] = useState(false);
  const [confirmRemoval, setConfirmRemoval] = useState(false);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const acknowledged = useRef<{ revisionId: string; message: string } | null>(
    null,
  );
  const content = revision.content;
  const showChecks =
    study?.config.condition !== "review_without_checks" &&
    study?.config.condition !== "spreadsheet_baseline";
  const cell = content.cells.find(
    (cell) =>
      cell.row <= selection.focus.row &&
      cell.row + cell.row_span > selection.focus.row &&
      cell.col <= selection.focus.col &&
      cell.col + cell.col_span > selection.focus.col,
  );
  const [contextResult, setContextResult] = useState<{
    revisionId: string;
    value: CellContext;
  } | null>(null);
  const [contextError, setContextError] = useState("");
  const [contextAttempt, setContextAttempt] = useState(0);
  const cellId = cell?.id;
  const context =
    contextResult?.revisionId === revision.id &&
    contextResult.value.cell_id === cellId
      ? contextResult.value
      : null;
  useEffect(() => {
    setContextError("");
    if (!cellId || study) return;
    const controller = new AbortController();
    apiRequest<CellContext>(
      `/api/v1/tables/${view.table.id}/revisions/${revision.id}/context/${encodeURIComponent(cellId)}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted)
          setContextResult({ revisionId: revision.id, value });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setContextError(errorMessage(error));
      });
    return () => controller.abort();
  }, [view.table.id, revision.id, cellId, study, contextAttempt]);
  const baseline = draftFor(content, cell);
  const dirty = JSON.stringify(baseline) !== JSON.stringify(draft);
  useEffect(() => {
    onDirty(dirty || busy || subtotalDirty);
  }, [dirty, busy, subtotalDirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);
  useEffect(() => {
    const warning = (event: BeforeUnloadEvent) => {
      if (dirty || busy || subtotalDirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warning);
    return () => window.removeEventListener("beforeunload", warning);
  }, [dirty, busy, subtotalDirty]);
  useEffect(() => {
    const controller = new AbortController();
    apiRequest<ReviewState>(`/api/v1/revisions/${revision.id}/review`, {
      signal: controller.signal,
    })
      .then((value) => {
        setReview(value);
        if (acknowledged.current?.revisionId === revision.id)
          acknowledged.current = null;
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          if (acknowledged.current?.revisionId === revision.id) {
            setStatus(
              `${acknowledged.current.message}; review status unavailable`,
            );
            setError("Your change is saved. Reload to refresh review status.");
          } else setError(errorMessage(error));
        }
      });
    return () => controller.abort();
  }, [revision.id]);
  function select(position: Position, extend: boolean) {
    if (busy) return false;
    if (
      dirty &&
      !window.confirm("Discard unsaved changes before selecting another cell?")
    )
      return false;
    const next = content.cells.find(
      (cell) =>
        cell.row <= position.row &&
        cell.row + cell.row_span > position.row &&
        cell.col <= position.col &&
        cell.col + cell.col_span > position.col,
    );
    setSelection((value) => ({
      anchor: extend ? value.anchor : position,
      focus: position,
    }));
    setDraft(draftFor(content, next));
    if (next) onSource(next.reference);
    setStatus("");
    return true;
  }
  useEffect(() => {
    if (!sourceSelection) return;
    const target = content.cells.find(
      (value) => value.id === sourceSelection.id,
    );
    if (target && select({ row: target.row, col: target.col }, false))
      openTool("cell");
  }, [sourceSelection]);
  function inspectIssue(issue: components["schemas"]["Issue"]) {
    if (busy) return false;
    const affected = content.cells.filter((value) =>
      issue.affected_cell_ids.includes(value.id),
    );
    if (affected.length) {
      const first = affected[0];
      if (!select({ row: first.row, col: first.col }, false)) return false;
    } else if (dirty) {
      if (
        !window.confirm(
          "Discard unsaved changes before inspecting another issue?",
        )
      )
        return false;
      setDraft(baseline);
    }
    if (issue.reference) onSource(issue.reference);
    setFocusedIssue(issue.id);
    if (!issue.reference && !affected.length)
      setStatus(
        "This check has no source location or affected cells. Compare the original page manually.",
      );
    return true;
  }
  async function removeTable() {
    if (busy || dirty || subtotalDirty) return;
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
      onRemoved?.(table);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  function nextUnchecked() {
    const ordered = [...content.cells].sort(
      (a, b) => a.row - b.row || a.col - b.col,
    );
    const current = ordered.findIndex((value) => value.id === cell?.id);
    const target = [
      ...ordered.slice(current + 1),
      ...ordered.slice(0, current + 1),
    ].find((value) => !review?.checked_cell_ids.includes(value.id));
    if (target) {
      select({ row: target.row, col: target.col }, false);
      openTool("cell");
    }
  }
  function accept(next: Revision, message: string, preserveDraft = false) {
    if (review?.checked_cell_ids.length || review?.current_approval)
      setInvalidation(
        "This edit changed the review evidence. Affected checked marks and any approval must be renewed.",
      );
    const position = {
      row: Math.min(selection.focus.row, next.content.rows - 1),
      col: Math.min(selection.focus.col, next.content.columns - 1),
    };
    const selected = next.content.cells.find(
      (cell) =>
        cell.row <= position.row &&
        cell.row + cell.row_span > position.row &&
        cell.col <= position.col &&
        cell.col + cell.col_span > position.col,
    );
    acknowledged.current = { revisionId: next.id, message };
    setRevision(next);
    onRevision(next);
    setSelection({ anchor: position, focus: position });
    if (!preserveDraft) setDraft(draftFor(next.content, selected));
    if (selected) onSource(selected.reference);
    setReview(null);
    setStatus(message);
    setConflict(false);
    pending.current = null;
  }
  async function save(operation: EditOperation) {
    if (busy) return;
    if (
      subtotalDirty &&
      operation.kind !== "confirm_subtotal" &&
      operation.kind !== "remove_subtotal" &&
      !window.confirm(
        "Discard unsaved subtotal setup before saving this change?",
      )
    )
      return;
    // Atomic saves never silently discard other edited fields.
    const expected = { ...baseline };
    if (operation.kind === "set_text") expected.text = draft.text;
    if (operation.kind === "set_interpretation")
      expected.interpretation = draft.interpretation;
    if (operation.kind === "set_headers") {
      expected.headers = draft.headers;
      expected.labels = draft.labels;
    }
    if (operation.kind === "set_notes") expected.notes = draft.notes;
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
      pending.current = { signature, key: crypto.randomUUID() };
    try {
      const next = await apiRequest<Revision>(
        `/api/v1/tables/${view.table.id}/edits`,
        {
          method: "POST",
          headers: { "Idempotency-Key": pending.current.key },
          body: signature,
        },
      );
      await accept(next, "Saved");
    } catch (error) {
      setStatus("");
      if (error instanceof ApiError && error.status === 409) {
        setConflict(true);
        setError(
          "This table changed in another tab. Your unsaved fields are retained. Reload the latest revision before saving again.",
        );
      } else setError(errorMessage(error));
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
      setSelection({
        anchor: { row: sameCell.row, col: sameCell.col },
        focus: { row: sameCell.row, col: sameCell.col },
      });
      setRevision(latest.revision);
      onRevision(latest.revision);
      setReview(
        await apiRequest<ReviewState>(
          `/api/v1/revisions/${latest.revision.id}/review`,
        ),
      );
      setConflict(false);
      setStatus("Latest revision loaded; unsaved fields retained");
      pending.current = null;
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function checked(value: boolean) {
    setBusy(true);
    setError("");
    try {
      setReview(
        await apiRequest<ReviewState>(
          `/api/v1/revisions/${revision.id}/checked`,
          {
            method: "POST",
            body: JSON.stringify({
              cell_ids: selectedCells(content, selection).map(
                (cell) => cell.id,
              ),
              checked: value,
            }),
          },
        ),
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function approve(warnings: boolean) {
    setBusy(true);
    setError("");
    try {
      await apiRequest<Snapshot>(`/api/v1/revisions/${revision.id}/approval`, {
        method: "POST",
        body: JSON.stringify({ allow_warnings: warnings }),
      });
      setReview(
        await apiRequest<ReviewState>(
          `/api/v1/revisions/${revision.id}/review`,
        ),
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function decideCells(
    verdict: components["schemas"]["CellReviewInput"]["verdict"],
    reason = "",
  ) {
    if (busy || dirty) return false;
    setBusy(true);
    setError("");
    try {
      setReview(
        await apiRequest<ReviewState>(
          `/api/v1/revisions/${revision.id}/cell-review`,
          {
            method: "POST",
            body: JSON.stringify({
              cell_ids: selectedCells(content, selection).map(
                (cell) => cell.id,
              ),
              verdict,
              reason,
            }),
          },
        ),
      );
      return true;
    } catch (error) {
      setError(errorMessage(error));
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function restore(id: string) {
    if (busy || dirty || subtotalDirty) return;
    setBusy(true);
    setError("");
    try {
      await accept(
        await apiRequest<Revision>(`/api/v1/tables/${view.table.id}/restore`, {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({
            base_revision_id: revision.id,
            historical_revision_id: id,
          }),
        }),
        "Restored as a new draft",
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function decide(
    id: string,
    kind: components["schemas"]["IssueDecisionRecord"]["kind"],
    reason: string,
  ) {
    setBusy(true);
    setError("");
    try {
      setReview(
        await apiRequest<ReviewState>(
          `/api/v1/revisions/${revision.id}/issues/${id}/decision`,
          { method: "POST", body: JSON.stringify({ kind, reason }) },
        ),
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="review-screen" ref={screen}>
      <div className="review-status">
        <strong>
          {!study && review?.current_approval
            ? review.current_approval.label === "reviewed"
              ? "Reviewed"
              : "Reviewed with warnings"
            : "Draft"}
        </strong>
        <span role="status">
          {subtotalDirty
            ? "Unsaved subtotal setup"
            : status ||
              (dirty
                ? "Unsaved changes"
                : "Compare every cell with its source")}
        </span>
        {!study && onRemoved && (
          <button
            disabled={busy || dirty || subtotalDirty}
            onClick={() => setConfirmRemoval(true)}
          >
            Remove table
          </button>
        )}
      </div>
      {confirmRemoval && (
        <section className="table-removal" aria-label="Remove this table">
          <h3>Remove this table from active work?</h3>
          <p>
            Cell reviews, issue decisions and approval will be cleared. Saved
            content, history and existing exports stay with this report. Restore
            it from Removed tables while the report is retained. Deleting or
            expiring the report removes this recovery option.
          </p>
          <div className="edit-toolbar">
            <button
              disabled={busy || dirty || subtotalDirty}
              onClick={() => void removeTable()}
            >
              Remove from active tables
            </button>
            <button disabled={busy} onClick={() => setConfirmRemoval(false)}>
              Keep table
            </button>
          </div>
        </section>
      )}
      {view.table.origin === "manual" &&
        content.cells.every((item) => !item.text.trim()) && (
          <p className="context-note">
            This is a blank manual table. Enter the values from the selected
            source area; no text was extracted.
          </p>
        )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {conflict && (
        <button disabled={busy} onClick={() => void reload()}>
          Reload latest revision
        </button>
      )}
      {invalidation && (
        <p className="review-invalidation" role="status">
          {invalidation}
        </p>
      )}
      <div className="grid-context">
        <span>
          {cell ? cellAddress(cell) : "Select a cell"}
          {context
            ? ` / ${[...context.row_headers, ...context.column_headers]
                .map((source) => source.text)
                .filter(Boolean)
                .join(" / ")}`
            : ""}
        </span>
        <button
          disabled={
            !review ||
            busy ||
            dirty ||
            review.checked_cell_ids.length === content.cells.length
          }
          onClick={nextUnchecked}
        >
          Next unchecked cell
        </button>
      </div>
      <TableGrid
        content={content}
        selection={selection}
        checked={new Set(review?.checked_cell_ids ?? [])}
        cellReviews={review?.cell_reviews ?? {}}
        onSelect={select}
        issueCells={
          new Set(
            showChecks
              ? (review?.issues
                  .filter(
                    (issue) =>
                      !review.issue_decisions?.[issue.fingerprint] ||
                      review.issue_decisions[issue.fingerprint].kind ===
                        "reopen",
                  )
                  .flatMap((issue) => issue.affected_cell_ids) ?? [])
              : [],
          )
        }
        highlightedCells={
          new Set(
            review?.issues.find((issue) => issue.id === focusedIssue)
              ?.affected_cell_ids ?? [],
          )
        }
        onActivate={() => {
          openTool("cell");
          requestAnimationFrame(() =>
            screen.current
              ?.querySelector<HTMLTextAreaElement>(
                "textarea[data-cell-text-editor]",
              )
              ?.focus(),
          );
        }}
      />
      <div className="keyboard-hint">
        Arrows move · Shift selects a range · Enter edits · Ctrl + Enter saves
      </div>
      <details className="keyboard-help">
        <summary>Keyboard help</summary>
        <section aria-label="Keyboard controls">
          <dl>
            <dt>Arrow keys</dt>
            <dd>
              Move between table cells. Merged cells are crossed as one cell.
            </dd>
            <dt>Shift + arrows / Shift + click</dt>
            <dd>Select a range.</dd>
            <dt>Ctrl + Home / End</dt>
            <dd>Move to the first / last table position.</dd>
            <dt>Enter or F2 in the grid</dt>
            <dd>Open Edit cell and focus the text editor.</dd>
            <dt>Ctrl or Command + Enter in the editor</dt>
            <dd>Save the edited cell text.</dd>
            <dt>Escape in the editor</dt>
            <dd>Discard unsaved editor fields.</dd>
            <dt>Left / right arrows on a review tab</dt>
            <dd>Switch review tools.</dd>
            <dt>Enter in Original page</dt>
            <dd>Go to the entered page. Scroll the source to browse pages.</dd>
            <dt>Escape while selecting a region</dt>
            <dd>Cancel the region selection.</dd>
          </dl>
          <p>
            Use Previous / Next unresolved issue in Checks to move through open
            checks. Each value still needs comparison with its source.
          </p>
        </section>
      </details>
      {dirty && (
        <button
          disabled={busy}
          onClick={() => {
            setDraft(baseline);
            setStatus("");
            setError("");
          }}
        >
          Discard unsaved changes
        </button>
      )}
      <ReviewActions
        study={!!study}
        review={review}
        cells={content.cells}
        selected={selectedCells(content, selection)}
        busy={busy || dirty}
        onChecked={(value) => void checked(value)}
        onVerdict={decideCells}
        onApprove={(value) => void approve(value)}
      />
      <div className="inspector-tabs" role="tablist" aria-label="Review tools">
        {(
          [
            ["cell", "Edit cell"],
            ...(!study
              ? [
                  ["context", "Context"],
                  ["subtotals", "Subtotals"],
                ]
              : []),
            ["structure", "Structure"],
            ...(showChecks
              ? [
                  [
                    "checks",
                    `Checks${review ? ` (${review.unresolved_count})` : ""}`,
                  ],
                ]
              : []),
            ...(!study
              ? [
                  ["history", "History"],
                  ["export", "Export"],
                ]
              : []),
          ] as string[][]
        ).map(([id, label]) => (
          <button
            key={id}
            id={`tab-${id}`}
            role="tab"
            aria-selected={tool === id}
            aria-controls={`panel-${id}`}
            tabIndex={tool === id ? 0 : -1}
            onClick={() => openTool(id)}
            onKeyDown={(event) => {
              if (event.key !== "ArrowRight" && event.key !== "ArrowLeft")
                return;
              event.preventDefault();
              const tabs = Array.from(
                event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>(
                  '[role="tab"]',
                ),
              );
              const next =
                tabs[
                  (tabs.indexOf(event.currentTarget) +
                    (event.key === "ArrowRight" ? 1 : tabs.length - 1)) %
                    tabs.length
                ];
              next.click();
              next.focus();
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="inspector-body" ref={inspector}>
        <section
          role="tabpanel"
          id="panel-cell"
          aria-labelledby="tab-cell"
          hidden={tool !== "cell"}
        >
          <EditControls
            cell={cell}
            content={content}
            selection={selection}
            draft={draft}
            onDraft={(value) => {
              setDraft(value);
              setStatus("");
            }}
            onSave={(operation) => void save(operation)}
            busy={busy}
            dirty={dirty}
            onDiscard={() => {
              setDraft(baseline);
              setStatus("");
              setError("");
            }}
          />
          {cell && (
            <details className="cell-evidence">
              <summary>Source references and table notes</summary>
              <p>
                {content.unit_notes.length
                  ? content.unit_notes.join("; ")
                  : "No table notes. Use Context to confirm source-backed units and scale."}
              </p>
              {(cell.source_references.length
                ? cell.source_references
                : [cell.reference]
              ).map((reference, index) => (
                <button key={index} onClick={() => onSource(reference)}>
                  Source {index + 1}: page {reference.page} ·{" "}
                  {reference.precision} reference
                </button>
              ))}
            </details>
          )}
        </section>
        {!study && (
          <section
            role="tabpanel"
            id="panel-context"
            aria-labelledby="tab-context"
            hidden={tool !== "context"}
          >
            <CellContextPanel
              key={`${revision.id}/${cell?.id ?? "none"}`}
              context={context}
              onRetry={() => setContextAttempt((value) => value + 1)}
              error={contextError}
              selectedIds={selectedCells(content, selection).map(
                (cell) => cell.id,
              )}
              disabled={busy || dirty}
              onSource={onSource}
              onSave={(operation) => void save(operation)}
            />
          </section>
        )}
        {!study && (
          <section
            role="tabpanel"
            id="panel-subtotals"
            aria-labelledby="tab-subtotals"
            hidden={tool !== "subtotals"}
          >
            <SubtotalsPanel
              key={revision.id}
              tableId={view.table.id}
              revisionId={revision.id}
              content={content}
              current={cell}
              selected={selectedCells(content, selection)}
              active={tool === "subtotals"}
              refreshKey={String(review?.generation ?? 0)}
              disabled={busy || dirty}
              onDirty={setSubtotalDirty}
              onSave={(operation) => void save(operation)}
              onSelect={(id) => {
                const target = content.cells.find((cell) => cell.id === id);
                if (target) select({ row: target.row, col: target.col }, false);
              }}
              onContext={() => openTool("context")}
            />
          </section>
        )}
        <section
          role="tabpanel"
          id="panel-structure"
          aria-labelledby="tab-structure"
          hidden={tool !== "structure"}
        >
          <EditControls
            cell={cell}
            content={content}
            selection={selection}
            draft={draft}
            onDraft={(value) => {
              setDraft(value);
              setStatus("");
            }}
            onSave={(operation) => void save(operation)}
            busy={busy}
            dirty={dirty}
            onDiscard={() => {
              setDraft(baseline);
              setStatus("");
              setError("");
            }}
            mode="structure"
          />
        </section>
        <section
          role="tabpanel"
          id="panel-history"
          aria-labelledby="tab-history"
          hidden={tool !== "history"}
        >
          {!study && (
            <History
              active={tool === "history"}
              table={view.table}
              revision={revision}
              busy={busy || dirty || subtotalDirty}
              onRestore={(id) => void restore(id)}
              selectedCellId={cell?.id}
              review={review}
            />
          )}
        </section>
        <section
          role="tabpanel"
          id="panel-export"
          aria-labelledby="tab-export"
          hidden={tool !== "export"}
        >
          {!study && (
            <ExportControls
              tableId={view.table.id}
              revisionId={revision.id}
              disabled={busy || dirty || subtotalDirty || !review}
            />
          )}
        </section>
        <section
          role="tabpanel"
          id="panel-checks"
          aria-labelledby="tab-checks"
          hidden={tool !== "checks"}
        >
          {review &&
            study?.config.condition !== "review_without_checks" &&
            study?.config.condition !== "spreadsheet_baseline" && (
              <IssueList
                key={revision.id}
                review={review}
                busy={busy}
                dirty={dirty}
                onDecision={(id, kind, reason) => void decide(id, kind, reason)}
                onInspect={inspectIssue}
                selectedIssue={focusedIssue}
              />
            )}
        </section>
        {!study && (
          <details>
            <summary>Run details</summary>
            <pre>{JSON.stringify(revision.extraction_manifest, null, 2)}</pre>
          </details>
        )}
      </div>
    </div>
  );
}
