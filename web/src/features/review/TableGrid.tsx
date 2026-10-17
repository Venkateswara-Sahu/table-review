import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { Cell, TableContent, ReviewState } from "../../api/models";
import { columnName } from "./cellContext";

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
  cellReviews = {},
  onSelect,
  issueCells,
  highlightedCells,
  onActivate,
}: {
  content: TableContent;
  selection: Selection;
  checked: Set<string>;
  cellReviews?: ReviewState["cell_reviews"];
  onSelect: (position: Position, extend: boolean) => boolean;
  issueCells: Set<string>;
  highlightedCells: Set<string>;
  onActivate: () => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState({
    top: 0,
    left: 0,
    width: 650,
    height: HEIGHT,
  });
  const firstRow = Math.max(0, Math.floor(viewport.top / ROW) - 2);
  const lastRow = Math.min(
    content.rows,
    Math.ceil((viewport.top + viewport.height) / ROW) + 2,
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
      setViewport((value) => ({
        ...value,
        width: element.clientWidth,
        height: element.clientHeight,
      })),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const y = selection.focus.row * ROW,
      x = selection.focus.col * COL;
    if (
      y < element.scrollTop ||
      y + ROW > element.scrollTop + element.clientHeight
    )
      element.scrollTop = y;
    if (
      x < element.scrollLeft ||
      x + COL > element.scrollLeft + element.clientWidth
    )
      element.scrollLeft = x;
  }, [selection.focus.row, selection.focus.col]);
  function navigate(event: KeyboardEvent, cell: Cell) {
    if (event.key === "Enter" || event.key === "F2") {
      event.preventDefault();
      onActivate();
      return;
    }
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
    if (!onSelect(position, event.shiftKey)) return;
    const element = scroller.current!;
    const y = target.row * ROW,
      x = target.col * COL;
    if (
      y < element.scrollTop ||
      y + ROW > element.scrollTop + element.clientHeight
    )
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
      height: element.clientHeight,
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
  const visible = content.cells.filter(
    (cell) =>
      cell.row < lastRow &&
      cell.row + cell.row_span > firstRow &&
      cell.col < lastCol &&
      cell.col + cell.col_span > firstCol,
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
          height: event.currentTarget.clientHeight,
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
        {[...new Set(visible.map((cell) => cell.row))].map((row) => (
          <div
            key={row}
            role="row"
            aria-rowindex={row + 1}
            style={{ display: "contents" }}
          >
            {visible
              .filter((cell) => cell.row === row)
              .map((cell) => {
                const verdict = cellReviews[cell.id]?.verdict ?? "unreviewed";
                const reviewLabel = verdict.replaceAll("_", " ");
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
                    className={`review-cell ${checked.has(cell.id) && verdict !== "unreadable" ? "checked" : ""} ${verdict === "unreadable" || issueCells.has(cell.id) ? "flagged" : ""} ${highlightedCells.has(cell.id) ? "issue-focused" : ""} ${cell.role === "column_header" || cell.role === "row_header" ? "header-cell" : ""}`}
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
                      aria-describedby={`cell-review-${cell.id}`}
                      title={`Row ${cell.row + 1}, column ${cell.col + 1} · ${reviewLabel}\n${cell.text}`}
                      onClick={(event) =>
                        onSelect(
                          { row: cell.row, col: cell.col },
                          event.shiftKey,
                        )
                      }
                      onKeyDown={(event) => navigate(event, cell)}
                    >
                      <span className="cell-address" aria-hidden="true">
                        {columnName(cell.col)}
                        {cell.row + 1}
                      </span>
                      {cell.text || <span className="blank-label">blank</span>}
                      <span
                        id={`cell-review-${cell.id}`}
                        className="cell-review-label"
                      >
                        {reviewLabel}
                      </span>
                      <span className="cell-check" aria-hidden="true">
                        {checked.has(cell.id) && verdict !== "unreadable"
                          ? "✓"
                          : ""}
                      </span>
                    </button>
                  </div>
                );
              })}
          </div>
        ))}
      </div>
    </div>
  );
}
