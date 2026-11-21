import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Check, CircleAlert, CircleSlash2 } from "lucide-react";
import type { Cell, ReviewState, TableContent } from "../../api/models";
import { columnName } from "./address";
import {
  cellAt,
  selectedCells,
  type Position,
  type Selection,
} from "./selection";

export type SubtotalBadgeInfo = {
  state:
    | "match"
    | "rounding_match"
    | "difference"
    | "needs_confirmation"
    | "unavailable";
  title: string;
  origin: "declared" | "detected";
};

function clamp(min: number, value: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function isNumericInterpretation(interp: Cell["interpretation"]) {
  return (
    interp === "en_us_number" ||
    interp === "eu_number" ||
    interp === "indian_number" ||
    interp === "dash_zero" ||
    interp === "percent"
  );
}

function computeColumnWidths(content: TableContent): number[] {
  const maxChars = Array.from({ length: content.columns }, () => 4);
  const numericCounts = Array.from({ length: content.columns }, () => 0);
  const bodyCounts = Array.from({ length: content.columns }, () => 0);
  const headerSet = new Set(content.header_rows);
  const labelCols = new Set(content.row_label_columns);

  for (const cell of content.cells) {
    const perCol = Math.ceil(cell.text.length / Math.max(1, cell.col_span));
    for (
      let c = cell.col;
      c < Math.min(content.columns, cell.col + cell.col_span);
      c++
    ) {
      if (perCol > maxChars[c]) maxChars[c] = perCol;
      if (!headerSet.has(cell.row) && cell.text.trim()) {
        bodyCounts[c]++;
        if (isNumericInterpretation(cell.interpretation)) numericCounts[c]++;
      }
    }
  }

  return maxChars.map((len, col) => {
    if (labelCols.has(col)) {
      return clamp(180, Math.round(len * 7.6 + 34), 340);
    }
    const mostlyNumeric =
      bodyCounts[col] > 0 && numericCounts[col] >= bodyCounts[col] * 0.5;
    if (mostlyNumeric) {
      return clamp(122, Math.round(len * 8.6 + 48), 210);
    }
    return clamp(118, Math.round(len * 7.5 + 30), 260);
  });
}

const VIRTUAL_ROW_THRESHOLD = 60;
const ESTIMATED_ROW_HEIGHT = 38;
const VIRTUAL_OVERSCAN = 8;

export function TableGrid({
  tableKey,
  content,
  selection,
  checked,
  cellReviews = {},
  onSelect,
  issueCells,
  highlightedCells,
  onActivate,
  editingCellId = null,
  inlineDraft = "",
  onStartEdit,
  onInlineDraftChange,
  onCommitInline,
  onCancelInline,
  subtotalByTotalCell,
  tracedTotalCellId = null,
  tracedAddendCells,
  onHoverSubtotalCell,
  visibleRowIndices = null,
}: {
  tableKey?: string;
  content: TableContent;
  selection: Selection;
  checked: Set<string>;
  cellReviews?: ReviewState["cell_reviews"];
  onSelect: (position: Position, extend: boolean) => boolean;
  issueCells: Set<string>;
  highlightedCells: Set<string>;
  onActivate: () => void;
  editingCellId?: string | null;
  inlineDraft?: string;
  onStartEdit?: (cell: Cell) => void;
  onInlineDraftChange?: (text: string) => void;
  onCommitInline?: (move?: "down" | "right" | "left" | "none") => void;
  onCancelInline?: () => void;
  subtotalByTotalCell?: Map<string, SubtotalBadgeInfo>;
  tracedTotalCellId?: string | null;
  tracedAddendCells?: Set<string>;
  onHoverSubtotalCell?: (cellId: string | null) => void;
  visibleRowIndices?: Set<number> | null;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const inlineInput = useRef<HTMLInputElement>(null);
  const rowRefs = useRef<Map<number, HTMLTableRowElement>>(new Map());
  const [headerTops, setHeaderTops] = useState<Record<number, number>>({});
  const [colOverrides, setColOverrides] = useState<Record<number, number>>({});
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(640);
  const [viewportWidth, setViewportWidth] = useState(800);

  useEffect(() => {
    setColOverrides({});
  }, [tableKey, content.columns]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const update = () => {
      if (el.clientHeight > 0) setViewportHeight(el.clientHeight);
      if (el.clientWidth > 0) setViewportWidth(el.clientWidth);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const autoColWidths = useMemo(() => computeColumnWidths(content), [content]);
  const colWidths = useMemo(
    () => autoColWidths.map((w, idx) => colOverrides[idx] ?? w),
    [autoColWidths, colOverrides],
  );
  const totalWidth = useMemo(
    () => colWidths.reduce((sum, w) => sum + w, 0),
    [colWidths],
  );

  function startColResize(
    event: ReactPointerEvent<HTMLSpanElement>,
    colIndex: number,
  ) {
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const startWidth = colWidths[colIndex] ?? 120;
    const onMove = (moveEvent: PointerEvent) => {
      const next = clamp(
        72,
        Math.round(startWidth + (moveEvent.clientX - startX)),
        560,
      );
      setColOverrides((prev) => ({ ...prev, [colIndex]: next }));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.cursor = "";
    };
    document.body.style.cursor = "col-resize";
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  const headerRowSet = useMemo(
    () => new Set(content.header_rows),
    [content.header_rows],
  );
  const labelColSet = useMemo(
    () => new Set(content.row_label_columns),
    [content.row_label_columns],
  );

  const allowStickyCols = viewportWidth >= 480;

  const { stickyLefts, stickyTotalLeft } = useMemo(() => {
    const map: Record<number, number> = {};
    let offset = 0;
    if (allowStickyCols) {
      for (let c = 0; c < content.columns; c++) {
        if (labelColSet.has(c)) {
          map[c] = offset;
          offset += colWidths[c];
        }
      }
    }
    return { stickyLefts: map, stickyTotalLeft: offset };
  }, [allowStickyCols, content.columns, labelColSet, colWidths]);

  const contiguousTopHeaders = useMemo(() => {
    const sorted = [...content.header_rows].sort((a, b) => a - b);
    const prefix: number[] = [];
    for (const r of sorted) {
      if (r === prefix.length) prefix.push(r);
      else break;
    }
    return new Set(prefix);
  }, [content.header_rows]);

  useLayoutEffect(() => {
    if (!contiguousTopHeaders.size) {
      setHeaderTops({});
      return;
    }
    const next: Record<number, number> = {};
    let top = 0;
    for (let r = 0; r < contiguousTopHeaders.size; r++) {
      next[r] = top;
      const el = rowRefs.current.get(r);
      top += el ? el.getBoundingClientRect().height : 40;
    }
    setHeaderTops((prev) => {
      const same =
        Object.keys(prev).length === Object.keys(next).length &&
        Object.entries(next).every(
          ([k, v]) => Math.abs((prev[Number(k)] ?? -1) - v) < 0.5,
        );
      return same ? prev : next;
    });
  }, [contiguousTopHeaders, content]);

  useEffect(() => {
    if (editingCellId && inlineInput.current) {
      inlineInput.current.focus();
      inlineInput.current.select();
    }
  }, [editingCellId]);

  useEffect(() => {
    const focused = cellAt(content, selection.focus);
    if (!focused || editingCellId) return;
    const el = scroller.current;
    if (!el) return;
    const target = el.querySelector<HTMLElement>(
      `[data-cell-id="${CSS.escape(focused.id)}"]`,
    );
    if (target) {
      target.scrollIntoView({ block: "nearest", inline: "nearest" });
      return;
    }
    // Virtualized jump: scroll roughly to the target row so it mounts cleanly.
    const estimatedTop = Math.max(
      0,
      (focused.row - contiguousTopHeaders.size - 2) * ESTIMATED_ROW_HEIGHT,
    );
    el.scrollTop = estimatedTop;
    setScrollTop(estimatedTop);
  }, [
    content,
    selection.focus.row,
    selection.focus.col,
    editingCellId,
    contiguousTopHeaders.size,
  ]);

  function navigate(event: KeyboardEvent, cell: Cell) {
    if (editingCellId === cell.id) return;
    if (event.key === "Enter" || event.key === "F2") {
      event.preventDefault();
      if (onStartEdit) onStartEdit(cell);
      else onActivate();
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
    if (!target) return;
    if (!onSelect(position, event.shiftKey)) return;
    if (scroller.current && content.rows > VIRTUAL_ROW_THRESHOLD) {
      const targetBodyIndex = Math.max(
        0,
        position.row - contiguousTopHeaders.size,
      );
      const topBound = targetBodyIndex * ESTIMATED_ROW_HEIGHT;
      const bottomBound = topBound + ESTIMATED_ROW_HEIGHT;
      const el = scroller.current;
      if (topBound < el.scrollTop) {
        el.scrollTop = topBound;
        setScrollTop(topBound);
      } else if (bottomBound > el.scrollTop + el.clientHeight - 44) {
        const nextTop = Math.max(0, bottomBound - el.clientHeight + 76);
        el.scrollTop = nextTop;
        setScrollTop(nextTop);
      }
    }
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const btn = scroller.current?.querySelector<HTMLButtonElement>(
          `[data-cell-id="${CSS.escape(target.id)}"]`,
        );
        btn?.focus();
        btn?.scrollIntoView({ block: "nearest", inline: "nearest" });
      });
    });
  }

  const gridRows = useMemo(() => {
    const startMap = new Map<string, Cell>();
    const covered = new Set<string>();
    for (const cell of content.cells) {
      startMap.set(`${cell.row}:${cell.col}`, cell);
      for (let r = cell.row; r < cell.row + cell.row_span; r++) {
        for (let c = cell.col; c < cell.col + cell.col_span; c++) {
          if (r !== cell.row || c !== cell.col) covered.add(`${r}:${c}`);
        }
      }
    }
    const rows: Array<{ rowIndex: number; cells: Cell[] }> = [];
    for (let r = 0; r < content.rows; r++) {
      const rowCells: Cell[] = [];
      for (let c = 0; c < content.columns; c++) {
        if (covered.has(`${r}:${c}`)) continue;
        const found = startMap.get(`${r}:${c}`);
        if (found) rowCells.push(found);
      }
      rows.push({ rowIndex: r, cells: rowCells });
    }
    return rows;
  }, [content]);

  const selected = new Set(
    selectedCells(content, selection).map((cell) => cell.id),
  );

  function renderRow(rowIndex: number, rowCells: Cell[]) {
    const isTopStickyRow = contiguousTopHeaders.has(rowIndex);
    return (
      <tr
        key={rowIndex}
        ref={(el) => {
          if (el) rowRefs.current.set(rowIndex, el);
          else rowRefs.current.delete(rowIndex);
        }}
        role="row"
        aria-rowindex={rowIndex + 1}
        className={headerRowSet.has(rowIndex) ? "grid-row-header" : undefined}
      >
        {rowCells.map((cell) => {
          const verdict = cellReviews[cell.id]?.verdict ?? "unreviewed";
          const isChecked = checked.has(cell.id) && verdict !== "unreadable";
          const focused =
            cell.row <= selection.focus.row &&
            cell.row + cell.row_span > selection.focus.row &&
            cell.col <= selection.focus.col &&
            cell.col + cell.col_span > selection.focus.col;
          const isHeaderCell =
            cell.role === "column_header" ||
            cell.role === "row_header" ||
            cell.role === "header" ||
            headerRowSet.has(cell.row);
          const isRowLabel =
            labelColSet.has(cell.col) && !headerRowSet.has(cell.row);
          const isStickyCol =
            allowStickyCols &&
            labelColSet.has(cell.col) &&
            !(
              cell.col_span > 1 && cell.col + cell.col_span === content.columns
            );
          const flagged = verdict === "unreadable" || issueCells.has(cell.id);
          const editing = editingCellId === cell.id;
          const subtotal = subtotalByTotalCell?.get(cell.id);

          const stickyStyle: CSSProperties = {};
          if (isTopStickyRow) {
            stickyStyle.position = "sticky";
            stickyStyle.top = headerTops[rowIndex] ?? rowIndex * 40;
          }
          if (isStickyCol && stickyLefts[cell.col] !== undefined) {
            stickyStyle.position = "sticky";
            stickyStyle.left = stickyLefts[cell.col];
          }

          const classNames = [
            "review-cell",
            isChecked && "cell-checked",
            flagged && "cell-flagged",
            verdict === "unreadable" && "cell-unreadable",
            highlightedCells.has(cell.id) && "cell-issue-focused",
            isHeaderCell && "cell-header",
            cell.role === "column_header" &&
              cell.col > 0 &&
              "cell-column-header",
            cell.role === "section" && "cell-section",
            isTopStickyRow && "cell-sticky-top",
            isStickyCol && "cell-sticky-col",
            selected.has(cell.id) && "cell-selected",
            editing && "cell-editing",
            tracedTotalCellId === cell.id && "cell-formula-total",
            tracedAddendCells?.has(cell.id) && "cell-formula-addend",
            isNumericInterpretation(cell.interpretation) ? "cell-numeric" : "",
          ]
            .filter(Boolean)
            .join(" ");

          const CellTag =
            headerRowSet.has(cell.row) || isRowLabel ? "th" : "td";
          const scope = headerRowSet.has(cell.row)
            ? "col"
            : isRowLabel
              ? "row"
              : undefined;

          return (
            <CellTag
              key={cell.id}
              role="gridcell"
              scope={scope}
              rowSpan={cell.row_span > 1 ? cell.row_span : undefined}
              colSpan={cell.col_span > 1 ? cell.col_span : undefined}
              aria-rowindex={cell.row + 1}
              aria-colindex={cell.col + 1}
              aria-rowspan={cell.row_span}
              aria-colspan={cell.col_span}
              aria-selected={selected.has(cell.id)}
              className={classNames}
              style={stickyStyle}
            >
              {editing ? (
                <input
                  ref={inlineInput}
                  type="text"
                  data-cell-id={cell.id}
                  data-inline-editor
                  aria-label={`Edit cell ${columnName(cell.col)}${cell.row + 1}`}
                  className="cell-inline-input"
                  value={inlineDraft}
                  spellCheck={false}
                  onChange={(event) =>
                    onInlineDraftChange?.(event.target.value)
                  }
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      if (
                        event.nativeEvent.isComposing ||
                        event.keyCode === 229
                      )
                        return;
                      onCommitInline?.("down");
                    } else if (event.key === "Tab") {
                      event.preventDefault();
                      onCommitInline?.(event.shiftKey ? "left" : "right");
                    } else if (event.key === "Escape") {
                      event.preventDefault();
                      event.stopPropagation();
                      onCancelInline?.();
                    }
                  }}
                  onBlur={() => onCommitInline?.("none")}
                />
              ) : (
                <button
                  type="button"
                  data-cell-id={cell.id}
                  data-row={cell.row}
                  data-col={cell.col}
                  tabIndex={focused ? 0 : -1}
                  aria-label={`Cell ${columnName(cell.col)}${cell.row + 1}: ${
                    cell.text || "blank"
                  }, ${verdict.replaceAll("_", " ")}`}
                  title={`${columnName(cell.col)}${cell.row + 1} · ${
                    cell.text || "blank"
                  }`}
                  className="cell-button"
                  onClick={(event) =>
                    onSelect({ row: cell.row, col: cell.col }, event.shiftKey)
                  }
                  onDoubleClick={() => {
                    if (onStartEdit) onStartEdit(cell);
                    else onActivate();
                  }}
                  onMouseEnter={() => {
                    if (subtotal && onHoverSubtotalCell)
                      onHoverSubtotalCell(cell.id);
                  }}
                  onMouseLeave={() => {
                    if (subtotal && onHoverSubtotalCell)
                      onHoverSubtotalCell(null);
                  }}
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
                  <span className="cell-indicators" aria-hidden="true">
                    {subtotal && (
                      <span
                        className={`cell-subtotal-badge subtotal-${subtotal.state}`}
                        title={subtotal.title}
                      >
                        {subtotal.state === "difference"
                          ? "Σ!"
                          : subtotal.state === "rounding_match"
                            ? "Σ≈"
                            : "Σ"}
                      </span>
                    )}
                    <span className="cell-state">
                      {verdict === "unreadable" ? (
                        <CircleSlash2 size={11} />
                      ) : flagged ? (
                        <CircleAlert size={11} />
                      ) : isChecked ? (
                        <Check size={11} strokeWidth={2.6} />
                      ) : null}
                    </span>
                  </span>
                </button>
              )}
              {isTopStickyRow && cell.col_span === 1 && (
                <span
                  className="col-resize-handle"
                  role="separator"
                  aria-orientation="vertical"
                  aria-label={`Resize column ${columnName(cell.col)}`}
                  title="Drag to resize column · double-click to auto-fit"
                  onPointerDown={(event) => startColResize(event, cell.col)}
                  onDoubleClick={(event) => {
                    event.stopPropagation();
                    setColOverrides((prev) => {
                      const next = { ...prev };
                      delete next[cell.col];
                      return next;
                    });
                  }}
                />
              )}
            </CellTag>
          );
        })}
      </tr>
    );
  }

  const headRows = gridRows.filter((r) => contiguousTopHeaders.has(r.rowIndex));
  const allBodyRows = gridRows.filter(
    (r) => !contiguousTopHeaders.has(r.rowIndex),
  );
  const bodyRows = visibleRowIndices
    ? allBodyRows.filter((r) => visibleRowIndices.has(r.rowIndex))
    : allBodyRows;

  const shouldVirtualize = bodyRows.length > VIRTUAL_ROW_THRESHOLD;
  let visibleBodySlice = bodyRows;
  let topSpacerHeight = 0;
  let bottomSpacerHeight = 0;
  let extraFocusedRow: { rowIndex: number; cells: Cell[] } | null = null;

  if (shouldVirtualize) {
    const startIdx = Math.max(
      0,
      Math.floor(scrollTop / ESTIMATED_ROW_HEIGHT) - VIRTUAL_OVERSCAN,
    );
    const visibleCount =
      Math.ceil(viewportHeight / ESTIMATED_ROW_HEIGHT) + VIRTUAL_OVERSCAN * 2;
    const endIdx = Math.min(bodyRows.length, startIdx + visibleCount);
    visibleBodySlice = bodyRows.slice(startIdx, endIdx);
    topSpacerHeight = startIdx * ESTIMATED_ROW_HEIGHT;
    bottomSpacerHeight =
      Math.max(0, bodyRows.length - endIdx) * ESTIMATED_ROW_HEIGHT;

    const focusIdx = bodyRows.findIndex(
      (row) => row.rowIndex === selection.focus.row,
    );
    if (focusIdx !== -1 && (focusIdx < startIdx || focusIdx >= endIdx)) {
      extraFocusedRow = bodyRows[focusIdx];
    }
  }

  const stickyTotalTop = useMemo(() => {
    if (!contiguousTopHeaders.size) return 0;
    const last = contiguousTopHeaders.size - 1;
    return (headerTops[last] ?? last * 40) + 42;
  }, [contiguousTopHeaders.size, headerTops]);

  return (
    <div
      className="review-grid-scroll"
      role="region"
      aria-label="Review table"
      tabIndex={0}
      ref={scroller}
      style={{
        scrollPaddingLeft: stickyTotalLeft || undefined,
        scrollPaddingTop: stickyTotalTop || undefined,
      }}
      onScroll={(event) => {
        if (shouldVirtualize) setScrollTop(event.currentTarget.scrollTop);
      }}
    >
      <table
        role="grid"
        aria-label="Review table"
        aria-rowcount={content.rows}
        aria-colcount={content.columns}
        className="review-grid review-table"
        style={{ minWidth: totalWidth }}
      >
        <colgroup>
          {colWidths.map((width, index) => (
            <col key={index} style={{ width }} />
          ))}
        </colgroup>
        {headRows.length > 0 && (
          <thead>
            {headRows.map((row) => renderRow(row.rowIndex, row.cells))}
          </thead>
        )}
        <tbody>
          {topSpacerHeight > 0 && (
            <tr className="grid-spacer" aria-hidden="true">
              <td
                colSpan={content.columns}
                style={{ height: topSpacerHeight }}
              />
            </tr>
          )}
          {extraFocusedRow &&
            extraFocusedRow.rowIndex <
              (visibleBodySlice[0]?.rowIndex ?? Infinity) &&
            renderRow(extraFocusedRow.rowIndex, extraFocusedRow.cells)}
          {visibleBodySlice.map((row) => renderRow(row.rowIndex, row.cells))}
          {extraFocusedRow &&
            extraFocusedRow.rowIndex >
              (visibleBodySlice[visibleBodySlice.length - 1]?.rowIndex ?? -1) &&
            renderRow(extraFocusedRow.rowIndex, extraFocusedRow.cells)}
          {bottomSpacerHeight > 0 && (
            <tr className="grid-spacer" aria-hidden="true">
              <td
                colSpan={content.columns}
                style={{ height: bottomSpacerHeight }}
              />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
