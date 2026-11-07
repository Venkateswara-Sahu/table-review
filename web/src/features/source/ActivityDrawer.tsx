import { useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  CircleSlash2,
  Clock3,
  Download,
  FileSearch,
  LoaderCircle,
  RotateCcw,
  XCircle,
} from "lucide-react";
import { useCancelTask, useRetryTask, useRuns } from "../../api/hooks";
import type { Document, Run, Task } from "../../api/models";
import { useNotices } from "../../app/notices";
import { Badge, Button, InlineNote, Spinner } from "../../ui/primitives";
import { Dialog, DialogContent } from "../../ui/dialog";
import { AdoptionPanel } from "../review/AdoptionPanel";

function taskLabel(task: Task) {
  const pages = Array.isArray(task.input.pages)
    ? (task.input.pages as number[])
    : [];
  const region = task.input.region as { page?: number } | null | undefined;
  return region
    ? `Region · page ${region.page}`
    : pages.length
      ? `Pages ${pages.join(", ")}`
      : task.kind.replaceAll("_", " ");
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

  const activeTasks = tasks.filter(
    (task) => task.state === "queued" || task.state === "running",
  );
  const pastTasks = tasks.filter(
    (task) => task.state !== "queued" && task.state !== "running",
  );
  const runItems = runs.data?.items ?? [];

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
              <h3>Extraction queue</h3>
              {!!activeTasks.length && (
                <Badge tone="warning" dot>
                  {activeTasks.length} active
                </Badge>
              )}
            </div>
            {!tasks.length && (
              <p className="small muted">
                No extraction work yet. Start one to create tables.
              </p>
            )}
            <ul className="task-list">
              {[...activeTasks, ...pastTasks].map((task) => (
                <li key={task.id} className={`task-row task-${task.state}`}>
                  <span className="task-icon" aria-hidden="true">
                    {task.state === "queued" || task.state === "running" ? (
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
                    <p>
                      {taskLabel(task)}
                      <Badge
                        tone={
                          task.state === "succeeded"
                            ? "success"
                            : task.state === "failed"
                              ? "danger"
                              : task.state === "cancelled"
                                ? "neutral"
                                : "warning"
                        }
                        className="badge-mini"
                      >
                        {task.state}
                      </Badge>
                    </p>
                    <p className="tiny muted">
                      {task.kind} · attempt {task.attempt_count} ·{" "}
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
                    {(task.state === "queued" || task.state === "running") && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={task.cancellation_requested}
                        onClick={() => void cancelTask(task.id)}
                      >
                        {task.cancellation_requested ? "Stopping…" : "Cancel"}
                      </Button>
                    )}
                    {(task.state === "failed" ||
                      task.state === "cancelled") && (
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
              ))}
            </ul>
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
                      {run.table_ids.map((id) => (
                        <Button
                          key={id}
                          size="sm"
                          variant="ghost"
                          icon={<ArrowUpRight size={12} />}
                          onClick={() => onOpenTable(id)}
                        >
                          Open {id.slice(0, 8)}
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
