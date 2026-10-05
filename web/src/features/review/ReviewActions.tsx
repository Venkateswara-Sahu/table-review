import type { Cell, ReviewState } from "../../api/models";
export function ReviewActions({
  review,
  cells,
  selected,
  busy,
  onChecked,
  onApprove,
}: {
  review: ReviewState | null;
  cells: Cell[];
  selected: Cell[];
  busy: boolean;
  onChecked: (checked: boolean) => void;
  onApprove: (warnings: boolean) => void;
}) {
  const allChecked =
    review && cells.every((cell) => review.checked_cell_ids.includes(cell.id));
  return (
    <div className="review-actions">
      <p>{selected.length} cells selected</p>
      <div className="edit-toolbar">
        <button
          disabled={busy || !review || !selected.length}
          onClick={() => onChecked(true)}
        >
          Mark selection checked
        </button>
        <button
          disabled={busy || !review || !selected.length}
          onClick={() => onChecked(false)}
        >
          Mark selection unchecked
        </button>
      </div>
      <p>
        {review?.checked_cell_ids.length ?? 0} / {cells.length} cells checked
      </p>
      {review?.checks_not_run && (
        <p className="warning">
          Checks have not run. Approval must retain this warning.
        </p>
      )}
      <div className="edit-toolbar">
        <button
          disabled={busy || !allChecked || !!review?.unresolved_count}
          onClick={() => onApprove(false)}
        >
          Approve reviewed table
        </button>
        <button disabled={busy || !allChecked} onClick={() => onApprove(true)}>
          Approve with warnings
        </button>
      </div>
      <p className="fine-print">
        Approval records your review of this exact revision. It does not
        guarantee error-free data.
      </p>
    </div>
  );
}
