import { useEffect, useState } from "react";
import { apiRequest, errorMessage } from "../../api/client";
import type { components } from "../../api/types";
import type { Revision, Table, ReviewState } from "../../api/models";
import { ReviewTimeline } from "./ReviewTimeline";

export function History(props: {
  table: Table;
  revision: Revision;
  busy: boolean;
  onRestore: (id: string) => void;
  selectedCellId?: string;
  review: ReviewState | null;
  active?: boolean;
}) {
  return (
    <section className="history">
      <ReviewTimeline
        revisionId={props.revision.id}
        selectedCellId={props.selectedCellId}
        refreshKey={`${props.review?.generation ?? "loading"}/${props.review?.current_approval?.id ?? "draft"}`}
        active={!!props.active}
      />
      <details className="legacy-history">
        <summary>Earlier history and revision restore</summary>
        <LegacyHistory {...props} />
      </details>
    </section>
  );
}

function LegacyHistory({
  table,
  revision,
  busy,
  onRestore,
  selectedCellId,
  review,
}: {
  table: Table;
  revision: Revision;
  busy: boolean;
  onRestore: (id: string) => void;
  selectedCellId?: string;
  review: ReviewState | null;
}) {
  const [scope, setScope] = useState("cell");
  const [checkedEvents, setCheckedEvents] = useState<
    components["schemas"]["CellReviewEvent"][]
  >([]);
  const [more, setMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSize] = useState(0);
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
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    apiRequest<components["schemas"]["CellReviewHistory"]>(
      `/api/v1/revisions/${revision.id}/checked-history?offset=${offset}`,
      { signal: controller.signal },
    )
      .then((result) => {
        setPageSize(result.items.length);
        setCheckedEvents((value) =>
          offset === 0
            ? result.items
            : [
                ...new Map(
                  [...value, ...result.items].map((event) => [event.id, event]),
                ).values(),
              ],
        );
        setMore(result.has_more);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => controller.abort();
  }, [open, revision.id, offset, review]);
  useEffect(() => {
    setOffset(0);
    setCheckedEvents([]);
  }, [revision.id, review]);
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
          <label>
            History scope
            <select
              value={scope}
              onChange={(event) => setScope(event.target.value)}
            >
              <option value="cell">Selected cell and table-wide edits</option>
              <option value="table">Whole table</option>
            </select>
          </label>
          <p className="fine-print">
            Review events below belong to this revision. Edit history includes
            earlier revisions.
          </p>
          {checkedEvents
            .filter(
              (event) =>
                scope === "table" ||
                event.cell_ids.includes(selectedCellId ?? ""),
            )
            .map((event) => (
              <p key={event.id}>
                {Object.values(event.cell_reviews ?? {}).length
                  ? [
                      ...new Set(
                        Object.values(event.cell_reviews ?? {}).map((record) =>
                          record.verdict.replaceAll("_", " "),
                        ),
                      ),
                    ].join(", ")
                  : event.checked
                    ? "Marked checked"
                    : "Marked unchecked"}{" "}
                by reviewer {event.actor_id.slice(0, 8)} ·{" "}
                {new Date(event.created_at).toLocaleString()}
                {Object.values(event.cell_reviews ?? {}).some(
                  (record) => record.reason,
                ) && (
                  <span>
                    {" "}
                    ·{" "}
                    {Object.values(event.cell_reviews ?? {})
                      .map((record) => record.reason)
                      .filter(Boolean)
                      .join("; ")}
                  </span>
                )}
              </p>
            ))}
          {more && (
            <button onClick={() => setOffset((value) => value + pageSize)}>
              Load more review events
            </button>
          )}
          <button
            disabled={busy}
            onClick={() => onRestore(table.original_revision_id)}
          >
            Restore original as new draft
          </button>
          {error && <p role="alert">{error}</p>}
          <ol>
            {events
              .filter(
                (event) =>
                  scope === "table" ||
                  event.structural_change ||
                  event.affected_cell_ids.length === 0 ||
                  event.affected_cell_ids.includes(selectedCellId ?? ""),
              )
              .map((event) => (
                <li key={event.id}>
                  <span>
                    {String(event.operation.kind).replaceAll("_", " ")} ·{" "}
                    {new Date(event.created_at).toLocaleString()}
                  </span>
                  <button
                    onClick={() => void inspect(event.result_revision_id)}
                  >
                    Inspect revision {events.length - events.indexOf(event)}
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => onRestore(event.result_revision_id)}
                  >
                    Restore revision {events.length - events.indexOf(event)} as
                    new draft
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
