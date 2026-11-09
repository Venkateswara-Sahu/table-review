import type { Cell, CellVerdict, TableContent } from "../../api/models";

export const verdictLabel: Record<CellVerdict, string> = {
  confirmed_value: "Value confirmed",
  confirmed_blank: "Blank confirmed",
  unreadable: "Unreadable",
  unreviewed: "Not reviewed",
};

export const verdictTone: Record<
  CellVerdict,
  "success" | "warning" | "danger" | "neutral"
> = {
  confirmed_value: "success",
  confirmed_blank: "success",
  unreadable: "danger",
  unreviewed: "neutral",
};

const headerRoles = new Set(["row_header", "column_header", "header", "label"]);

function nearest(
  content: TableContent,
  cell: Cell,
  direction: "row" | "column",
) {
  return content.cells
    .filter((value) => {
      if (value.id === cell.id) return false;
      if (direction === "row")
        return (
          value.row === cell.row &&
          value.col < cell.col &&
          (headerRoles.has(value.role) || value.col === 0)
        );
      return (
        value.col === cell.col &&
        value.row < cell.row &&
        (headerRoles.has(value.role) || value.row === 0)
      );
    })
    .sort((a, b) => (direction === "row" ? b.col - a.col : b.row - a.row))[0];
}

/** A human name for a cell, built only from text already in the table. */
export function cellName(content: TableContent, cell: Cell) {
  const row = nearest(content, cell, "row");
  const column = nearest(content, cell, "column");
  const parts = [row?.text.trim(), column?.text.trim()].filter(
    (value): value is string => !!value,
  );
  if (parts.length) return parts.join(", ");
  return `Row ${cell.row + 1}, column ${cell.col + 1}`;
}

export function tableShape(content: TableContent) {
  return `${content.rows} rows, ${content.columns} columns, ${content.cells.length} cells`;
}
