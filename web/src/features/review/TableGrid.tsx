import { useEffect, useRef, useState, type KeyboardEvent } from "react";
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

const ROW = 48,
  COL = 145,
  HEIGHT = 360;
export function TableGrid({
  content,
  selection,
  checked,
  onSelect,
}: {
  content: TableContent;
  selection: Selection;
  checked: Set<string>;
  onSelect: (position: Position, extend: boolean) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState({ top: 0, left: 0, width: 650 });
  const firstRow = Math.max(0, Math.floor(viewport.top / ROW) - 2);
  const lastRow = Math.min(
    content.rows,
    Math.ceil((viewport.top + HEIGHT) / ROW) + 2,
  );
  const firstCol = Math.max(0, Math.floor(viewport.left / COL) - 2);
  const lastCol = Math.min(
    content.columns,
    Math.ceil((viewport.left + viewport.width) / COL) + 2,
  );
  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const observer = new ResizeObserver(() =>
      setViewport((value) => ({ ...value, width: element.clientWidth })),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  function navigate(event: KeyboardEvent, cell: Cell) {
    const position = { ...selection.focus };
    if (event.ctrlKey && event.key === "End") {
      position.row = content.rows - 1;
      position.col = content.columns - 1;
    } else if (event.ctrlKey && event.key === "Home") {
      position.row = 0;
      position.col = 0;
    } else if (event.key === "ArrowRight")
      position.col = event.shiftKey
        ? position.col + 1
        : cell.col + cell.col_span;
    else if (event.key === "ArrowLeft")
      position.col = event.shiftKey ? position.col - 1 : cell.col - 1;
    else if (event.key === "ArrowDown")
      position.row = event.shiftKey
        ? position.row + 1
        : cell.row + cell.row_span;
    else if (event.key === "ArrowUp")
      position.row = event.shiftKey ? position.row - 1 : cell.row - 1;
    else return;
    event.preventDefault();
    position.row = Math.max(0, Math.min(content.rows - 1, position.row));
    position.col = Math.max(0, Math.min(content.columns - 1, position.col));
    const target = content.cells.find(
      (value) =>
        value.row <= position.row &&
        value.row + value.row_span > position.row &&
        value.col <= position.col &&
        value.col + value.col_span > position.col,
    );
    if (!target) return; // A missing slot is not an invented editable cell.
    onSelect(position, event.shiftKey);
    const element = scroller.current!;
    const y = target.row * ROW,
      x = target.col * COL;
    if (y < element.scrollTop || y + ROW > element.scrollTop + HEIGHT)
      element.scrollTop = y;
    if (
      x < element.scrollLeft ||
      x + COL > element.scrollLeft + element.clientWidth
    )
      element.scrollLeft = x;
    setViewport({
      top: element.scrollTop,
      left: element.scrollLeft,
      width: element.clientWidth,
    });
    requestAnimationFrame(() =>
      scroller.current
        ?.querySelector<HTMLButtonElement>(
          `[data-cell-id="${CSS.escape(target.id)}"]`,
        )
        ?.focus(),
    );
  }
  const selected = new Set(
    selectedCells(content, selection).map((cell) => cell.id),
  );
  return (
    <div
      className="review-grid-scroll"
      ref={scroller}
      onScroll={(event) =>
        setViewport({
          top: event.currentTarget.scrollTop,
          left: event.currentTarget.scrollLeft,
          width: event.currentTarget.clientWidth,
        })
      }
    >
      <div
        role="grid"
        aria-label="Review table"
        aria-rowcount={content.rows}
        aria-colcount={content.columns}
        className="review-grid"
        style={{ width: content.columns * COL, height: content.rows * ROW }}
      >
        {content.cells
          .filter(
            (cell) =>
              cell.row < lastRow &&
              cell.row + cell.row_span > firstRow &&
              cell.col < lastCol &&
              cell.col + cell.col_span > firstCol,
          )
          .map((cell) => {
            const focused =
              cell.row <= selection.focus.row &&
              cell.row + cell.row_span > selection.focus.row &&
              cell.col <= selection.focus.col &&
              cell.col + cell.col_span > selection.focus.col;
            return (
              <div
                key={cell.id}
                role="gridcell"
                aria-rowindex={cell.row + 1}
                aria-colindex={cell.col + 1}
                aria-rowspan={cell.row_span}
                aria-colspan={cell.col_span}
                aria-selected={selected.has(cell.id)}
                className={`review-cell ${checked.has(cell.id) ? "checked" : ""} ${cell.role === "column_header" || cell.role === "row_header" ? "header-cell" : ""}`}
                style={{
                  top: cell.row * ROW,
                  left: cell.col * COL,
                  width: cell.col_span * COL,
                  height: cell.row_span * ROW,
                }}
              >
                <button
                  data-cell-id={cell.id}
                  data-row={cell.row}
                  data-col={cell.col}
                  tabIndex={focused ? 0 : -1}
                  aria-label={`Source: ${cell.text || "blank cell"}`}
                  title={`Row ${cell.row + 1}, column ${cell.col + 1} · ${checked.has(cell.id) ? "checked" : "unchecked"}\n${cell.text}`}
                  onClick={(event) =>
                    onSelect({ row: cell.row, col: cell.col }, event.shiftKey)
                  }
                  onKeyDown={(event) => navigate(event, cell)}
                >
                  {cell.text || <span className="blank-label">blank</span>}
                  <span className="cell-check" aria-hidden="true">
                    {checked.has(cell.id) ? "✓" : ""}
                  </span>
                </button>
              </div>
            );
          })}
      </div>
    </div>
  );
}
