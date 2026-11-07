import { useEffect, useState } from "react";
import { Columns3, Combine, Rows3, Scissors, Settings2 } from "lucide-react";
import type { Cell, EditOperation, TableContent } from "../../api/models";
import { Button, InlineNote, Input, Textarea } from "../../ui/primitives";
import type { Selection } from "../table/selection";
import { selectionBounds } from "../table/selection";

export function StructurePanel({
  content,
  revisionId,
  selection,
  cell,
  busy,
  dirty,
  onSave,
}: {
  content: TableContent;
  revisionId: string;
  selection: Selection;
  cell?: Cell;
  busy: boolean;
  dirty: boolean;
  onSave: (operation: EditOperation) => void;
}) {
  const box = selectionBounds(selection);
  const [headers, setHeaders] = useState(
    content.header_rows.map((value) => value + 1).join(", "),
  );
  const [labels, setLabels] = useState(
    content.row_label_columns.map((value) => value + 1).join(", "),
  );
  const [notes, setNotes] = useState(content.unit_notes.join("\n"));

  useEffect(() => {
    setHeaders(content.header_rows.map((value) => value + 1).join(", "));
    setLabels(content.row_label_columns.map((value) => value + 1).join(", "));
    setNotes(content.unit_notes.join("\n"));
  }, [
    revisionId,
    content.header_rows,
    content.row_label_columns,
    content.unit_notes,
  ]);

  const savedHeaders = content.header_rows.map((value) => value + 1).join(", ");
  const savedLabels = content.row_label_columns
    .map((value) => value + 1)
    .join(", ");
  const savedNotes = content.unit_notes.join("\n");
  const indices = (value: string) =>
    value.trim()
      ? value
          .split(",")
          .map((item) => Number(item.trim()) - 1)
          .filter((value) => Number.isInteger(value) && value >= 0)
      : [];

  return (
    <div className="structure-panel">
      <InlineNote tone="info" icon={<Settings2 size={15} />}>
        <p>
          Structure edits clear checked cells and the approval. The new revision
          starts with a fresh review.
        </p>
      </InlineNote>

      <section className="structure-section">
        <h4>Rows and columns</h4>
        <div className="structure-grid">
          <Button
            size="sm"
            icon={<Rows3 size={14} />}
            disabled={busy || dirty}
            onClick={() =>
              onSave({ kind: "insert_row", index: selection.focus.row + 1 })
            }
          >
            Insert row after
          </Button>
          <Button
            size="sm"
            variant="danger"
            disabled={busy || dirty || content.rows === 1}
            onClick={() =>
              onSave({ kind: "delete_row", index: selection.focus.row })
            }
          >
            Delete row
          </Button>
          <Button
            size="sm"
            icon={<Columns3 size={14} />}
            disabled={busy || dirty}
            onClick={() =>
              onSave({
                kind: "insert_column",
                index: selection.focus.col + 1,
              })
            }
          >
            Insert column after
          </Button>
          <Button
            size="sm"
            variant="danger"
            disabled={busy || dirty || content.columns === 1}
            onClick={() =>
              onSave({ kind: "delete_column", index: selection.focus.col })
            }
          >
            Delete column
          </Button>
        </div>
        <p className="tiny muted">
          The focused position is row {selection.focus.row + 1}, column{" "}
          {selection.focus.col + 1}.
        </p>
      </section>

      <section className="structure-section">
        <h4>Merged cells</h4>
        <div className="row">
          <Button
            size="sm"
            icon={<Combine size={14} />}
            disabled={busy || dirty || box.rows * box.columns === 1}
            onClick={() => onSave({ kind: "merge", ...box })}
          >
            Merge {box.rows}×{box.columns} selection
          </Button>
          <Button
            size="sm"
            icon={<Scissors size={14} />}
            disabled={
              busy || dirty || !cell || cell.row_span * cell.col_span === 1
            }
            onClick={() => cell && onSave({ kind: "split", cell_id: cell.id })}
          >
            Split cell
          </Button>
        </div>
        <p className="tiny muted">
          Only merge physical cells that represent one value in the source.
        </p>
      </section>

      <section className="structure-section">
        <h4>Headers and units</h4>
        <div className="structure-fields">
          <label className="field">
            <span className="field-label">Header rows</span>
            <Input
              placeholder="e.g. 1, 2"
              value={headers}
              disabled={busy}
              onChange={(event) => setHeaders(event.target.value)}
            />
            <span className="field-hint">
              Original grid indices start at 1. Comma separated.
            </span>
          </label>
          <label className="field">
            <span className="field-label">Row label columns</span>
            <Input
              placeholder="e.g. 1"
              value={labels}
              disabled={busy}
              onChange={(event) => setLabels(event.target.value)}
            />
            <span className="field-hint">Leave empty for none.</span>
          </label>
        </div>
        <Button
          size="sm"
          disabled={
            busy ||
            dirty ||
            (headers === savedHeaders && labels === savedLabels)
          }
          onClick={() =>
            onSave({
              kind: "set_headers",
              header_rows: indices(headers),
              row_label_columns: indices(labels),
            })
          }
        >
          Save header roles
        </Button>
      </section>

      <section className="structure-section">
        <h4>Raw unit notes</h4>
        <Textarea
          aria-label="Raw unit notes"
          rows={3}
          disabled={busy}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
        <Button
          size="sm"
          disabled={busy || dirty || notes === savedNotes}
          onClick={() =>
            onSave({
              kind: "set_notes",
              unit_notes: notes === "" ? [] : notes.split("\n"),
            })
          }
        >
          Save unit notes
        </Button>
        <p className="tiny muted">
          Keep the source wording. Notes are evidence, not calculated
          conversions.
        </p>
      </section>
    </div>
  );
}
