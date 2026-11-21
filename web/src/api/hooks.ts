import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest, idempotencyKey } from "./client";
import type {
  CellContext,
  Document,
  DocumentOutline,
  DocumentSearchResult,
  EditHistory,
  ExportRecord,
  ExtractionBackend,
  ExtractionMode,
  List,
  PageBox,
  PageInfo,
  ReportExportRecord,
  ReviewState,
  ReviewTimeline,
  Run,
  RuntimeCapabilities,
  SubtotalResults,
  Table,
  TableCandidatesResult,
  TableView,
  Task,
} from "./models";

const keys = {
  capabilities: ["capabilities"] as const,
  documents: ["documents"] as const,
  document: (id: string) => ["document", id] as const,
  page: (id: string, page: number) => ["page", id, page] as const,
  tables: (id: string, page: number, removed: boolean) =>
    ["tables", id, page, removed] as const,
  allTables: (id: string) => ["tables", id, "all"] as const,
  outline: (id: string) => ["outline", id] as const,
  search: (id: string, query: string) => ["search", id, query] as const,
  tableCandidates: (id: string) => ["table-candidates", id] as const,
  reportExports: (id: string) => ["report-exports", id] as const,
  tasks: (id: string) => ["tasks", id] as const,
  runs: (id: string) => ["runs", id] as const,
  table: (id: string) => ["table", id] as const,
  review: (revisionId: string) => ["review", revisionId] as const,
  exports: (tableId: string) => ["exports", tableId] as const,
  subtotals: (tableId: string, revisionId: string) =>
    ["subtotals", tableId, revisionId] as const,
  context: (tableId: string, revisionId: string, cellId: string) =>
    ["context", tableId, revisionId, cellId] as const,
  history: (tableId: string) => ["history", tableId] as const,
  timeline: (revisionId: string, cellId?: string) =>
    ["timeline", revisionId, cellId ?? "table"] as const,
};

export function useCapabilities(enabled = true) {
  return useQuery({
    queryKey: keys.capabilities,
    queryFn: () => apiRequest<RuntimeCapabilities>("/api/v1/capabilities"),
    enabled,
    staleTime: 60_000,
    retry: false,
  });
}

export function useDocuments() {
  return useQuery({
    queryKey: keys.documents,
    queryFn: () => apiRequest<List<Document>>("/api/v1/documents"),
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (item) =>
          item.preparation_state !== "ready" &&
          item.preparation_state !== "failed",
      )
        ? 2500
        : false,
  });
}

export function useDocument(documentId?: string) {
  return useQuery({
    queryKey: keys.document(documentId ?? ""),
    queryFn: () =>
      apiRequest<Document>(
        `/api/v1/documents/${encodeURIComponent(documentId!)}`,
      ),
    enabled: !!documentId,
    staleTime: 60_000,
  });
}

export function usePage(documentId?: string, pageNo?: number) {
  return useQuery({
    queryKey: keys.page(documentId ?? "", pageNo ?? 0),
    queryFn: () =>
      apiRequest<PageInfo>(
        `/api/v1/documents/${encodeURIComponent(documentId!)}/pages/${pageNo}`,
      ),
    enabled: !!documentId && !!pageNo && pageNo > 0,
    staleTime: 30_000,
    placeholderData: (previousData, previousQuery) => {
      const prevKey = previousQuery?.queryKey;
      if (
        Array.isArray(prevKey) &&
        prevKey[0] === "page" &&
        prevKey[1] === (documentId ?? "")
      ) {
        return previousData;
      }
      return undefined;
    },
  });
}

export function useTables(
  documentId?: string,
  pageNo?: number,
  removed = false,
  active = false,
) {
  return useQuery({
    queryKey: keys.tables(documentId ?? "", pageNo ?? 0, removed),
    queryFn: () =>
      apiRequest<List<Table>>(
        `/api/v1/documents/${encodeURIComponent(documentId!)}/tables?page_no=${pageNo}${
          removed ? "&removed=true" : ""
        }`,
      ),
    enabled: !!documentId && !!pageNo && pageNo > 0,
    refetchInterval: active ? 1500 : false,
  });
}

export function useAllTables(documentId?: string, enabled = true) {
  return useQuery({
    queryKey: keys.allTables(documentId ?? ""),
    queryFn: () =>
      apiRequest<List<Table>>(
        `/api/v1/documents/${encodeURIComponent(documentId!)}/tables`,
      ),
    enabled: !!documentId && enabled,
    staleTime: 10_000,
  });
}

export function useDocumentOutline(documentId?: string, enabled = false) {
  return useQuery({
    queryKey: keys.outline(documentId ?? ""),
    queryFn: () =>
      apiRequest<DocumentOutline>(
        `/api/v1/documents/${encodeURIComponent(documentId!)}/outline`,
      ),
    enabled: !!documentId && enabled,
    staleTime: 300_000,
  });
}

export function useDocumentSearch(
  documentId?: string,
  query = "",
  enabled = false,
) {
  const trimmed = query.trim();
  return useQuery({
    queryKey: keys.search(documentId ?? "", trimmed),
    queryFn: () =>
      apiRequest<DocumentSearchResult>(
        `/api/v1/documents/${encodeURIComponent(documentId!)}/search?q=${encodeURIComponent(trimmed)}&limit=80`,
      ),
    enabled: !!documentId && enabled && trimmed.length > 0,
    staleTime: 60_000,
  });
}

export function useTableCandidates(documentId?: string, enabled = false) {
  return useQuery({
    queryKey: keys.tableCandidates(documentId ?? ""),
    queryFn: () =>
      apiRequest<TableCandidatesResult>(
        `/api/v1/documents/${encodeURIComponent(documentId!)}/table-candidates`,
      ),
    enabled: !!documentId && enabled,
    staleTime: 300_000,
  });
}

export function useReportExports(documentId?: string, enabled = false) {
  return useQuery({
    queryKey: keys.reportExports(documentId ?? ""),
    queryFn: () =>
      apiRequest<List<ReportExportRecord>>(
        `/api/v1/documents/${encodeURIComponent(documentId!)}/exports`,
      ),
    enabled: !!documentId && enabled,
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (item) => item.state === "queued" || item.state === "running",
      )
        ? 1000
        : false,
  });
}

export function useTasks(documentId?: string) {
  return useQuery({
    queryKey: keys.tasks(documentId ?? ""),
    queryFn: () =>
      apiRequest<List<Task>>(
        `/api/v1/documents/${encodeURIComponent(documentId!)}/tasks`,
      ),
    enabled: !!documentId,
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (task) => task.state === "queued" || task.state === "running",
      )
        ? 1500
        : false,
  });
}

export function useRuns(documentId?: string, enabled = false) {
  return useQuery({
    queryKey: keys.runs(documentId ?? ""),
    queryFn: () =>
      apiRequest<List<Run>>(
        `/api/v1/documents/${encodeURIComponent(documentId!)}/runs?limit=20`,
      ),
    enabled: !!documentId && enabled,
    staleTime: 5000,
  });
}

export function useTableView(tableId?: string) {
  return useQuery({
    queryKey: keys.table(tableId ?? ""),
    queryFn: () =>
      apiRequest<TableView>(`/api/v1/tables/${encodeURIComponent(tableId!)}`),
    enabled: !!tableId,
    staleTime: 30_000,
  });
}

export function useReview(revisionId?: string) {
  return useQuery({
    queryKey: keys.review(revisionId ?? ""),
    queryFn: () =>
      apiRequest<ReviewState>(
        `/api/v1/revisions/${encodeURIComponent(revisionId!)}/review`,
      ),
    enabled: !!revisionId,
    staleTime: 0,
  });
}

export function useExports(tableId?: string, enabled = true) {
  return useQuery({
    queryKey: keys.exports(tableId ?? ""),
    queryFn: () =>
      apiRequest<List<ExportRecord>>(
        `/api/v1/tables/${encodeURIComponent(tableId!)}/exports`,
      ),
    enabled: !!tableId && enabled,
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (item) => item.state === "queued" || item.state === "running",
      )
        ? 1000
        : false,
  });
}

export function useSubtotals(
  tableId?: string,
  revisionId?: string,
  enabled = false,
) {
  return useQuery({
    queryKey: keys.subtotals(tableId ?? "", revisionId ?? ""),
    queryFn: () =>
      apiRequest<SubtotalResults>(
        `/api/v1/tables/${encodeURIComponent(tableId!)}/revisions/${encodeURIComponent(revisionId!)}/subtotals`,
      ),
    enabled: !!tableId && !!revisionId && enabled,
  });
}

export function useContext(
  tableId?: string,
  revisionId?: string,
  cellId?: string,
  enabled = true,
) {
  return useQuery({
    queryKey: keys.context(tableId ?? "", revisionId ?? "", cellId ?? ""),
    queryFn: () =>
      apiRequest<CellContext>(
        `/api/v1/tables/${encodeURIComponent(tableId!)}/revisions/${encodeURIComponent(revisionId!)}/context/${encodeURIComponent(cellId!)}`,
      ),
    enabled: !!tableId && !!revisionId && !!cellId && enabled,
    retry: 1,
  });
}

export function useEditHistory(tableId?: string, enabled = false) {
  return useQuery({
    queryKey: keys.history(tableId ?? ""),
    queryFn: () =>
      apiRequest<EditHistory>(
        `/api/v1/tables/${encodeURIComponent(tableId!)}/history`,
      ),
    enabled: !!tableId && enabled,
  });
}

export function useTimeline(
  revisionId?: string,
  enabled = false,
  cellId?: string,
) {
  return useQuery({
    queryKey: keys.timeline(revisionId ?? "", cellId),
    queryFn: () => {
      const params = new URLSearchParams();
      if (cellId) params.set("cell_id", cellId);
      return apiRequest<ReviewTimeline>(
        `/api/v1/revisions/${encodeURIComponent(revisionId!)}/timeline${
          params.size ? `?${params}` : ""
        }`,
      );
    },
    enabled: !!revisionId && enabled,
  });
}

export function useUploadDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      return apiRequest<Document>("/api/v1/documents", {
        method: "POST",
        body,
        headers: { "Idempotency-Key": idempotencyKey() },
      });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: keys.documents }),
  });
}

export function useDeleteDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      apiRequest<void>(`/api/v1/documents/${documentId}`, {
        method: "DELETE",
      }),
    onSuccess: (_data, documentId) => {
      client.removeQueries({ queryKey: keys.document(documentId) });
      client.invalidateQueries({ queryKey: keys.documents });
    },
  });
}

export function useOpenDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      apiRequest<Document>(`/api/v1/documents/${documentId}/open`, {
        method: "POST",
      }),
    onSuccess: (document) => {
      client.setQueryData(keys.document(document.id), document);
      client.invalidateQueries({ queryKey: keys.documents });
    },
  });
}

export function useExtract(documentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      pages: number[];
      region?: PageBox | null;
      backend?: ExtractionBackend;
      mode?: ExtractionMode;
    }) =>
      apiRequest<Task>(`/api/v1/documents/${documentId}/runs`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify({
          pages: input.pages,
          region: input.region ?? null,
          backend: input.backend ?? "auto",
          mode: input.mode ?? "accurate",
        }),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.tasks(documentId) });
      client.invalidateQueries({ queryKey: keys.runs(documentId) });
    },
  });
}

export function useStitchTables(documentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      table_ids: string[];
      title?: string | null;
      drop_repeated_headers?: boolean;
    }) =>
      apiRequest<Table>(`/api/v1/documents/${documentId}/tables/stitch`, {
        method: "POST",
        body: JSON.stringify({
          table_ids: input.table_ids,
          title: input.title || null,
          drop_repeated_headers: input.drop_repeated_headers ?? true,
        }),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["tables", documentId] });
    },
  });
}

export function useCancelTask(documentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) =>
      apiRequest<Task>(`/api/v1/tasks/${taskId}/cancel`, {
        method: "POST",
      }),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: keys.tasks(documentId) }),
  });
}

export function useRetryTask(documentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) =>
      apiRequest<Task>(`/api/v1/tasks/${taskId}/retry`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey() },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.tasks(documentId) });
      client.invalidateQueries({ queryKey: keys.runs(documentId) });
    },
  });
}

export function useCreateManualTable(documentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { region: PageBox; rows: number; columns: number }) =>
      apiRequest<Table>(`/api/v1/documents/${documentId}/tables`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["tables", documentId] });
    },
  });
}

export function tableSourcePage(view: TableView) {
  return (
    view.revision.content.source_reference?.page ??
    view.revision.content.cells[0]?.reference.page ??
    1
  );
}

export { keys as queryKeys };
