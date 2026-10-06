import { useEffect, useState } from "react";
import { apiRequest, errorMessage } from "../../api/client";
import type { Document } from "../../api/models";

export function ReportList({
  onOpen,
}: {
  onOpen: (document: Document) => void;
}) {
  const [reports, setReports] = useState<Document[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  async function deleteReport(report: Document) {
    if (
      !window.confirm(
        "Delete this report? Saved tables and exports will become inaccessible immediately. Active files are removed within 24 hours under normal operation; existing backups expire within 7 days.",
      )
    )
      return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await apiRequest(`/api/v1/documents/${report.id}`, { method: "DELETE" });
      setReports((items) => items.filter((item) => item.id !== report.id));
      setNotice(
        "Report deleted. Its tables and exports are no longer accessible.",
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function refresh(signal?: AbortSignal) {
    try {
      const data = await apiRequest<{ items: Document[] }>(
        "/api/v1/documents",
        { signal },
      );
      setReports(data.items);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError"))
        setError(errorMessage(error));
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, []);
  async function upload(file?: File) {
    if (!file) return;
    setBusy(true);
    setError("");
    const form = new FormData();
    form.append("file", file);
    try {
      await apiRequest<Document>("/api/v1/documents", {
        method: "POST",
        body: form,
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      await refresh();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="reports">
      <div className="section-heading">
        <div>
          <p className="eyebrow">SAVED WORK</p>
          <h1>Your reports</h1>
          <p className="muted">
            Choose a report to select pages, review tables and keep your
            changes.
          </p>
        </div>
        <button onClick={() => void refresh()}>Refresh reports</button>
      </div>
      <section className="upload-panel">
        <div>
          <h2>Start with a report</h2>
          <p>English digital PDF · up to 50 MiB · 500 pages</p>
        </div>
        <label className="upload-button">
          {busy ? "Uploading…" : "Upload PDF"}
          <input
            aria-label="Upload PDF"
            type="file"
            accept=".pdf,application/pdf"
            disabled={busy}
            onChange={(event) => {
              void upload(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </label>
      </section>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {reports.length === 0 && (
        <div className="empty">
          <h2>No reports yet</h2>
          <p>Upload a PDF to begin. You choose the pages to process.</p>
        </div>
      )}
      <div className="report-list">
        {reports.map((report) => (
          <article key={report.id} className="report-row">
            <span className="file-icon" aria-hidden="true">
              PDF
            </span>
            <div>
              <h2>{report.filename}</h2>
              <p className="muted">
                {report.page_count} pages ·{" "}
                {(report.byte_count / 1024 / 1024).toFixed(1)} MiB ·{" "}
                {report.preparation_state}
              </p>
              <p className="fine-print">
                Expires{" "}
                {new Date(
                  new Date(report.last_foreground_at).getTime() + 30 * 86400000,
                ).toLocaleDateString()}
              </p>
            </div>
            <button
              aria-label={`Open ${report.filename}`}
              disabled={busy}
              onClick={() => onOpen(report)}
            >
              Open report <span aria-hidden="true">↗</span>
            </button>
            <button
              aria-label={`Delete ${report.filename}`}
              disabled={busy}
              onClick={() => void deleteReport(report)}
            >
              Delete
            </button>
          </article>
        ))}
      </div>
    </main>
  );
}
