import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Download,
  FileDown,
  FileSpreadsheet,
  LoaderCircle,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import {
  apiBlob,
  apiRequest,
  errorMessage,
  idempotencyKey,
} from "../../api/client";
import type {
  ExportFormat,
  ExportRecord,
  ReviewState,
  TableContent,
} from "../../api/models";
import { Dialog, DialogContent } from "../../ui/dialog";
import { Badge, Button, InlineNote } from "../../ui/primitives";
import { useNotices } from "../../app/notices";

export function FinishDialog({
  open,
  onOpenChange,
  tableId,
  revisionId,
  content,
  review,
  busy,
  exports,
  onApprove,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tableId: string;
  revisionId: string;
  content: TableContent;
  review?: ReviewState;
  busy: boolean;
  exports: ExportRecord[];
  onApprove: (warnings: boolean) => Promise<boolean>;
}) {
  const { notify, notifyError } = useNotices();
  const client = useQueryClient();
  const [approving, setApproving] = useState<"clean" | "warnings" | null>(null);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const keys = useRef<Partial<Record<ExportFormat, string>>>({});
  const timers = useRef<number[]>([]);

  useEffect(
    () => () => {
      for (const timer of timers.current) window.clearTimeout(timer);
    },
    [],
  );

  const total = content.cells.length;
  const reviewed = review?.checked_cell_ids.length ?? 0;
  const allReviewed = total > 0 && reviewed === total;
  const approval = review?.current_approval;
  const approved = !!approval;
  const unreadable = review?.unreadable_count ?? 0;
  const unresolved = review?.unresolved_count ?? 1;
  const checksNotRun = review?.checks_not_run ?? true;
  const currentExports = exports.filter(
    (item) => item.revision_id === revisionId,
  );
  const latest = currentExports[0];

  async function approve(warnings: boolean) {
    setApproving(warnings ? "warnings" : "clean");
    try {
      const ok = await onApprove(warnings);
      if (ok)
        notify(
          warnings
            ? "Approved with warnings. The warnings are preserved in the record."
            : "Table approved. Every cell has a recorded decision.",
          warnings ? "warning" : "success",
        );
    } finally {
      setApproving(null);
    }
  }

  async function startExport(format: ExportFormat) {
    setExporting(format);
    keys.current[format] ??= idempotencyKey();
    try {
      const record = await apiRequest<ExportRecord>(
        `/api/v1/tables/${tableId}/exports`,
        {
          method: "POST",
          headers: { "Idempotency-Key": keys.current[format]! },
          body: JSON.stringify({ revision_id: revisionId, format }),
        },
      );
      keys.current[format] = undefined;
      notify(
        format === "workbook"
          ? "Workbook export started"
          : "CSV package export started",
        "info",
      );
      void record;
      await client.invalidateQueries({ queryKey: ["exports", tableId] });
    } catch (error) {
      notifyError(error);
    } finally {
      setExporting(null);
    }
  }

  async function download(record: ExportRecord) {
    setDownloading(record.id);
    try {
      const blob = await apiBlob(`/api/v1/exports/${record.id}/download`);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      const status =
        record.manifest?.review_status === "reviewed" ||
        record.manifest?.review_status === "reviewed_with_warnings"
          ? record.manifest.review_status
          : "draft";
      anchor.download = `table-${status}-${record.revision_id}.${
        record.format === "workbook" ? "xlsx" : "zip"
      }`;
      anchor.click();
      const timer = window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      timers.current.push(timer);
    } catch (error) {
      notifyError(error);
    } finally {
      setDownloading(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={approved ? "Approved and ready to export" : "Finish this table"}
        description={
          approved
            ? "The approval pins the revision, the checks and every decision. Exports keep that record."
            : "A short preflight, then one decision to approve the revision."
        }
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Close
            </Button>
            {!approved ? (
              <>
                <Button
                  variant="secondary"
                  disabled={busy || !allReviewed || approving !== null}
                  loading={approving === "warnings"}
                  onClick={() => void approve(true)}
                >
                  Approve with warnings
                </Button>
                <Button
                  variant="primary"
                  icon={<ShieldCheck size={15} />}
                  disabled={
                    busy ||
                    !allReviewed ||
                    unresolved > 0 ||
                    unreadable > 0 ||
                    approving !== null
                  }
                  loading={approving === "clean"}
                  onClick={() => void approve(false)}
                >
                  Approve table
                </Button>
              </>
            ) : (
              <>
                <Button
                  variant="secondary"
                  icon={<FileDown size={15} />}
                  disabled={exporting !== null}
                  loading={exporting === "csv_package"}
                  onClick={() => void startExport("csv_package")}
                >
                  Export CSV package
                </Button>
                <Button
                  variant="primary"
                  icon={<FileSpreadsheet size={15} />}
                  disabled={exporting !== null}
                  loading={exporting === "workbook"}
                  onClick={() => void startExport("workbook")}
                >
                  Export workbook
                </Button>
              </>
            )}
          </>
        }
      >
        <div className="finish-preflight">
          <section className="preflight-list" aria-label="Preflight checks">
            <CheckLine
              passed={allReviewed}
              label="Every cell reviewed"
              detail={`${reviewed} of ${total} cells have a recorded decision`}
            />
            <CheckLine
              passed={!unreadable}
              warning={unreadable > 0}
              label="No unreadable values"
              detail={
                unreadable
                  ? `${unreadable} cell${unreadable === 1 ? "" : "s"} marked unreadable — approval must retain the warning`
                  : "No cell is marked unreadable"
              }
            />
            <CheckLine
              passed={!checksNotRun}
              warning={checksNotRun}
              label="Source checks ran"
              detail={
                checksNotRun
                  ? "Some checks did not run — approval must retain the warning"
                  : "The available check set ran on this revision"
              }
            />
            <CheckLine
              passed={unresolved === 0}
              warning={unresolved > 0}
              label="No unresolved issues"
              detail={
                unresolved
                  ? `${unresolved} unresolved check warning${unresolved === 1 ? "" : "s"}`
                  : "Every check has a recorded decision"
              }
            />
          </section>

          {approved ? (
            <InlineNote tone="success" icon={<CheckCircle2 size={16} />}>
              <div className="approval-note">
                <strong>
                  {approval.label === "reviewed"
                    ? "Reviewed"
                    : "Reviewed with warnings"}
                </strong>
                <span className="small">
                  Approved {new Date(approval.approved_at).toLocaleString()} ·{" "}
                  revision {approval.revision_id.slice(0, 8)} · checksum{" "}
                  <span className="mono">
                    {approval.content_checksum.slice(0, 12)}
                  </span>
                </span>
                <span className="tiny muted">
                  Editing this revision starts a new draft and clears the
                  approval.
                </span>
              </div>
            </InlineNote>
          ) : (
            <InlineNote
              tone={
                allReviewed
                  ? unresolved || unreadable
                    ? "warning"
                    : "info"
                  : "info"
              }
              icon={<AlertTriangle size={16} />}
            >
              <p>
                {!allReviewed
                  ? `Review the remaining ${total - reviewed} cell${total - reviewed === 1 ? "" : "s"} before approving. Use Next unchecked in the cell inspector.`
                  : unresolved || unreadable
                    ? "This revision can only be approved with warnings. The warnings stay attached to the approval and every export."
                    : "Every requirement is met. You can approve this revision now."}
              </p>
            </InlineNote>
          )}

          <section className="finish-exports" aria-label="Exports">
            <div className="finish-section-head">
              <div>
                <h3>Export</h3>
                <p className="muted small">
                  {approved
                    ? "Each export pins the approved revision and its review record."
                    : "You can export a draft, but it will be marked as a draft export."}
                </p>
              </div>
              {latest && (
                <Badge
                  tone={
                    latest.state === "succeeded"
                      ? "success"
                      : latest.state === "failed"
                        ? "danger"
                        : "warning"
                  }
                  dot
                >
                  {latest.state === "succeeded"
                    ? "Ready"
                    : latest.state === "failed"
                      ? "Failed"
                      : "Preparing"}
                </Badge>
              )}
            </div>

            {!approved && (
              <div className="draft-export-row">
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<FileSpreadsheet size={14} />}
                  disabled={busy || exporting !== null}
                  loading={exporting === "workbook"}
                  onClick={() => void startExport("workbook")}
                >
                  Workbook
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<FileDown size={14} />}
                  disabled={busy || exporting !== null}
                  loading={exporting === "csv_package"}
                  onClick={() => void startExport("csv_package")}
                >
                  CSV package
                </Button>
                <span className="tiny muted">
                  Produces a draft export of the current revision.
                </span>
              </div>
            )}

            {!!currentExports.length && (
              <ul className="export-list">
                {currentExports.slice(0, 4).map((record) => (
                  <li key={record.id}>
                    <span className="export-icon" aria-hidden="true">
                      {record.format === "workbook" ? (
                        <FileSpreadsheet size={16} />
                      ) : (
                        <FileDown size={16} />
                      )}
                    </span>
                    <div className="export-main">
                      <p>
                        {record.format === "workbook"
                          ? "Workbook (.xlsx)"
                          : "CSV package (.zip)"}
                      </p>
                      <p className="tiny muted">
                        {record.manifest?.review_status === "reviewed"
                          ? "Reviewed"
                          : record.manifest?.review_status ===
                              "reviewed_with_warnings"
                            ? "Reviewed with warnings"
                            : "Draft"}{" "}
                        · revision {record.revision_id.slice(0, 8)}
                      </p>
                    </div>
                    {record.state === "succeeded" ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        icon={<Download size={13} />}
                        loading={downloading === record.id}
                        onClick={() => void download(record)}
                      >
                        Download
                      </Button>
                    ) : record.state === "failed" ? (
                      <span className="tiny export-error">
                        <XCircle size={13} aria-hidden="true" />
                        {record.failure_code === "workbook_text_unsupported"
                          ? "Use a CSV package"
                          : "Export failed"}
                      </span>
                    ) : (
                      <span className="tiny muted export-pending">
                        <LoaderCircle className="button-spin" size={13} />
                        Preparing
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {!currentExports.length && approved && (
              <p className="tiny muted">
                No export has been requested for this revision yet.
              </p>
            )}
          </section>

          {!!review && review.issues.length > 0 && (
            <details className="finish-details">
              <summary>
                <Check size={13} aria-hidden="true" /> {review.issues.length}{" "}
                checks recorded for this revision
              </summary>
              <p className="small muted">
                {unresolved ? `${unresolved} remain open.` : "All are decided."}{" "}
                Open Checks to inspect the evidence behind each one.
              </p>
            </details>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function CheckLine({
  passed,
  warning,
  label,
  detail,
}: {
  passed: boolean;
  warning?: boolean;
  label: string;
  detail: string;
}) {
  return (
    <div
      className={`preflight-row ${
        passed
          ? "preflight-pass"
          : warning
            ? "preflight-warn"
            : "preflight-fail"
      }`}
    >
      <span className="preflight-mark" aria-hidden="true">
        {passed ? (
          <Check size={14} strokeWidth={2.8} />
        ) : warning ? (
          <AlertTriangle size={14} />
        ) : (
          <XCircle size={14} />
        )}
      </span>
      <div>
        <p>{label}</p>
        <p className="tiny muted">{detail}</p>
      </div>
    </div>
  );
}
