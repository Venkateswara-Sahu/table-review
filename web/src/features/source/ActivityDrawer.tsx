import { useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  ChevronDown,
  CircleSlash2,
  Clock3,
  Download,
  FileSearch,
  LoaderCircle,
  RotateCcw,
  Table2,
  XCircle,
} from "lucide-react";
import { useCancelTask, useRetryTask, useRuns } from "../../api/hooks";
import type { Document, Run, Task } from "../../api/models";
import { useNotices } from "../../app/notices";
import { Badge, Button, InlineNote, Spinner } from "../../ui/primitives";
import { Dialog, DialogContent } from "../../ui/dialog";
import { AdoptionPanel } from "../review/AdoptionPanel";

function taskPages(task: Task) {
  const pages = Array.isArray(task.input.pages)
    ? (task.input.pages as number[])
    : [];
  const region = task.input.region as { page?: number } | null | undefined;
  if (region?.page && !pages.includes(region.page))
    return [...pages, region.page].sort((a, b) => a - b);
  return pages.sort((a, b) => a - b);
}

function pageGroupLabel(tasks: Task[]) {
  const pages = taskPages(tasks[0]);
  if (!pages.length) return "Document work";
  return pages.length === 1 ? `Page ${pages[0]}` : `Pages ${pages.join(", ")}`;
}

function taskStatus(task: Task) {
  switch (task.state) {
    case "queued":
      return { label: "Queued", tone: "warning" as const };
    case "running":
      return { label: "Extracting", tone: "warning" as const };
    case "failed":
      return { label: "Extraction stopped", tone: "danger" as const };
    case "cancelled":
      return { label: "Cancelled", tone: "neutral" as const };
    default:
      return { label: "Ready", tone: "success" as const };
  }
}

function taskSummary(task: Task) {
  if (task.state === "failed")
    return task.failure_code === "unsupported_page"
      ? "This page has no usable digital text."
      : task.failure_code === "ocr_unavailable"
        ? "Offline OCR models were unavailable."
        : "Extraction stopped before it finished.";
  if (task.state === "queued") return "Waiting for the extractor.";
  if (task.state === "running") return "Reading the page and building a draft.";
  if (task.state === "cancelled") return "Stopped on request.";
  if (task.result?.conversion_outcome === "partial_success")
    return "Completed with warnings. Inspect the evidence before trusting every cell.";
  if (task.kind !== "extract") return "Completed.";
  return "Completed. Saved tables are ready to review.";
}

function timeOf(value: string) {
  return new Date(value).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

function isActive(task: Task) {
  return task.state === "queued" || task.state === "running";
}

function taskTables(task: Task) {
  const ids = task.result?.table_ids;
  return Array.isArray(ids)
    ? ids.filter((id): id is string => typeof id === "string")
    : [];
}

function groupTasks(tasks: Task[]) {
  const groups = new Map<string, Task[]>();
  for (const task of tasks) {
    const key = taskPages(task).join(",") || "other";
    groups.set(key, [...(groups.get(key) ?? []), task]);
  }
  return [...groups.entries()]
    .map(([key, items]) => ({
      key,
      tasks: [...items].sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    }))
    .sort((a, b) => {
      const activeA = a.tasks.some(isActive) ? 1 : 0;
      const activeB = b.tasks.some(isActive) ? 1 : 0;
      if (activeA !== activeB) return activeB - activeA;
      return (
        new Date(b.tasks[0].created_at).getTime() -
        new Date(a.tasks[0].created_at).getTime()
      );
    });
}

function outcome(run: Run) {
  if (run.manifest.parser_executed === false) return "Synthetic test fixture";
  switch (run.manifest.conversion_outcome) {
    case "success":
      return "Conversion completed";
    case "partial_success":
      return "Partial result — inspect warnings";
    case "failure":
      return "Conversion failed";
    default:
      return "Earlier run — outcome not recorded";
  }
}

function downloadRun(run: Run) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(run, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `extraction-${run.id}.json`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ActivityDrawer({
  open,
  onOpenChange,
  document,
  tasks,
  onExtract,
  onOpenTable,
  onAdopted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  document: Document;
  tasks: Task[];
  onExtract: () => void;
  onOpenTable: (id: string) => void;
  onAdopted: (targetId: string) => void;
}) {
  const runs = useRuns(document.id, open);
  const cancel = useCancelTask(document.id);
  const retry = useRetryTask(document.id);
  const { notifyError } = useNotices();
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  async function cancelTask(id: string) {
    try {
      await cancel.mutateAsync(id);
    } catch (error) {
      notifyError(error);
    }
  }

  async function retryTask(id: string) {
    try {
      await retry.mutateAsync(id);
    } catch (error) {
      notifyError(error);
    }
  }

  const groups = groupTasks(tasks);
  const activeCount = tasks.filter(isActive).length;
  const runItems = runs.data?.items ?? [];

  function taskRow(task: Task, { retry = true }: { retry?: boolean } = {}) {
    return (
      <li key={task.id} className={`task-row task-${task.state}`}>
        <span className="task-icon" aria-hidden="true">
          {isActive(task) ? (
            <LoaderCircle className="button-spin" size={15} />
          ) : task.state === "failed" ? (
            <XCircle size={15} />
          ) : task.state === "cancelled" ? (
            <CircleSlash2 size={15} />
          ) : (
            <FileSearch size={15} />
          )}
        </span>
        <div className="task-main">
          <p>{task.kind.replaceAll("_", " ")}</p>
          <p className="tiny muted">
            attempt {task.attempt_count} ·{" "}
            {new Date(task.created_at).toLocaleString()}
          </p>
          {task.failure_code && (
            <p className="tiny error">
              {task.failure_code === "unsupported_page"
                ? "A selected page has no usable digital text."
                : task.failure_code === "ocr_unavailable"
                  ? "Offline OCR models are unavailable."
                  : task.failure_code.replaceAll("_", " ")}
            </p>
          )}
        </div>
        <div className="task-actions">
          {isActive(task) && (
            <Button
              size="sm"
              variant="ghost"
              disabled={task.cancellation_requested}
              onClick={() => void cancelTask(task.id)}
            >
              {task.cancellation_requested ? "Stopping…" : "Cancel"}
            </Button>
          )}
          {(task.state === "failed" || task.state === "cancelled") && retry && (
            <Button
              size="sm"
              variant="ghost"
              icon={<RotateCcw size={13} />}
              onClick={() => void retryTask(task.id)}
            >
              Retry
            </Button>
          )}
        </div>
      </li>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        side="right"
        title="Activity"
        description={`Extraction work and run history for ${document.filename}`}
        footer={
          <Button variant="secondary" onClick={onExtract}>
            New extraction
          </Button>
        }
      >
        <div className="activity-drawer">
          <section className="activity-section">
            <div className="activity-head">
              <h3>By page</h3>
              {!!activeCount && (
                <Badge tone="warning" dot>
                  {activeCount} active
                </Badge>
              )}
            </div>
            {!tasks.length && (
              <p className="small muted">
                No extraction work yet. Start one to create tables.
              </p>
            )}
            {groups.map((group) => {
              const active = group.tasks.filter(isActive);
              const past = group.tasks.filter((task) => !isActive(task));
              const lead = active[0] ?? past[0];
              if (!lead) return null;
              const status = taskStatus(lead);
              const older = active.length ? past : past.slice(1);
              const savedIds = [
                ...new Set(group.tasks.flatMap((task) => taskTables(task))),
              ];
              return (
                <div className="activity-group" key={group.key}>
                  <div className="activity-group-head">
                    <span className={`dot ${lead.state}`} aria-hidden="true" />
                    <strong>{pageGroupLabel(group.tasks)}</strong>
                    <Badge tone={status.tone} className="badge-mini">
                      {status.label}
                    </Badge>
                    <span className="activity-time">
                      {timeOf(lead.created_at)}
                    </span>
                  </div>
                  <p className="activity-summary">
                    {taskSummary(lead)}
                    {past.length > 1
                      ? ` ${past.length} attempts kept; earlier work was not replaced.`
                      : ""}
                  </p>
                  <ul className="task-list">
                    {active.map((task) => taskRow(task))}
                    {!active.length && taskRow(lead, { retry: false })}
                  </ul>
                  {(!!savedIds.length ||
                    lead.state === "failed" ||
                    lead.state === "cancelled") && (
                    <div className="activity-saved">
                      <span className="activity-saved-label">
                        <Table2 size={13} aria-hidden="true" />
                        {savedIds.length
                          ? `${savedIds.length} saved table${
                              savedIds.length === 1 ? "" : "s"
                            }`
                          : "No saved table"}
                      </span>
                      <div className="row">
                        {savedIds.map((id, index) => (
                          <Button
                            key={id}
                            size="sm"
                            variant="ghost"
                            icon={<ArrowUpRight size={12} />}
                            onClick={() => onOpenTable(id)}
                          >
                            Open table
                            {savedIds.length > 1 ? ` ${index + 1}` : ""}
                          </Button>
                        ))}
                        {(lead.state === "failed" ||
                          lead.state === "cancelled") && (
                          <Button
                            size="sm"
                            variant="secondary"
                            icon={<RotateCcw size={13} />}
                            onClick={() => void retryTask(lead.id)}
                          >
                            Retry page
                          </Button>
                        )}
                      </div>
                    </div>
                  )}
                  {!!older.length && (
                    <details className="activity-attempts">
                      <summary className="attempt-head">
                        <ChevronDown size={13} aria-hidden="true" />
                        {older.length} earlier attempt
                        {older.length === 1 ? "" : "s"}
                        <span className="attempt-count">
                          {timeOf(older[older.length - 1].created_at)} –{" "}
                          {timeOf(older[0].created_at)}
                        </span>
                      </summary>
                      <div className="timeline">
                        {older.map((task) => (
                          <div className="timeline-row" key={task.id}>
                            <span className="timeline-dot" />
                            <div className="timeline-main">
                              <strong>{taskStatus(task).label}</strong>
                              <span>
                                {taskSummary(task).replace(/\.$/, "")} ·{" "}
                                {task.kind.replaceAll("_", " ")}
                              </span>
                            </div>
                            <span className="timeline-time">
                              {timeOf(task.created_at)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              );
            })}
          </section>

          <section className="activity-section">
            <div className="activity-head">
              <h3>Extraction runs</h3>
              <Button
                size="sm"
                variant="ghost"
                icon={<Clock3 size={13} />}
                disabled={runs.isFetching}
                onClick={() => void runs.refetch()}
              >
                Refresh
              </Button>
            </div>
            {runs.isPending && (
              <p className="small muted" role="status">
                <Spinner size={14} /> Loading run history…
              </p>
            )}
            {runs.isError && (
              <InlineNote tone="danger">
                <p>{runs.error.message}</p>
                <Button size="sm" onClick={() => void runs.refetch()}>
                  Try again
                </Button>
              </InlineNote>
            )}
            {!runs.isPending && !runItems.length && (
              <p className="small muted">
                No runs yet. Each extraction is saved here with its evidence.
              </p>
            )}
            <ol className="run-list">
              {runItems.map((run) => {
                const comparison =
                  run.manifest.comparison &&
                  typeof run.manifest.comparison === "object"
                    ? (run.manifest.comparison as Record<string, unknown>)
                    : {};
                const matches = Array.isArray(comparison.matches)
                  ? comparison.matches
                  : [];
                const ambiguous = Array.isArray(comparison.ambiguous)
                  ? comparison.ambiguous.length
                  : 0;
                const expanded = expandedRun === run.id;
                return (
                  <li className="run-row" key={run.id}>
                    <div className="run-head">
                      <div>
                        <p>{outcome(run)}</p>
                        <p className="tiny muted">
                          {Array.isArray(
                            (run.manifest.request as Record<string, unknown>)
                              ?.pages,
                          )
                            ? `Pages ${(
                                (
                                  run.manifest.request as Record<
                                    string,
                                    unknown
                                  >
                                ).pages as number[]
                              ).join(", ")}`
                            : "Pages unavailable"}{" "}
                          · {run.table_ids.length} tables · run{" "}
                          {run.id.slice(0, 8)}
                        </p>
                      </div>
                      <Badge
                        tone={
                          run.manifest.conversion_outcome === "failure"
                            ? "danger"
                            : run.manifest.conversion_outcome ===
                                "partial_success"
                              ? "warning"
                              : "success"
                        }
                        dot
                      >
                        {run.manifest.conversion_outcome === "failure"
                          ? "Failed"
                          : run.manifest.conversion_outcome ===
                              "partial_success"
                            ? "Partial"
                            : "Complete"}
                      </Badge>
                    </div>

                    {!!run.warnings.length && (
                      <p className="tiny warning-text">
                        <AlertTriangle size={12} aria-hidden="true" />{" "}
                        {run.warnings.length} warning
                        {run.warnings.length === 1 ? "" : "s"} recorded
                      </p>
                    )}

                    <div className="run-actions">
                      {run.table_ids.map((id, index) => (
                        <Button
                          key={id}
                          size="sm"
                          variant="ghost"
                          icon={<ArrowUpRight size={12} />}
                          onClick={() => onOpenTable(id)}
                        >
                          Open table
                          {run.table_ids.length > 1 ? ` ${index + 1}` : ""}
                        </Button>
                      ))}
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-expanded={expanded}
                        onClick={() => setExpandedRun(expanded ? null : run.id)}
                      >
                        {expanded ? "Hide evidence" : "Run evidence"}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<Download size={12} />}
                        onClick={() => downloadRun(run)}
                      >
                        JSON
                      </Button>
                    </div>

                    {expanded && (
                      <div className="run-evidence">
                        {!!run.warnings.length && (
                          <ul className="run-warnings">
                            {run.warnings.map((warning, index) => (
                              <li key={index}>{warning}</li>
                            ))}
                          </ul>
                        )}
                        <details>
                          <summary className="tiny muted">
                            Recorded manifest
                          </summary>
                          <pre>{JSON.stringify(run.manifest, null, 2)}</pre>
                        </details>
                      </div>
                    )}

                    {matches.length > 0 && (
                      <div className="run-adoption">
                        <p className="tiny muted">
                          {matches.length} candidate pair
                          {matches.length === 1 ? "" : "s"}
                          {ambiguous ? ` · ${ambiguous} ambiguous` : ""}. New
                          extractions keep earlier work until adopted.
                        </p>
                        {matches.map((value, index) => {
                          const match =
                            value && typeof value === "object"
                              ? (value as Record<string, unknown>)
                              : {};
                          return typeof match.before_id === "string" &&
                            typeof match.after_id === "string" ? (
                            <AdoptionPanel
                              key={`${run.id}-${index}`}
                              targetId={match.before_id}
                              candidateId={match.after_id}
                              disabled={!open}
                              onOpenTable={onOpenTable}
                              onAdopted={(targetId) => {
                                onAdopted(targetId);
                                onOpenTable(targetId);
                              }}
                            />
                          ) : null;
                        })}
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
