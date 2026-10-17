import type { Cell } from "../../api/models";
export function columnName(index: number): string {
  let label = "";
  for (let value = index + 1; value > 0; value = Math.floor((value - 1) / 26))
    label = String.fromCharCode(65 + ((value - 1) % 26)) + label;
  return label;
}
export function cellAddress(cell: Pick<Cell, "row" | "col">) {
  return `${columnName(cell.col)}${cell.row + 1}`;
}
