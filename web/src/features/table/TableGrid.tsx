import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Check, CircleAlert, CircleSlash2 } from "lucide-react";
import type { Cell, ReviewState, TableContent } from "../../api/models";
import { columnName } from "./address";
import {
  cellAt,
  selectedCells,
  type Position,
  type Selection,
} from "./selection";

const ROW = 40;
const COL = 132;
const HEIGHT = 420;

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
    width: 680,
    height: HEIGHT,
  });
  // Narrow tables use the full pane width; wide tables keep a readable
  // fixed column so scanning stays dense.
  const colWidth =
    content.columns * COL < viewport.width
      ? Math.floor(viewport.width / content.columns)
      : COL;
  const firstRow = Math.max(0, Math.floor(viewport.top / ROW) - 2);
  const lastRow = Math.min(
    content.rows,
    Math.ceil((viewport.top + viewport.height) / ROW) + 2,
  );
  const firstCol = Math.max(0, Math.floor(viewport.left / colWidth) - 2);
  const lastCol = Math.min(
    content.columns,
    Math.ceil((viewport.left + viewport.width) / colWidth) + 2,
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
      x = selection.focus.col * colWidth;
    if (
      y < element.scrollTop ||
      y + ROW > element.scrollTop + element.clientHeight
    )
      element.scrollTop = y;
    if (
      x < element.scrollLeft ||
      x + colWidth > element.scrollLeft + element.clientWidth
    )
      element.scrollLeft = x;
  }, [selection.focus.row, selection.focus.col, colWidth]);

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
    const target = cellAt(content, position);
    if (!target) return; // A missing slot is not an invented editable cell.
    if (!onSelect(position, event.shiftKey)) return;
    const element = scroller.current!;
    const y = target.row * ROW,
      x = target.col * colWidth;
    if (
      y < element.scrollTop ||
      y + ROW > element.scrollTop + element.clientHeight
    )
      element.scrollTop = y;
    if (
      x < element.scrollLeft ||
      x + colWidth > element.scrollLeft + element.clientWidth
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
        style={{
          width: content.columns * colWidth,
          height: content.rows * ROW,
        }}
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
                const isChecked =
                  checked.has(cell.id) && verdict !== "unreadable";
                const focused =
                  cell.row <= selection.focus.row &&
                  cell.row + cell.row_span > selection.focus.row &&
                  cell.col <= selection.focus.col &&
                  cell.col + cell.col_span > selection.focus.col;
                const isHeader =
                  cell.role === "column_header" ||
                  cell.role === "row_header" ||
                  cell.role === "header";
                const flagged =
                  verdict === "unreadable" || issueCells.has(cell.id);
                return (
                  <div
                    key={cell.id}
                    role="gridcell"
                    aria-rowindex={cell.row + 1}
                    aria-colindex={cell.col + 1}
                    aria-rowspan={cell.row_span}
                    aria-colspan={cell.col_span}
                    aria-selected={selected.has(cell.id)}
                    className={[
                      "review-cell",
                      isChecked && "cell-checked",
                      flagged && "cell-flagged",
                      verdict === "unreadable" && "cell-unreadable",
                      highlightedCells.has(cell.id) && "cell-issue-focused",
                      isHeader && "cell-header",
                      selected.has(cell.id) && "cell-selected",
                      cell.interpretation === "en_us_number" ||
                      cell.interpretation === "percent"
                        ? "cell-numeric"
                        : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    style={{
                      top: cell.row * ROW,
                      left: cell.col * colWidth,
                      width: cell.col_span * colWidth,
                      height: cell.row_span * ROW,
                    }}
                  >
                    <button
                      data-cell-id={cell.id}
                      data-row={cell.row}
                      data-col={cell.col}
                      tabIndex={focused ? 0 : -1}
                      aria-label={`Cell ${columnName(cell.col)}${cell.row + 1}: ${
                        cell.text || "blank"
                      }, ${verdict.replaceAll("_", " ")}`}
                      title={`${columnName(cell.col)}${cell.row + 1} · ${cell.text || "blank"}`}
                      className="cell-button"
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
                      <span className="cell-value">
                        {cell.text || (
                          <span className="cell-blank" aria-label="Blank cell">
                            —
                          </span>
                        )}
                      </span>
                      <span className="cell-state" aria-hidden="true">
                        {verdict === "unreadable" ? (
                          <CircleSlash2 size={11} />
                        ) : flagged ? (
                          <CircleAlert size={11} />
                        ) : isChecked ? (
                          <Check size={11} strokeWidth={2.6} />
                        ) : null}
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
