import { useEffect, useRef, useState } from "react";
import { ApiError, apiRequest, errorMessage } from "../../api/client";
import type {
  EditOperation,
  Revision,
  ReviewState,
  Snapshot,
  SourceReference,
  TableView,
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
}: {
  view: TableView;
  onSource: (reference: SourceReference) => void;
  onDirty: (dirty: boolean) => void;
  onRevision: (revision: Revision) => void;
}) {
  const [revision, setRevision] = useState(view.revision);
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
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const content = revision.content;
  const cell = content.cells.find(
    (cell) =>
      cell.row <= selection.focus.row &&
      cell.row + cell.row_span > selection.focus.row &&
      cell.col <= selection.focus.col &&
      cell.col + cell.col_span > selection.focus.col,
  );
  const baseline = draftFor(content, cell);
  const dirty = JSON.stringify(baseline) !== JSON.stringify(draft);
  useEffect(() => {
    onDirty(dirty || busy);
  }, [dirty, busy, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);
  useEffect(() => {
    const warning = (event: BeforeUnloadEvent) => {
      if (dirty || busy) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warning);
    return () => window.removeEventListener("beforeunload", warning);
  }, [dirty, busy]);
  useEffect(() => {
    const controller = new AbortController();
    apiRequest<ReviewState>(`/api/v1/revisions/${revision.id}/review`, {
      signal: controller.signal,
    })
      .then(setReview)
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => controller.abort();
  }, [revision.id]);
  function select(position: Position, extend: boolean) {
    if (busy) return;
    if (
      dirty &&
      !window.confirm("Discard unsaved changes before selecting another cell?")
    )
      return;
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
  }
  async function accept(
    next: Revision,
    message: string,
    preserveDraft = false,
  ) {
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
    setRevision(next);
    onRevision(next);
    setSelection({ anchor: position, focus: position });
    if (!preserveDraft) setDraft(draftFor(next.content, selected));
    if (selected) onSource(selected.reference);
    setReview(null);
    const state = await apiRequest<ReviewState>(
      `/api/v1/revisions/${next.id}/review`,
    );
    setReview(state);
    setStatus(message);
    setConflict(false);
    pending.current = null;
  }
  async function save(operation: EditOperation) {
    if (busy) return;
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
  async function restore(id: string) {
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
  return (
    <div className="review-screen">
      <div className="review-status">
        <strong>
          {review?.current_approval
            ? review.current_approval.label === "reviewed"
              ? "Reviewed"
              : "Reviewed with warnings"
            : "Draft"}
        </strong>
        <span role="status">
          {status ||
            (dirty ? "Unsaved changes" : "Compare every cell with its source")}
        </span>
      </div>
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
      <p className="fine-print">
        Arrow keys move between cells. Shift + arrows or Shift + click selects a
        range. Ctrl + Home / End moves to the first / last cell.
      </p>
      <TableGrid
        content={content}
        selection={selection}
        checked={new Set(review?.checked_cell_ids ?? [])}
        onSelect={select}
      />
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
        review={review}
        cells={content.cells}
        selected={selectedCells(content, selection)}
        busy={busy || dirty}
        onChecked={(value) => void checked(value)}
        onApprove={(value) => void approve(value)}
      />
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
      />
      <History
        table={view.table}
        revision={revision}
        busy={busy || dirty}
        onRestore={(id) => void restore(id)}
      />
      <details>
        <summary>Run details</summary>
        <pre>{JSON.stringify(revision.extraction_manifest, null, 2)}</pre>
      </details>
    </div>
  );
}
