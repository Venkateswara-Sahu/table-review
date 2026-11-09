import { expect, test } from "@playwright/test";
import type { Cell, TableContent } from "../src/api/models";
import { firstReviewableCell } from "../src/features/table/selection";

function reference(precision: "cell" | "table" | "page") {
  return {
    page: 1,
    precision,
    ...(precision === "page"
      ? {}
      : {
          box: {
            page: 1,
            left: 10,
            top: 10,
            right: 20,
            bottom: 20,
            page_width: 100,
            page_height: 100,
          },
        }),
  };
}

function cell(
  id: string,
  row: number,
  col: number,
  text: string,
  precision: "cell" | "table" | "page",
): Cell {
  return {
    id,
    row,
    col,
    row_span: 1,
    col_span: 1,
    raw_text: text,
    text,
    role: "data",
    interpretation: "uncertain",
    reference: reference(precision),
  } as unknown as Cell;
}

function content(cells: Cell[]): TableContent {
  return {
    rows: Math.max(...cells.map((item) => item.row + 1), 1),
    columns: Math.max(...cells.map((item) => item.col + 1), 1),
    header_rows: [],
    row_label_columns: [],
    unit_notes: [],
    cells,
  } as unknown as TableContent;
}

test("opening a table skips a blank corner stub without exact geometry", () => {
  const blankCorner = cell("corner", 0, 0, "", "table");
  const dateHeader = cell("date", 0, 1, "Dec 28, 2024", "cell");
  expect(firstReviewableCell(content([blankCorner, dateHeader]))?.id).toBe(
    "date",
  );
});

test("opening a blank manual grid falls back to the first cell", () => {
  const first = cell("a1", 0, 0, "", "page");
  const second = cell("b1", 0, 1, "", "page");
  expect(firstReviewableCell(content([second, first]))?.id).toBe("a1");
});

test("recognized text wins over a blank cell with exact geometry", () => {
  const recognized = cell("label", 0, 0, "Revenue", "table");
  const blank = cell("blank", 0, 1, "", "cell");
  expect(firstReviewableCell(content([blank, recognized]))?.id).toBe("label");
});
