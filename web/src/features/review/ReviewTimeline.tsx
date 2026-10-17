import { useEffect, useState } from "react";
import { cellAddress } from "./cellContext";
import { apiRequest, errorMessage } from "../../api/client";
import type { components } from "../../api/types";

type Event = components["schemas"]["TimelineEvent"];
type Timeline = components["schemas"]["ReviewTimeline"];
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
function clipped(text: string) {
  return text.length > 4000
    ? text.slice(0, 4000) +
        "\nPreview shortened. Full details remain in the saved event and timeline exports."
    : text;
}
function wording(value: unknown): string {
  const item = record(value);
  if (typeof item.text === "string") return clipped(item.text || "Blank");
  if (typeof item.value === "string")
    return clipped(`${item.kind ?? "Context"}: ${item.value}`);
  if (typeof item.verdict === "string")
    return clipped(
      `${item.verdict.replaceAll("_", " ")}${item.reason ? `: ${item.reason}` : ""}`,
    );
  if (typeof item.kind === "string")
    return clipped(`${item.kind}${item.reason ? `: ${item.reason}` : ""}`);
  if (typeof value === "string") return clipped(value);
  return value == null ? "None recorded" : clipped(JSON.stringify(value));
}
function address(value: unknown) {
  const cell = record(value);
  return typeof cell.row === "number" && typeof cell.col === "number"
    ? cellAddress({ row: cell.row, col: cell.col })
    : null;
}
function Snapshot({ value, other }: { value: unknown; other: unknown }) {
  const item = record(value);
  const comparison = record(other);
  const fields =
    typeof item.text === "string"
      ? [...new Set([...Object.keys(item), ...Object.keys(comparison)])].filter(
          (field) =>
            field !== "text" &&
            JSON.stringify(item[field]) !== JSON.stringify(comparison[field]),
        )
      : [];
  return (
    <>
      {wording(value)}
      {!!fields.length && (
        <dl>
          {fields.map((field) => (
            <div key={field}>
              <dt>{field.replaceAll("_", " ")}</dt>
              <dd>{wording(item[field])}</dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}
const eventNames: Record<Event["kind"], string> = {
  coverage_start: "Recording boundary",
  checks_changed: "Checks refreshed",
  cell_review: "Cell review changed",
  issue_decision: "Issue decision",
  approval: "Table approved",
  edit: "Revision saved",
  table_visibility: "Table removed or restored",
};

function Entry({
  event,
  selectedCellId,
  wholeTable,
}: {
  event: Event;
  selectedCellId?: string;
  wholeTable: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [limit, setLimit] = useState(50);
  const details = event.details;
  const changes = [
    ...(Array.isArray(details.changes) ? details.changes : []),
    ...(Array.isArray(details.context_changes) ? details.context_changes : []),
  ]
    .map(record)
    .filter((change) => wholeTable || change.cell_id === selectedCellId);
  const reopened = Array.isArray(details.invalidated_cell_ids)
    ? details.invalidated_cell_ids
    : [];
  const contexts = Array.isArray(details.invalidated_context_cell_ids)
    ? details.invalidated_context_cell_ids
    : [];
  return (
    <li className="timeline-entry">
      <div className="timeline-heading">
        <strong>{eventNames[event.kind]}</strong>
        <span>Event {event.sequence}</span>
      </div>
      <p>{String(details.reason ?? "Recorded action")}</p>
      <p className="timeline-meta">
        {event.actor_id ? `Reviewer ${event.actor_id.slice(0, 8)}` : "System"} ·{" "}
        {new Date(event.recorded_at).toLocaleString()} · Revision{" "}
        {event.revision_id.slice(0, 8)}
      </p>
      {!!reopened.length && (
        <p>
          {reopened.length} cell review{reopened.length === 1 ? "" : "s"}{" "}
          reopened.
          {selectedCellId && reopened.includes(selectedCellId)
            ? " Includes the selected cell."
            : ""}
        </p>
      )}
      {!!contexts.length && (
        <p>
          {contexts.length} context confirmation target
          {contexts.length === 1 ? "" : "s"} reopened.
        </p>
      )}
      {!!details.invalidated_approval_id && (
        <p>
          Approval {String(details.invalidated_approval_id).slice(0, 8)}{" "}
          invalidated.
        </p>
      )}
      {event.kind !== "coverage_start" && (
        <details onToggle={(event) => setExpanded(event.currentTarget.open)}>
          <summary>Inspect recorded changes</summary>
          {expanded && (
            <>
              {changes.slice(0, limit).map((change) => (
                <div
                  className="timeline-change"
                  key={`${String(change.cell_id)}/${String(change.kind ?? "cell")}`}
                >
                  <strong>
                    Cell{" "}
                    {address(change.after) ??
                      address(change.before) ??
                      String(change.cell_id)}
                    {change.kind ? ` (${String(change.kind)} context)` : ""}
                  </strong>
                  <dl>
                    <dt>Before</dt>
                    <dd>
                      <Snapshot value={change.before} other={change.after} />
                    </dd>
                    <dt>After</dt>
                    <dd>
                      <Snapshot value={change.after} other={change.before} />
                    </dd>
                  </dl>
                </div>
              ))}
              {changes.length > limit && (
                <button onClick={() => setLimit((value) => value + 50)}>
                  Show next 50 cell changes
                </button>
              )}
              {(event.kind === "cell_review" ||
                event.kind === "issue_decision") && (
                <dl>
                  <dt>Before</dt>
                  <dd>
                    {wording(
                      event.kind === "cell_review" &&
                        !wholeTable &&
                        selectedCellId
                        ? record(details.before)[selectedCellId]
                        : details.before,
                    )}
                  </dd>
                  <dt>After</dt>
                  <dd>
                    {wording(
                      event.kind === "cell_review" &&
                        !wholeTable &&
                        selectedCellId
                        ? record(details.after)[selectedCellId]
                        : details.after,
                    )}
                  </dd>
                </dl>
              )}
              {!changes.length &&
                event.kind !== "cell_review" &&
                event.kind !== "issue_decision" && (
                  <pre>{clipped(JSON.stringify(details, null, 2))}</pre>
                )}
              {!!changes.length && (
                <details>
                  <summary>Recorded event details</summary>
                  <pre>{clipped(JSON.stringify(details, null, 2))}</pre>
                </details>
              )}
            </>
          )}
        </details>
      )}
    </li>
  );
}

export function ReviewTimeline({
  revisionId,
  selectedCellId,
  refreshKey,
  active,
}: {
  revisionId: string;
  selectedCellId?: string;
  refreshKey: string;
  active: boolean;
}) {
  const [scope, setScope] = useState("cell");
  const key = `${revisionId}/${refreshKey}/${scope}/${scope === "cell" ? selectedCellId : "all"}`;
  const [cursor, setCursor] = useState({
    key: "",
    after: 0,
    through: undefined as number | undefined,
  });
  const [data, setData] = useState<{ key: string; value: Timeline } | null>(
    null,
  );
  const [failure, setFailure] = useState<{
    key: string;
    message: string;
  } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(false);
  const after = cursor.key === key ? cursor.after : 0;
  const through = cursor.key === key ? cursor.through : undefined;
  const visible = data?.key === key ? data.value : null;
  useEffect(() => {
    if (!active || (scope === "cell" && !selectedCellId)) return;
    const controller = new AbortController();
    setLoading(true);
    setFailure(null);
    const params = new URLSearchParams({ after: String(after) });
    if (through !== undefined) params.set("through", String(through));
    if (scope === "cell" && selectedCellId)
      params.set("cell_id", selectedCellId);
    apiRequest<Timeline>(`/api/v1/revisions/${revisionId}/timeline?${params}`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (controller.signal.aborted) return;
        setData((prior) => ({
          key,
          value: {
            ...value,
            items:
              after && prior?.key === key
                ? [
                    ...new Map(
                      [...prior.value.items, ...value.items].map((item) => [
                        item.sequence,
                        item,
                      ]),
                    ).values(),
                  ]
                : value.items,
          },
        }));
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setFailure({ key, message: errorMessage(error) });
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [active, key, revisionId, scope, selectedCellId, after, through, attempt]);
  return (
    <section className="review-timeline" aria-label="Ordered review timeline">
      <h3>Review timeline</h3>
      <label>
        Timeline scope
        <select
          value={scope}
          onChange={(event) => setScope(event.target.value)}
        >
          <option value="cell">Selected cell and table-wide actions</option>
          <option value="table">Whole table</option>
        </select>
      </label>
      <p className="fine-print">
        Oldest first. Event numbers show saved order. Earlier independent
        records remain below.
      </p>
      {failure?.key === key && (
        <div>
          <p role="alert">{failure.message}</p>
          <button
            disabled={loading}
            onClick={() => setAttempt((value) => value + 1)}
          >
            Retry timeline
          </button>
        </div>
      )}
      {loading && <p role="status">Loading timeline…</p>}
      {visible && (
        <>
          {!visible.items.length && (
            <p>
              No unified events for this selection. Check the whole-table scope
              or earlier history below.
            </p>
          )}
          <ol className="timeline-list">
            {visible.items.map((event) => (
              <Entry
                key={event.id}
                event={event}
                selectedCellId={selectedCellId}
                wholeTable={scope === "table"}
              />
            ))}
          </ol>
          {visible.has_more && (
            <button
              disabled={loading}
              onClick={() =>
                setCursor({
                  key,
                  after: visible.next_after_sequence,
                  through: visible.through_sequence,
                })
              }
            >
              Load more timeline events
            </button>
          )}
          {!visible.has_more && !!visible.items.length && (
            <p className="fine-print">
              Showing recorded history through event {visible.through_sequence}.
            </p>
          )}
        </>
      )}
    </section>
  );
}
