import { useRef, useState } from "react";
import type { Cell, ReviewState } from "../../api/models";
import type { components } from "../../api/types";
type Verdict = components["schemas"]["CellReviewInput"]["verdict"];
export function ReviewActions({
  review,
  cells,
  selected,
  busy,
  onChecked,
  onVerdict,
  onApprove,
  study = false,
}: {
  review: ReviewState | null;
  cells: Cell[];
  selected: Cell[];
  busy: boolean;
  onChecked: (checked: boolean) => void;
  onVerdict: (verdict: Verdict, reason?: string) => Promise<boolean>;
  onApprove: (warnings: boolean) => void;
  study?: boolean;
}) {
  const [reason, setReason] = useState("");
  const unreadablePanel = useRef<HTMLDetailsElement>(null);
  const allChecked =
    review && cells.every((cell) => review.checked_cell_ids.includes(cell.id));
  return (
    <div className="review-actions">
      <p>{selected.length} cells selected</p>
      <div className="edit-toolbar">
        <button
          disabled={
            busy ||
            !review ||
            !selected.length ||
            selected.some((cell) => !cell.text.trim())
          }
          onClick={() => onChecked(true)}
        >
          Confirm values
        </button>
        <button
          disabled={
            busy ||
            !review ||
            !selected.length ||
            selected.some((cell) => !!cell.text.trim())
          }
          onClick={() => void onVerdict("confirmed_blank")}
        >
          Confirm blank
        </button>
        <button
          disabled={busy || !review || !selected.length}
          onClick={() => onChecked(false)}
        >
          Clear review
        </button>
      </div>
      <p>
        {review?.checked_cell_ids.length ?? 0} / {cells.length} cells reviewed
      </p>
      <details className="unreadable-controls" ref={unreadablePanel}>
        <summary>Unreadable source</summary>
        <p className="fine-print">
          Use this when the source cannot be read. The draft stays unchanged and
          retains a warning.
        </p>
        <label htmlFor="unreadable-reason">Why is the source unreadable?</label>
        <textarea
          id="unreadable-reason"
          disabled={busy}
          value={reason}
          maxLength={1000}
          onChange={(event) => setReason(event.target.value)}
        />
        <button
          disabled={busy || !review || !selected.length || !reason.trim()}
          onClick={async () => {
            if (await onVerdict("unreadable", reason)) {
              setReason("");
              if (unreadablePanel.current) unreadablePanel.current.open = false;
            }
          }}
        >
          Record unreadable
        </button>
      </details>
      {!!review?.unreadable_count && (
        <p className="warning">
          {review.unreadable_count} unreadable cells remain. Approval and
          exports must retain this warning.
        </p>
      )}
      {!study && review?.checks_not_run && (
        <p className="warning">
          Checks have not run. Approval must retain this warning.
        </p>
      )}
      {!study && (
        <details className="approval-controls">
          <summary>Approve table</summary>
          <div className="edit-toolbar">
            <button
              disabled={busy || !allChecked || !!review?.unresolved_count}
              onClick={() => onApprove(false)}
            >
              Approve reviewed table
            </button>
            <button
              disabled={busy || !allChecked}
              onClick={() => onApprove(true)}
            >
              Approve with warnings
            </button>
          </div>
          <p className="fine-print">
            Review every cell first, including empty positions. Unreadable
            values require approval with warnings.
          </p>
        </details>
      )}
    </div>
  );
}
