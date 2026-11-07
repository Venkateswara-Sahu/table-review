import type { Cell, TableContent } from "../../api/models";

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
