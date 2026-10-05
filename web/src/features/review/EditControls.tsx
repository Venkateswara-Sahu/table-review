import { useId } from "react";
import type { Cell, EditOperation, TableContent } from "../../api/models";
import { selectionBounds, type Selection } from "./TableGrid";

export type EditDraft = {
  text: string;
  interpretation: Cell["interpretation"];
  headers: string;
  labels: string;
  notes: string;
};
export function draftFor(content: TableContent, cell?: Cell): EditDraft {
  return {
    text: cell?.text ?? "",
    interpretation: cell?.interpretation ?? "text",
    headers: content.header_rows.map((value) => value + 1).join(", "),
    labels: content.row_label_columns.map((value) => value + 1).join(", "),
    notes: content.unit_notes.join("\n"),
  };
}
export function EditControls({
  cell,
  content,
  selection,
  draft,
  onDraft,
  onSave,
  busy,
  dirty,
}: {
  cell?: Cell;
  content: TableContent;
  selection: Selection;
  draft: EditDraft;
  onDraft: (value: EditDraft) => void;
  onSave: (operation: EditOperation) => void;
  busy: boolean;
  dirty: boolean;
}) {
  const id = useId();
  const box = selectionBounds(selection);
  const indices = (value: string) =>
    value.trim() ? value.split(",").map((item) => Number(item.trim()) - 1) : [];
  return (
    <div className="edit-controls">
      {cell && (
        <>
          <p className="fine-print">
            Row {cell.row + 1}, column {cell.col + 1} · {cell.row_span} ×{" "}
            {cell.col_span} span
          </p>
          <label htmlFor={`${id}-text`}>Cell text</label>
          <textarea
            disabled={busy}
            id={`${id}-text`}
            value={draft.text}
            onChange={(event) =>
              onDraft({ ...draft, text: event.target.value })
            }
          />
          <button
            className="primary"
            disabled={busy || draft.text === cell.text}
            onClick={() =>
              onSave({ kind: "set_text", cell_id: cell.id, text: draft.text })
            }
          >
            Save text
          </button>
          <p className="fine-print raw-literal">
            Original literal: {cell.raw_text || "(blank)"}
          </p>
          <label>
            Value interpretation
            <select
              disabled={busy}
              value={draft.interpretation}
              onChange={(event) =>
                onDraft({
                  ...draft,
                  interpretation: event.target.value as Cell["interpretation"],
                })
              }
            >
              <option value="text">Text (preserve literal)</option>
              <option value="en_us_number">English US number</option>
              <option value="percent">Percent (explicit % only)</option>
              <option value="uncertain">Uncertain</option>
            </select>
          </label>
          <button
            disabled={busy || draft.interpretation === cell.interpretation}
            onClick={() =>
              onSave({
                kind: "set_interpretation",
                cell_id: cell.id,
                interpretation: draft.interpretation,
              })
            }
          >
            Save interpretation
          </button>
        </>
      )}
      <details>
        <summary>Table structure</summary>
        <p className="fine-print">
          Save or discard edited fields first. Structure changes clear checked
          cells and approval.
        </p>
        <div className="edit-toolbar">
          <button
            disabled={busy || dirty}
            onClick={() =>
              onSave({ kind: "insert_row", index: selection.focus.row + 1 })
            }
          >
            Insert row after
          </button>
          <button
            disabled={busy || dirty || content.rows === 1}
            onClick={() =>
              onSave({ kind: "delete_row", index: selection.focus.row })
            }
          >
            Delete row
          </button>
          <button
            disabled={busy || dirty}
            onClick={() =>
              onSave({ kind: "insert_column", index: selection.focus.col + 1 })
            }
          >
            Insert column after
          </button>
          <button
            disabled={busy || dirty || content.columns === 1}
            onClick={() =>
              onSave({ kind: "delete_column", index: selection.focus.col })
            }
          >
            Delete column
          </button>
          <button
            disabled={busy || dirty || box.rows * box.columns === 1}
            onClick={() => onSave({ kind: "merge", ...box })}
          >
            Merge selection
          </button>
          <button
            disabled={
              busy || dirty || !cell || cell.row_span * cell.col_span === 1
            }
            onClick={() => cell && onSave({ kind: "split", cell_id: cell.id })}
          >
            Split cell
          </button>
        </div>
      </details>
      <details>
        <summary>Headers and units</summary>
        <label>
          Header rows
          <input
            disabled={busy}
            placeholder="e.g. 1, 2"
            value={draft.headers}
            onChange={(event) =>
              onDraft({ ...draft, headers: event.target.value })
            }
          />
        </label>
        <label>
          Row label columns
          <input
            disabled={busy}
            placeholder="e.g. 1"
            value={draft.labels}
            onChange={(event) =>
              onDraft({ ...draft, labels: event.target.value })
            }
          />
        </label>
        <p className="fine-print">
          Original grid indices start at 1. Use commas; leave empty for none.
        </p>
        <button
          disabled={
            busy ||
            (draft.headers === draftFor(content).headers &&
              draft.labels === draftFor(content).labels)
          }
          onClick={() =>
            onSave({
              kind: "set_headers",
              header_rows: indices(draft.headers),
              row_label_columns: indices(draft.labels),
            })
          }
        >
          Save header roles
        </button>
        <label htmlFor={`${id}-notes`}>Raw unit notes</label>
        <textarea
          disabled={busy}
          id={`${id}-notes`}
          value={draft.notes}
          onChange={(event) => onDraft({ ...draft, notes: event.target.value })}
        />
        <button
          disabled={busy || draft.notes === content.unit_notes.join("\n")}
          onClick={() =>
            onSave({
              kind: "set_notes",
              unit_notes: draft.notes === "" ? [] : draft.notes.split("\n"),
            })
          }
        >
          Save unit notes
        </button>
      </details>
    </div>
  );
}
