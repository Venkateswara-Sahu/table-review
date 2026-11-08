import { useEffect, useId, useState } from "react";
import { Check, CircleSlash2, RotateCcw, ScanText } from "lucide-react";
import type {
  Cell,
  CellVerdict,
  ReviewState,
  TableContent,
} from "../../api/models";
import { Button, InlineNote, Textarea } from "../../ui/primitives";
import { cellAddress } from "../table/address";
import { cellName, verdictLabel } from "./cellMeta";
import type { EditDraft } from "./draft";

export function VerdictTray({
  cell,
  content,
  selected,
  review,
  draft,
  onDraft,
  onDiscard,
  onConfirm,
  onVerdict,
  onNextUnchecked,
  busy,
  dirty,
}: {
  cell?: Cell;
  content: TableContent;
  selected: Cell[];
  review?: ReviewState;
  draft: EditDraft;
  onDraft: (value: EditDraft) => void;
  onDiscard: () => void;
  onConfirm: () => void;
  onVerdict: (verdict: CellVerdict, reason?: string) => void;
  onNextUnchecked: () => void;
  busy: boolean;
  dirty: boolean;
}) {
  const id = useId();
  const [reason, setReason] = useState("");
  const [unreadableOpen, setUnreadableOpen] = useState(false);
  const selectionKey = selected.map((item) => item.id).join(",");

  useEffect(() => {
    setReason("");
    setUnreadableOpen(false);
  }, [selectionKey]);

  if (!cell) {
    return (
      <div className="verdict verdict-empty" aria-label="Cell verdict">
        <p className="muted small">
          Select a cell in the table to record a decision.
        </p>
      </div>
    );
  }

  const verdict = review?.cell_reviews?.[cell.id]?.verdict ?? "unreviewed";
  const anyValue = selected.some((item) => !!item.text.trim());
  const multiple = selected.length > 1;
  // The focused cell uses the tray draft; every other selected cell uses its
  // saved text. A single blank cell can be confirmed once the tray fills it.
  const confirmable = selected.every((item) =>
    (item.id === cell.id ? draft.text : item.text).trim(),
  );

  return (
    <div className="verdict" aria-label="Cell verdict" data-decision-form>
      <div className="verdict-head">
        <span className="cell-badge mono">{cellAddress(cell)}</span>
        <strong className="verdict-cell">{cellName(content, cell)}</strong>
        {multiple && (
          <span className="verdict-scope">{selected.length} selected</span>
        )}
        {draft.text !== cell.text && (
          <span className="verdict-unsaved">Unsaved edit</span>
        )}
        <span className="verdict-status">
          <span className={`dot ${verdict === "unreviewed" ? "" : verdict}`} />
          {verdictLabel[verdict]}
        </span>
        {verdict !== "unreviewed" && (
          <button
            type="button"
            className="link-button verdict-clear"
            disabled={busy || dirty}
            onClick={() => onVerdict("unreviewed")}
          >
            <RotateCcw size={11} aria-hidden="true" />
            Clear decision
          </button>
        )}
      </div>

      <div className="verdict-body">
        <Textarea
          id={`${id}-value`}
          data-cell-text-editor
          className="verdict-input"
          aria-label="Cell value"
          value={draft.text}
          rows={1}
          spellCheck={false}
          disabled={busy}
          onChange={(event) => onDraft({ ...draft, text: event.target.value })}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              onDiscard();
            }
            if (
              (event.ctrlKey || event.metaKey) &&
              event.key === "Enter" &&
              !busy
            ) {
              event.preventDefault();
              onConfirm();
            }
          }}
        />
        <Button
          size="sm"
          variant="ink"
          className="verdict-confirm"
          disabled={busy || !confirmable}
          icon={<Check size={14} />}
          onClick={onConfirm}
        >
          {multiple ? "Confirm selection" : "Confirm value"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          aria-label={
            multiple ? "Confirm blanks for selection" : "Confirm blank"
          }
          disabled={busy || dirty || anyValue}
          onClick={() => onVerdict("confirmed_blank")}
        >
          Blank
        </Button>
        <Button
          size="sm"
          variant="ghost"
          aria-label={multiple ? "Mark selection unreadable" : "Unreadable"}
          aria-expanded={unreadableOpen}
          disabled={busy || dirty}
          icon={<CircleSlash2 size={14} />}
          onClick={() => setUnreadableOpen((value) => !value)}
        >
          Unreadable
        </Button>
        <button
          type="button"
          className="verdict-next"
          disabled={
            busy ||
            (review?.checked_cell_ids.length ?? 0) === content.cells.length
          }
          aria-label="Next unchecked cell"
          onClick={onNextUnchecked}
        >
          Next cell
          <kbd>N</kbd>
        </button>
      </div>

      <div className="verdict-foot">
        <span className="verdict-hint">
          {dirty
            ? "Confirm saves the edit, then records the decision."
            : "Ctrl + Enter confirms without leaving the cell."}
        </span>
      </div>

      {unreadableOpen && (
        <InlineNote tone="warning" icon={<ScanText size={15} />}>
          <div className="unreadable-form">
            <label className="field-label" htmlFor={`${id}-reason`}>
              Why is the source unreadable?
            </label>
            <Textarea
              id={`${id}-reason`}
              rows={2}
              maxLength={1000}
              value={reason}
              disabled={busy || dirty}
              onChange={(event) => setReason(event.target.value)}
            />
            <div className="row">
              <Button
                size="sm"
                variant="ink"
                disabled={busy || dirty || !reason.trim()}
                onClick={() => {
                  onVerdict("unreadable", reason.trim());
                  setReason("");
                  setUnreadableOpen(false);
                }}
              >
                Record unreadable
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setUnreadableOpen(false)}
              >
                Cancel
              </Button>
            </div>
            <p className="tiny muted">
              The draft text stays unchanged. The cell keeps a warning and
              approval must be recorded with warnings.
            </p>
          </div>
        </InlineNote>
      )}
    </div>
  );
}
