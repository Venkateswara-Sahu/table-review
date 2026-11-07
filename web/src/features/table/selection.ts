import type { Cell, TableContent } from "../../api/models";

export type Position = { row: number; col: number };
export type Selection = { anchor: Position; focus: Position };

export function selectionBounds(selection: Selection) {
  return {
    row: Math.min(selection.anchor.row, selection.focus.row),
    col: Math.min(selection.anchor.col, selection.focus.col),
    rows: Math.abs(selection.anchor.row - selection.focus.row) + 1,
    columns: Math.abs(selection.anchor.col - selection.focus.col) + 1,
  };
}

export function selectedCells(
  content: TableContent,
  selection: Selection,
): Cell[] {
  const box = selectionBounds(selection);
  return content.cells.filter(
    (cell) =>
      cell.row < box.row + box.rows &&
      cell.row + cell.row_span > box.row &&
      cell.col < box.col + box.columns &&
      cell.col + cell.col_span > box.col,
  );
}

export function cellAt(
  content: TableContent,
  position: Position,
): Cell | undefined {
  return content.cells.find(
    (cell) =>
      cell.row <= position.row &&
      cell.row + cell.row_span > position.row &&
      cell.col <= position.col &&
      cell.col + cell.col_span > position.col,
  );
}
