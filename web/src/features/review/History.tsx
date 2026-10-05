import { useEffect, useState } from "react";
import { apiRequest, errorMessage } from "../../api/client";
import type { components } from "../../api/types";
import type { Revision, Table } from "../../api/models";
export function History({
  table,
  revision,
  busy,
  onRestore,
}: {
  table: Table;
  revision: Revision;
  busy: boolean;
  onRestore: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<
    components["schemas"]["EditEventView"][]
  >([]);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Revision | null>(null);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    apiRequest<components["schemas"]["EditHistory"]>(
      `/api/v1/tables/${table.id}/history`,
      { signal: controller.signal },
    )
      .then((value) => setEvents(value.items))
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => controller.abort();
  }, [open, table.id, revision.id]);
  async function inspect(id: string) {
    try {
      setPreview(
        await apiRequest<Revision>(
          `/api/v1/tables/${table.id}/revisions/${id}`,
        ),
      );
    } catch (error) {
      setError(errorMessage(error));
    }
  }
  return (
    <section className="history">
      <button aria-expanded={open} onClick={() => setOpen(!open)}>
        Revision history
      </button>
      {open && (
        <>
          <p className="fine-print">
            Restoring creates a new unchecked draft. Previous revisions stay
            intact.
          </p>
          <button
            disabled={busy}
            onClick={() => onRestore(table.original_revision_id)}
          >
            Restore original as new draft
          </button>
          {error && <p role="alert">{error}</p>}
          <ol>
            {events.map((event, index) => (
              <li key={event.id}>
                <span>
                  {String(event.operation.kind).replaceAll("_", " ")} ·{" "}
                  {new Date(event.created_at).toLocaleString()}
                </span>
                <button onClick={() => void inspect(event.result_revision_id)}>
                  Inspect revision {events.length - index}
                </button>
                <button
                  disabled={busy}
                  onClick={() => onRestore(event.result_revision_id)}
                >
                  Restore revision {events.length - index} as new draft
                </button>
              </li>
            ))}
          </ol>
          {preview && (
            <div className="history-preview">
              <p>Historical revision · {preview.checksum}</p>
              <pre>
                {preview.content.cells
                  .map(
                    (cell) =>
                      `[${cell.row + 1}, ${cell.col + 1}] ${cell.text || "(blank)"}`,
                  )
                  .join("\n")}
              </pre>
            </div>
          )}
        </>
      )}
    </section>
  );
}
