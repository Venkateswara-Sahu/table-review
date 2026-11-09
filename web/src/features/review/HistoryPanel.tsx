import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { History as HistoryIcon, RotateCcw } from "lucide-react";
import { apiRequest } from "../../api/client";
import type {
  EditEvent,
  EditHistory,
  Revision,
  ReviewTimeline,
  TimelineEvent,
} from "../../api/models";
import { Button, Select } from "../../ui/primitives";

const eventNames: Record<TimelineEvent["kind"], string> = {
  coverage_start: "Recording boundary",
  checks_changed: "Checks refreshed",
  cell_review: "Cell review changed",
  issue_decision: "Issue decision",
  approval: "Table approved",
  edit: "Revision saved",
  table_visibility: "Table removed or restored",
};

export function HistoryPanel({
  tableId,
  revisionId,
  originalRevisionId,
  cellId,
  busy,
  onRestore,
  onPreview,
}: {
  tableId: string;
  revisionId: string;
  originalRevisionId: string;
  cellId?: string;
  busy: boolean;
  onRestore: (id: string) => void;
  onPreview: (revision: Revision) => void;
}) {
  const [scope, setScope] = useState<"cell" | "table">("cell");
  const [cursor, setCursor] = useState<{ after: number; through?: number }>({
    after: 0,
  });
  const timeline = useQuery({
    queryKey: [
      "timeline",
      revisionId,
      scope,
      cellId ?? "none",
      cursor.after,
      cursor.through ?? "end",
    ],
    queryFn: () => {
      const params = new URLSearchParams({ after: String(cursor.after) });
      if (cursor.through !== undefined)
        params.set("through", String(cursor.through));
      if (scope === "cell" && cellId) params.set("cell_id", cellId);
      return apiRequest<ReviewTimeline>(
        `/api/v1/revisions/${revisionId}/timeline?${params}`,
      );
    },
  });
  const edits = useQuery({
    queryKey: ["history", tableId],
    queryFn: () => apiRequest<EditHistory>(`/api/v1/tables/${tableId}/history`),
    // History changes whenever an edit succeeds. Do not serve a cached list
    // to a reviewer who reopens the panel after saving.
    staleTime: 0,
  });
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState("");

  async function inspect(id: string) {
    setPreviewing(id);
    setPreviewError("");
    try {
      onPreview(
        await apiRequest<Revision>(`/api/v1/tables/${tableId}/revisions/${id}`),
      );
    } catch (error) {
      setPreviewError(
        error instanceof Error ? error.message : "Revision could not be loaded",
      );
    } finally {
      setPreviewing(null);
    }
  }

  const events = timeline.data?.items ?? [];
  const history = edits.data?.items ?? [];

  return (
    <div className="history-panel">
      <div className="more-intro">
        <HistoryIcon size={16} aria-hidden="true" />
        <p className="small">
          Restoring makes a new unchecked draft. Previous revisions, decisions
          and exports stay intact.
        </p>
      </div>

      <div className="history-toolbar">
        <label className="field">
          <span className="field-label">Timeline scope</span>
          <Select
            value={scope}
            onChange={(event) => {
              setScope(event.target.value as "cell" | "table");
              setCursor({ after: 0 });
            }}
          >
            <option value="cell">This cell and table-wide actions</option>
            <option value="table">Whole table</option>
          </Select>
        </label>
        <span className="tiny muted">
          Oldest first. Event numbers show saved order.
        </span>
      </div>

      {timeline.isError && (
        <p className="error small" role="alert">
          {timeline.error.message}{" "}
          <button
            type="button"
            className="link-button"
            onClick={() => void timeline.refetch()}
          >
            Retry
          </button>
        </p>
      )}
      {timeline.isPending && (
        <p className="small muted" role="status">
          Loading timeline…
        </p>
      )}
      {timeline.data && !events.length && (
        <p className="small muted">
          No recorded events for this selection. Try the whole-table scope.
        </p>
      )}
      <ol className="timeline-list">
        {events.map((event) => (
          <li className="timeline-entry" key={event.id}>
            <span className="timeline-dot" aria-hidden="true" />
            <div>
              <div className="timeline-head">
                <strong>{eventNames[event.kind]}</strong>
                <span className="tiny mono">
                  #{event.sequence} · {event.actor_id?.slice(0, 8) ?? "system"}{" "}
                  · {new Date(event.recorded_at).toLocaleString()}
                </span>
              </div>
              <p className="small">
                {String(event.details.reason ?? "Recorded action")}
              </p>
              <details>
                <summary className="tiny muted">Recorded details</summary>
                <pre>{JSON.stringify(event.details, null, 2)}</pre>
              </details>
            </div>
          </li>
        ))}
      </ol>
      {timeline.data?.has_more && (
        <Button
          size="sm"
          disabled={timeline.isFetching}
          onClick={() =>
            setCursor({
              after: timeline.data!.next_after_sequence,
              through: timeline.data!.through_sequence,
            })
          }
        >
          Load more events
        </Button>
      )}

      <section className="history-revisions">
        <div className="row spread">
          <h4>Earlier revisions</h4>
          <Button
            size="sm"
            variant="secondary"
            icon={<RotateCcw size={13} />}
            disabled={busy}
            onClick={() => onRestore(originalRevisionId)}
          >
            Restore original as draft
          </Button>
        </div>
        {edits.isError && (
          <p className="error small" role="alert">
            {edits.error.message}
          </p>
        )}
        {!history.length && !edits.isPending && (
          <p className="small muted">
            No earlier edits. This is the original extraction.
          </p>
        )}
        <ol className="revision-list">
          {history.map((event: EditEvent, index) => (
            <li key={event.id}>
              <div>
                <p className="small">
                  {String(event.operation.kind).replaceAll("_", " ")}
                  {event.structural_change ? " · structure" : ""}
                </p>
                <p className="tiny muted">
                  {new Date(event.created_at).toLocaleString()} ·{" "}
                  {event.affected_cell_ids.length} affected cells
                </p>
              </div>
              <div className="row">
                <Button
                  size="sm"
                  variant="ghost"
                  loading={previewing === event.result_revision_id}
                  onClick={() => void inspect(event.result_revision_id)}
                >
                  Inspect
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => onRestore(event.result_revision_id)}
                >
                  Restore as draft
                </Button>
              </div>
            </li>
          ))}
        </ol>
        {previewError && (
          <p className="error small" role="alert">
            {previewError}
          </p>
        )}
      </section>
    </div>
  );
}
