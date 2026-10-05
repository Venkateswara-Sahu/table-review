import { useEffect, useRef, useState } from "react";
import { apiBlob, apiRequest, errorMessage } from "../../api/client";
import type { components } from "../../api/types";

type Export = components["schemas"]["ExportRecord"];
export function ExportControls({
  tableId,
  revisionId,
  disabled,
}: {
  tableId: string;
  revisionId: string;
  disabled: boolean;
}) {
  const [records, setRecords] = useState<Export[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const pending = useRef<{ identity: string; key: string } | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const active = new AbortController();
    controller.current = active;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await apiRequest<components["schemas"]["ExportList"]>(
          `/api/v1/tables/${tableId}/exports`,
          { signal: active.signal },
        );
        if (active.signal.aborted) return;
        setRecords(result.items);
        if (
          result.items.some(
            (r) => r.state === "queued" || r.state === "running",
          )
        )
          timer = setTimeout(() => void poll(), 1000);
      } catch (error) {
        if (!active.signal.aborted) setError(errorMessage(error));
      }
    }
    void poll();
    return () => {
      active.abort();
      clearTimeout(timer);
    };
  }, [tableId, refresh]);
  async function create(format: Export["format"]) {
    setBusy(true);
    setError("");
    const identity = `${tableId}/${revisionId}/${format}`;
    if (pending.current?.identity !== identity)
      pending.current = { identity, key: crypto.randomUUID() };
    try {
      await apiRequest<Export>(`/api/v1/tables/${tableId}/exports`, {
        method: "POST",
        signal: controller.current?.signal,
        headers: { "Idempotency-Key": pending.current.key },
        body: JSON.stringify({ revision_id: revisionId, format }),
      });
      pending.current = null;
      setRefresh((value) => value + 1);
    } catch (error) {
      if (!controller.current?.signal.aborted) setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function download(record: Export) {
    setBusy(true);
    setError("");
    try {
      const blob = await apiBlob(
        `/api/v1/exports/${record.id}/download`,
        controller.current?.signal,
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      const status = record.manifest?.review_status;
      const label =
        status === "reviewed" || status === "reviewed_with_warnings"
          ? status
          : "draft";
      anchor.download = `table-${label}-${record.revision_id}.${record.format === "workbook" ? "xlsx" : "zip"}`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      if (!controller.current?.signal.aborted) setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="Exports">
      <h3>Exports</h3>
      <p>
        Each export keeps the saved revision and review status from the moment
        you request it.
      </p>
      <div className="edit-toolbar">
        <button
          disabled={disabled || busy}
          onClick={() => void create("workbook")}
        >
          Create workbook
        </button>
        <button
          disabled={disabled || busy}
          onClick={() => void create("csv_package")}
        >
          Create CSV package
        </button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <ul>
        {records.map((record) => (
          <li key={record.id}>
            {record.format === "workbook" ? "Workbook" : "CSV package"} ·{" "}
            {record.manifest?.review_status === "reviewed"
              ? "Reviewed"
              : record.manifest?.review_status === "reviewed_with_warnings"
                ? "Reviewed with warnings"
                : "Draft"}{" "}
            · {record.state}
            <span className="fine-print">
              {" "}
              Revision {record.revision_id.slice(0, 8)}
            </span>
            {record.state === "succeeded" && (
              <button disabled={busy} onClick={() => void download(record)}>
                Download{" "}
                {record.format === "workbook" ? "workbook" : "CSV package"}
              </button>
            )}
            {record.state === "failed" && (
              <span>
                {" "}
                {record.failure_code === "workbook_text_unsupported"
                  ? "Create a CSV package to preserve text outside workbook limits."
                  : "Export failed. Please try again."}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
