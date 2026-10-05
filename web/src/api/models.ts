import type { components } from "./types";

export type Document = components["schemas"]["DocumentView"];
export type PageInfo = components["schemas"]["PageView"];
export type Table = components["schemas"]["TableRecord"];
export type TableView = components["schemas"]["TableView"];
export type Revision = components["schemas"]["RevisionView"];
export type Cell = components["schemas"]["CellContent"];
export type TableContent = components["schemas"]["TableContent"];
export type SourceReference = components["schemas"]["SourceReference"];
export type PageBox = components["schemas"]["PageBox"];
export type Task = components["schemas"]["TaskView"];
export type Run = components["schemas"]["RunView"];
export type ReviewState = components["schemas"]["ReviewState"];
export type Snapshot = components["schemas"]["ReviewSnapshot"];
export type Actor = components["schemas"]["Actor"];
export type Session = components["schemas"]["SessionView"];
export type EditOperation = components["schemas"]["EditInput"]["operation"];
