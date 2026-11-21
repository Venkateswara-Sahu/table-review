import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  apiRequest,
  apiText,
  errorMessage,
  idempotencyKey,
} from "../../api/client";
import { queryKeys, useReview, useSubtotals } from "../../api/hooks";
import type {
  ApprovalMode,
  BulkCellReviewInput,
  Cell,
  CellVerdict,
  EditOperation,
  Issue,
  IssueDecision,
  Revision,
  ReviewState,
  SourceReference,
  Table,
  TableView,
} from "../../api/models";
import { useConfirm } from "../../app/confirm";
import { useNotices } from "../../app/notices";
import {
  cellAt,
  firstReviewableCell,
  selectedCells,
  type Position,
  type Selection,
} from "../table/selection";
import type { SubtotalBadgeInfo } from "../table/TableGrid";
import { draftFor, type EditDraft } from "./draft";

export type InlineMove = "down" | "right" | "left" | "none";

export type SubtotalTrace = {
  totalCellId: string;
  addendCellIds: Set<string>;
  label: string;
  origin: "detected" | "declared";
  direction: string;
  state: SubtotalBadgeInfo["state"];
  inputSum: string | null;
  reportedTotal: string | null;
  difference: string | null;
};

export function useTableReview({
  view,
  onSource,
  onRevision,
  onDirty,
  sourceSelection,
  onRemoved,
  checksEnabled,
}: {
  view: TableView;
  onSource: (reference: SourceReference, label?: string) => void;
  onRevision: (revision: Revision) => void;
  onDirty: (dirty: boolean) => void;
  sourceSelection: { id: string; sequence: number } | null;
  onRemoved: (table: Table) => void;
  checksEnabled: boolean;
}) {
  const client = useQueryClient();
  const confirm = useConfirm();
  const { notify, notifyError } = useNotices();
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const committingRef = useRef<string | null>(null);

  const [revision, setRevision] = useState(view.revision);
  const initial = firstReviewableCell(view.revision.content);
  const [selection, setSelection] = useState<Selection>({
    anchor: { row: initial?.row ?? 0, col: initial?.col ?? 0 },
    focus: { row: initial?.row ?? 0, col: initial?.col ?? 0 },
  });
  const [draft, setDraft] = useState<EditDraft>(() =>
    draftFor(view.revision.content, initial),
  );
  const [editingCellId, setEditingCellId] = useState<string | null>(null);
  const [inlineDraft, setInlineDraft] = useState("");
  const [undoStack, setUndoStack] = useState<string[]>([]);
  const [redoStack, setRedoStack] = useState<string[]>([]);

  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [errorTitle, setErrorTitle] = useState("Change not saved");
  const [conflict, setConflict] = useState(false);
  const [invalidation, setInvalidation] = useState("");
  const [busy, setBusy] = useState(false);
  const [subtotalDirty, setSubtotalDirty] = useState(false);
  const [focusedIssue, setFocusedIssue] = useState<string | null>(null);
  const [hoveredSubtotalCellId, setHoveredSubtotalCellId] = useState<
    string | null
  >(null);
  const [previewRevision, setPreviewRevision] = useState<Revision | null>(null);

  const reviewQuery = useReview(revision.id);
  const review = reviewQuery.data;
  const subtotalsQuery = useSubtotals(view.table.id, revision.id, true);

  const content = revision.content;
  const selected = useMemo(
    () => selectedCells(content, selection),
    [content, selection],
  );
  const cell = cellAt(content, selection.focus);
  const baseline = useMemo(() => draftFor(content, cell), [content, cell]);
  const dirty =
    editingCellId !== null ||
    JSON.stringify(baseline) !== JSON.stringify(draft);

  const subtotalByTotalCell = useMemo(() => {
    const map = new Map<string, SubtotalBadgeInfo>();
    const data = subtotalsQuery.data;
    if (!data) return map;
    for (const det of data.detected ?? []) {
      map.set(det.total_cell_id, {
        state: det.state,
        origin: "detected",
        title: `${det.label} (${det.direction}): reported ${det.reported_total}, computed ${det.input_sum}, diff ${det.difference}`,
      });
    }
    for (const item of data.items ?? []) {
      map.set(item.group.total_cell_id, {
        state: item.state,
        origin: "declared",
        title: `${item.group.name} (declared): ${item.state.replaceAll("_", " ")}${
          item.difference != null ? `, diff ${item.difference}` : ""
        }`,
      });
    }
    return map;
  }, [subtotalsQuery.data]);

  const subtotalTracesByTotal = useMemo(() => {
    const map = new Map<string, SubtotalTrace>();
    const data = subtotalsQuery.data;
    if (!data) return map;
    for (const det of data.detected ?? []) {
      map.set(det.total_cell_id, {
        totalCellId: det.total_cell_id,
        addendCellIds: new Set(det.term_cell_ids),
        label: det.label,
        origin: "detected",
        direction: det.direction,
        state: det.state,
        inputSum: det.input_sum ?? null,
        reportedTotal: det.reported_total ?? null,
        difference: det.difference ?? null,
      });
    }
    for (const item of data.items ?? []) {
      map.set(item.group.total_cell_id, {
        totalCellId: item.group.total_cell_id,
        addendCellIds: new Set(item.group.terms.map((term) => term.cell_id)),
        label: item.group.name,
        origin: "declared",
        direction: "declared",
        state: item.state,
        inputSum: item.input_sum ?? null,
        reportedTotal: item.reported_total ?? null,
        difference: item.difference ?? null,
      });
    }
    return map;
  }, [subtotalsQuery.data]);

  const activeSubtotalTrace = useMemo(() => {
    const targetId = hoveredSubtotalCellId ?? cell?.id ?? null;
    if (!targetId) return null;
    return subtotalTracesByTotal.get(targetId) ?? null;
  }, [hoveredSubtotalCellId, cell?.id, subtotalTracesByTotal]);

  const issueCells = useMemo(
    () =>
      new Set(
        checksEnabled
          ? (review?.issues
              .filter(
                (issue) =>
                  !review.issue_decisions?.[issue.fingerprint] ||
                  review.issue_decisions[issue.fingerprint].kind === "reopen",
              )
              .flatMap((issue) => issue.affected_cell_ids) ?? [])
          : [],
      ),
    [review, checksEnabled],
  );

  const highlightedCells = useMemo(
    () =>
      new Set(
        review?.issues.find((issue) => issue.id === focusedIssue)
          ?.affected_cell_ids ?? [],
      ),
    [review, focusedIssue],
  );

  useEffect(() => {
    if (view.revision.id === revision.id) return;
    if (dirty || busy || subtotalDirty) {
      setConflict(true);
      setErrorTitle("New revision available");
      setError(
        "A newer revision arrived while you were editing. Load it to keep your draft, or discard your changes before continuing.",
      );
      return;
    }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceSelection?.sequence]);

  function selectPosition(
    position: Position,
    extend: boolean,
    known?: Cell,
    allowWhenEditing = false,
  ): boolean {
    if (busy) return false;
    if (!allowWhenEditing && editingCellId !== null) return false;
    if (
      !allowWhenEditing &&
      JSON.stringify(baseline) !== JSON.stringify(draft)
    ) {
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

  function applyAcceptedRevision(
    next: Revision,
    message: string,
    opts?: {
      historyMode?: "edit" | "undo" | "redo" | "none";
      moveFocus?: InlineMove;
      focusCellOrigin?: Cell;
    },
  ) {
    const mode = opts?.historyMode ?? "edit";
    if (mode === "edit" && next.id !== revision.id) {
      setUndoStack((prev) => [...prev.slice(-29), revision.id]);
      setRedoStack([]);
    }
    if (
      mode !== "undo" &&
      mode !== "redo" &&
      (review?.checked_cell_ids.length || review?.current_approval)
    ) {
      setInvalidation(
        "This edit changed the review evidence. Affected decisions must be renewed.",
      );
    } else if (mode === "undo" || mode === "redo") {
      setInvalidation("");
    }

    let nextRow = Math.min(selection.focus.row, next.content.rows - 1);
    let nextCol = Math.min(selection.focus.col, next.content.columns - 1);
    const origin = opts?.focusCellOrigin;
    if (origin && opts?.moveFocus && opts.moveFocus !== "none") {
      if (opts.moveFocus === "down") {
        nextRow = Math.min(next.content.rows - 1, origin.row + origin.row_span);
        nextCol = Math.min(next.content.columns - 1, origin.col);
      } else if (opts.moveFocus === "right") {
        nextRow = origin.row;
        nextCol = Math.min(
          next.content.columns - 1,
          origin.col + origin.col_span,
        );
      } else if (opts.moveFocus === "left") {
        nextRow = origin.row;
        nextCol = Math.max(0, origin.col - 1);
      }
    }

    const target = cellAt(next.content, { row: nextRow, col: nextCol });
    setRevision(next);
    onRevision(next);
    const focus = target
      ? { row: target.row, col: target.col }
      : { row: 0, col: 0 };
    setSelection({ anchor: focus, focus });
    setDraft(draftFor(next.content, target));
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
    return target;
  }

  async function save(
    operation: EditOperation,
    opts?: {
      moveFocus?: InlineMove;
      focusCellOrigin?: Cell;
      message?: string;
    },
  ): Promise<Revision | null> {
    if (busy) return null;
    if (
      subtotalDirty &&
      operation.kind !== "confirm_subtotal" &&
      operation.kind !== "remove_subtotal"
    ) {
      notify(
        "Save or discard the subtotal setup before saving this change.",
        "warning",
      );
      return null;
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
      applyAcceptedRevision(next, opts?.message ?? "Saved", {
        historyMode: "edit",
        moveFocus: opts?.moveFocus,
        focusCellOrigin: opts?.focusCellOrigin,
      });
      return next;
    } catch (caught) {
      setStatus("");
      setErrorTitle("Change not saved");
      if (caught instanceof ApiError && caught.status === 409) {
        setConflict(true);
        setError(
          "This table changed in another tab. Your unsaved fields are retained. Load the latest revision before saving again.",
        );
      } else setError(errorMessage(caught));
      return null;
    } finally {
      setBusy(false);
    }
  }

  function startInlineEdit(targetCell: Cell) {
    if (busy) return;
    if (JSON.stringify(baseline) !== JSON.stringify(draft)) {
      notify("Save or discard the tray edit before editing inline.", "warning");
      return;
    }
    selectPosition(
      { row: targetCell.row, col: targetCell.col },
      false,
      targetCell,
      true,
    );
    setEditingCellId(targetCell.id);
    setInlineDraft(targetCell.text);
  }

  function cancelInlineEdit() {
    committingRef.current = null;
    const focusId = editingCellId;
    setEditingCellId(null);
    setInlineDraft("");
    if (focusId) {
      requestAnimationFrame(() =>
        document
          .querySelector<HTMLElement>(`[data-cell-id="${CSS.escape(focusId)}"]`)
          ?.focus(),
      );
    }
  }

  async function commitInlineEdit(move: InlineMove = "none") {
    const activeId = editingCellId;
    if (!activeId || committingRef.current === activeId) return;
    const targetCell = content.cells.find((item) => item.id === activeId);
    committingRef.current = activeId;
    const nextText = inlineDraft;
    setEditingCellId(null);
    setInlineDraft("");

    try {
      if (!targetCell) return;
      if (nextText === targetCell.text) {
        if (move !== "none") {
          const nextPos = { row: targetCell.row, col: targetCell.col };
          if (move === "down")
            nextPos.row = Math.min(
              content.rows - 1,
              targetCell.row + targetCell.row_span,
            );
          else if (move === "right")
            nextPos.col = Math.min(
              content.columns - 1,
              targetCell.col + targetCell.col_span,
            );
          else if (move === "left")
            nextPos.col = Math.max(0, targetCell.col - 1);
          const nextCell = cellAt(content, nextPos);
          if (nextCell) {
            selectPosition(nextPos, false, nextCell, true);
            requestAnimationFrame(() =>
              document
                .querySelector<HTMLElement>(
                  `[data-cell-id="${CSS.escape(nextCell.id)}"]`,
                )
                ?.focus(),
            );
          }
        }
        return;
      }
      const next = await save(
        {
          kind: "set_cell",
          cell_id: targetCell.id,
          text: nextText,
        },
        {
          moveFocus: move,
          focusCellOrigin: targetCell,
          message: "Saved",
        },
      );
      if (next && move !== "none") {
        const dest = cellAt(next.content, {
          row:
            move === "down"
              ? Math.min(
                  next.content.rows - 1,
                  targetCell.row + targetCell.row_span,
                )
              : targetCell.row,
          col:
            move === "right"
              ? Math.min(
                  next.content.columns - 1,
                  targetCell.col + targetCell.col_span,
                )
              : move === "left"
                ? Math.max(0, targetCell.col - 1)
                : targetCell.col,
        });
        if (dest) {
          requestAnimationFrame(() =>
            document
              .querySelector<HTMLElement>(
                `[data-cell-id="${CSS.escape(dest.id)}"]`,
              )
              ?.focus(),
          );
        }
      }
    } finally {
      committingRef.current = null;
    }
  }

  function setReviewState(next: ReviewState, revisionId = revision.id) {
    client.setQueryData(queryKeys.review(revisionId), next);
  }

  async function markVerdict(
    verdict: CellVerdict,
    reason = "",
    options?: { revisionId?: string; allowDirty?: boolean },
  ): Promise<boolean> {
    if (busy || (dirty && !options?.allowDirty)) return false;
    const ids = selected.map((item) => item.id);
    if (!ids.length) return false;
    const revisionId = options?.revisionId ?? revision.id;
    setBusy(true);
    setError("");
    try {
      const next = await apiRequest<ReviewState>(
        `/api/v1/revisions/${revisionId}/cell-review`,
        {
          method: "POST",
          body: JSON.stringify({
            cell_ids: ids,
            verdict,
            reason,
          }),
        },
      );
      setReviewState(next, revisionId);
      const words =
        verdict === "confirmed_value"
          ? "confirmed against source"
          : verdict === "confirmed_blank"
            ? "confirmed blank"
            : verdict === "unreadable"
              ? "recorded unreadable"
              : "returned to review";
      setStatus(`${ids.length} cell${ids.length === 1 ? "" : "s"} ${words}.`);
      return true;
    } catch (caught) {
      setErrorTitle("Decision not recorded");
      setError(errorMessage(caught));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function bulkConfirm(
    scope: BulkCellReviewInput["scope"],
  ): Promise<void> {
    if (busy || dirty || subtotalDirty) return;
    setBusy(true);
    setError("");
    try {
      const body: BulkCellReviewInput = {
        scope,
        cell_ids: scope === "cells" ? selected.map((item) => item.id) : [],
        row: scope === "row" && cell ? cell.row : null,
        col: scope === "column" && cell ? cell.col : null,
        include_blanks: false,
        skip_flagged: true,
      };
      const before = review?.checked_cell_ids.length ?? 0;
      const next = await apiRequest<ReviewState>(
        `/api/v1/revisions/${revision.id}/bulk-review`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
      setReviewState(next, revision.id);
      const added = Math.max(0, next.checked_cell_ids.length - before);
      notify(
        added > 0
          ? `Confirmed ${added} clean cell${added === 1 ? "" : "s"}.`
          : "All eligible clean cells in this scope are already confirmed.",
        "success",
      );
    } catch (caught) {
      setErrorTitle("Bulk confirm failed");
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function saveCellChanges() {
    if (!cell || busy) return null;
    const textChanged = draft.text !== cell.text;
    const interpChanged = draft.interpretation !== cell.interpretation;
    if (!textChanged && !interpChanged) return null;
    return await save({
      kind: "set_cell",
      cell_id: cell.id,
      text: textChanged ? draft.text : undefined,
      interpretation: interpChanged ? draft.interpretation : undefined,
    });
  }

  async function confirmValue() {
    if (busy || !cell) return;
    const prospective = selected.map((item) =>
      item.id === cell.id ? draft.text : item.text,
    );
    if (!prospective.every((value) => value.trim())) {
      setErrorTitle("Value not confirmed");
      setError(
        "Every selected cell needs text before it can be confirmed. Save or clear the empty cell first.",
      );
      return;
    }
    const textChanged = draft.text !== cell.text;
    const interpChanged = draft.interpretation !== cell.interpretation;
    if (textChanged || interpChanged) {
      const next = await save({
        kind: "set_cell",
        cell_id: cell.id,
        text: textChanged ? draft.text : undefined,
        interpretation: interpChanged ? draft.interpretation : undefined,
      });
      if (!next) return;
      const recorded = await markVerdict("confirmed_value", "", {
        revisionId: next.id,
        allowDirty: true,
      });
      if (!recorded)
        setError(
          "The value was saved, but its confirmation was not recorded. Confirm again to record the decision.",
        );
      return;
    }
    await markVerdict("confirmed_value");
  }

  async function decideIssue(
    id: string,
    kind: IssueDecision["kind"],
    reason: string,
  ) {
    setBusy(true);
    setError("");
    try {
      const next = await apiRequest<ReviewState>(
        `/api/v1/revisions/${revision.id}/issues/${id}/decision`,
        { method: "POST", body: JSON.stringify({ kind, reason }) },
      );
      setReviewState(next);
      setStatus(`Issue ${kind === "reopen" ? "reopened" : kind}.`);
    } catch (caught) {
      setErrorTitle("Decision not recorded");
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
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
      requestAnimationFrame(() =>
        document
          .querySelector<HTMLTextAreaElement>("textarea[data-cell-text-editor]")
          ?.focus(),
      );
    } else {
      notify("Every cell has a recorded decision.", "success");
    }
  }

  function nextException() {
    const ordered = [...content.cells].sort(
      (a, b) => a.row - b.row || a.col - b.col,
    );
    const current = ordered.findIndex((value) => value.id === cell?.id);
    const target = [
      ...ordered.slice(current + 1),
      ...ordered.slice(0, current + 1),
    ].find(
      (value) =>
        issueCells.has(value.id) ||
        review?.cell_reviews?.[value.id]?.verdict === "unreadable",
    );
    if (target) {
      selectPosition({ row: target.row, col: target.col }, false, target);
      const matchingIssue = review?.issues.find(
        (issue) =>
          issue.affected_cell_ids.includes(target.id) &&
          (!review.issue_decisions?.[issue.fingerprint] ||
            review.issue_decisions[issue.fingerprint].kind === "reopen"),
      );
      if (matchingIssue) setFocusedIssue(matchingIssue.id);
      requestAnimationFrame(() =>
        document
          .querySelector<HTMLTextAreaElement>("textarea[data-cell-text-editor]")
          ?.focus(),
      );
    } else {
      notify("No open exceptions remaining.", "success");
    }
  }

  async function undo() {
    if (busy || dirty || subtotalDirty || !undoStack.length) return;
    const targetRev = undoStack[undoStack.length - 1];
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
            historical_revision_id: targetRev,
          }),
        },
      );
      setUndoStack((prev) => prev.slice(0, -1));
      setRedoStack((prev) => [...prev.slice(-29), revision.id]);
      applyAcceptedRevision(next, "Undid change", { historyMode: "undo" });
    } catch (caught) {
      setErrorTitle("Undo failed");
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function redo() {
    if (busy || dirty || subtotalDirty || !redoStack.length) return;
    const targetRev = redoStack[redoStack.length - 1];
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
            historical_revision_id: targetRev,
          }),
        },
      );
      setRedoStack((prev) => prev.slice(0, -1));
      setUndoStack((prev) => [...prev.slice(-29), revision.id]);
      applyAcceptedRevision(next, "Redid change", { historyMode: "redo" });
    } catch (caught) {
      setErrorTitle("Redo failed");
      setError(errorMessage(caught));
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
      applyAcceptedRevision(
        next,
        "Restored as a new draft. Review affected cells.",
        { historyMode: "edit" },
      );
      setPreviewRevision(null);
    } catch (caught) {
      setErrorTitle("Restore not completed");
      setError(errorMessage(caught));
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
      setErrorTitle("Latest revision not loaded");
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function approve(
    warnings: boolean,
    mode: ApprovalMode = "standard",
  ): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/api/v1/revisions/${revision.id}/approval`, {
        method: "POST",
        body: JSON.stringify({ allow_warnings: warnings, mode }),
      });
      const next = await apiRequest<ReviewState>(
        `/api/v1/revisions/${revision.id}/review`,
      );
      setReviewState(next);
      setStatus(warnings ? "Approved with warnings." : "Table approved.");
      return true;
    } catch (caught) {
      setErrorTitle("Approval not recorded");
      setError(errorMessage(caught));
      return false;
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
      body: "Cell reviews, issue decisions and the approval are cleared. Saved content, history and existing exports stay with the report. You can restore it from Removed tables while the report is retained.",
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
      setErrorTitle("Removal not completed");
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function copyAs(format: "tsv" | "markdown" | "json") {
    try {
      const path = `/api/v1/tables/${view.table.id}/revisions/${revision.id}/formatted?format=${format}`;
      const text =
        format === "json"
          ? JSON.stringify(await apiRequest<unknown>(path), null, 2)
          : await apiText(path);
      await navigator.clipboard.writeText(text);
      const label =
        format === "tsv"
          ? "TSV for Excel / Sheets"
          : format === "markdown"
            ? "Markdown"
            : "JSON";
      notify(`Copied table as ${label}`, "success");
    } catch (caught) {
      notifyError(caught);
    }
  }

  async function copySelectionAsTsv() {
    if (!selected.length) return;
    const minRow = Math.min(...selected.map((item) => item.row));
    const maxRow = Math.max(...selected.map((item) => item.row));
    const minCol = Math.min(...selected.map((item) => item.col));
    const maxCol = Math.max(...selected.map((item) => item.col));
    const byPos = new Map(
      selected.map((item) => [`${item.row}:${item.col}`, item.text]),
    );
    const lines: string[] = [];
    for (let r = minRow; r <= maxRow; r++) {
      const cols: string[] = [];
      for (let c = minCol; c <= maxCol; c++) {
        const raw = byPos.get(`${r}:${c}`) ?? "";
        cols.push(raw.replaceAll(/\r?\n/g, " ").replaceAll("\t", " "));
      }
      lines.push(cols.join("\t"));
    }
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      notify(
        selected.length === 1
          ? "Copied cell value"
          : `Copied ${selected.length} cells (TSV)`,
        "success",
      );
    } catch (caught) {
      notifyError(caught);
    }
  }

  const actionsRef = useRef({
    nextUnchecked,
    nextException,
    undo,
    redo,
    copySelectionAsTsv,
  });
  actionsRef.current = {
    nextUnchecked,
    nextException,
    undo,
    redo,
    copySelectionAsTsv,
  };

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || editingCellId !== null) return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
          target.closest('[role="dialog"]'))
      )
        return;

      if (
        (event.ctrlKey || event.metaKey) &&
        !event.altKey &&
        !event.shiftKey &&
        event.key.toLowerCase() === "c"
      ) {
        const nativeSelection = window.getSelection()?.toString() ?? "";
        if (
          !nativeSelection &&
          target instanceof HTMLElement &&
          (target.closest(".review-grid-scroll") || target === document.body)
        ) {
          event.preventDefault();
          void actionsRef.current.copySelectionAsTsv();
        }
        return;
      }

      if (
        (event.ctrlKey || event.metaKey) &&
        !event.altKey &&
        event.key.toLowerCase() === "z"
      ) {
        event.preventDefault();
        if (event.shiftKey) void actionsRef.current.redo();
        else void actionsRef.current.undo();
        return;
      }
      if (
        (event.ctrlKey || event.metaKey) &&
        !event.shiftKey &&
        !event.altKey &&
        event.key.toLowerCase() === "y"
      ) {
        event.preventDefault();
        void actionsRef.current.redo();
        return;
      }

      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (
        target instanceof HTMLElement &&
        !target.closest(".review-pane") &&
        target !== document.body
      )
        return;

      const lower = event.key.toLowerCase();
      if (lower === "n") {
        event.preventDefault();
        actionsRef.current.nextUnchecked();
      } else if (lower === "e") {
        event.preventDefault();
        actionsRef.current.nextException();
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [editingCellId]);

  return {
    revision,
    content,
    review,
    reviewQuery,
    subtotalsQuery,
    subtotalByTotalCell,
    activeSubtotalTrace,
    setHoveredSubtotalCellId,
    selection,
    selected,
    cell,
    draft,
    setDraft,
    baseline,
    dirty,
    editingCellId,
    inlineDraft,
    setInlineDraft,
    startInlineEdit,
    cancelInlineEdit,
    commitInlineEdit,
    busy,
    subtotalDirty,
    setSubtotalDirty,
    status,
    setStatus,
    error,
    setError,
    errorTitle,
    conflict,
    invalidation,
    focusedIssue,
    previewRevision,
    setPreviewRevision,
    issueCells,
    highlightedCells,
    canUndo: undoStack.length > 0 && !busy && !dirty && !subtotalDirty,
    canRedo: redoStack.length > 0 && !busy && !dirty && !subtotalDirty,
    selectPosition,
    save,
    saveCellChanges,
    markVerdict,
    bulkConfirm,
    confirmValue,
    decideIssue,
    inspectIssue,
    nextUnchecked,
    nextException,
    undo,
    redo,
    restore,
    reload,
    approve,
    removeTable,
    copyAs,
    copySelectionAsTsv,
  };
}
