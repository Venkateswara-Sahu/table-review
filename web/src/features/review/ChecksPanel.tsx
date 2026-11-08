import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Crosshair,
  MessageSquareWarning,
  ShieldCheck,
  ShieldX,
} from "lucide-react";
import type { Issue, IssueDecision, ReviewState } from "../../api/models";
import { Badge, Button, InlineNote, Textarea } from "../../ui/primitives";
import { cx } from "../../ui/primitives";

const labels: Record<Issue["kind"], string> = {
  coverage_discrepancy: "Coverage discrepancy",
  duplicate_source_token: "Repeated source location",
  placement_ambiguity: "Placement ambiguity",
  structural_inconsistency: "Structural inconsistency",
  limited_reference: "Limited source reference",
  coverage_unavailable: "Coverage unavailable",
  unreviewed_blank: "Unreviewed blank",
  unreadable_value: "Unreadable value",
  subtotal_difference: "Declared subtotal difference",
  subtotal_unavailable: "Subtotal needs attention",
};

type DecisionKind = IssueDecision["kind"];
type Filter = "open" | "all" | "decided";

export function ChecksPanel({
  review,
  busy,
  dirty,
  onDecision,
  onInspect,
  selectedIssue,
}: {
  review: ReviewState;
  busy: boolean;
  dirty: boolean;
  onDecision: (id: string, kind: DecisionKind, reason: string) => void;
  onInspect: (issue: Issue) => void;
  selectedIssue: string | null;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState<Filter>("open");
  const [decideFor, setDecideFor] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!decideFor) return;
    const frame = requestAnimationFrame(() =>
      list.current
        ?.querySelector(`[data-issue="${decideFor}"] [data-decision-form]`)
        ?.scrollIntoView({ block: "end" }),
    );
    return () => cancelAnimationFrame(frame);
  }, [decideFor]);

  const open = review.issues.filter(
    (issue) =>
      !review.issue_decisions?.[issue.fingerprint] ||
      review.issue_decisions[issue.fingerprint].kind === "reopen",
  );
  const issues = review.issues.filter((issue) =>
    filter === "all"
      ? true
      : filter === "open"
        ? open.includes(issue)
        : !open.includes(issue),
  );
  const current = open.findIndex((issue) => issue.id === selectedIssue);

  function step(direction: number) {
    if (!open.length) return;
    const index =
      current < 0
        ? direction > 0
          ? 0
          : open.length - 1
        : (current + direction + open.length) % open.length;
    const next = open[index];
    onInspect(next);
    setFilter("open");
    requestAnimationFrame(() =>
      list.current
        ?.querySelector(`[data-issue="${next.id}"]`)
        ?.scrollIntoView({ block: "nearest" }),
    );
  }

  return (
    <section className="checks-panel" aria-label="Source checks">
      <header className="checks-head">
        <div>
          <h3>
            Source checks
            <span className="count-pill">{open.length}</span>
          </h3>
          <p className="muted small">
            {open.length
              ? `${open.length} unresolved · ${review.issues.length - open.length} of ${review.issues.length} decided`
              : review.issues.length
                ? "Every check has a recorded decision"
                : "No discrepancies flagged by the checks that ran"}
          </p>
        </div>
        <div className="checks-nav">
          <Button
            size="sm"
            variant="ghost"
            icon={<ArrowLeft size={14} />}
            disabled={busy || !open.length}
            onClick={() => step(-1)}
          >
            Previous
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy || !open.length}
            onClick={() => step(1)}
          >
            Next <ArrowRight size={14} />
          </Button>
        </div>
      </header>

      <p className="checks-status" role="status" data-testid="issue-progress">
        {open.length
          ? current >= 0
            ? `Unresolved issue ${current + 1} of ${open.length}`
            : `${open.length} unresolved issues`
          : "No unresolved issues"}
      </p>

      {review.checks_not_run && (
        <InlineNote tone="warning">
          <p>
            Some checks did not run. This stays an approval warning, separate
            from the issue count.
          </p>
        </InlineNote>
      )}

      <div className="checks-filter" role="group" aria-label="Filter checks">
        {(
          [
            ["open", `Open ${open.length}`],
            ["all", `All ${review.issues.length}`],
            ["decided", `Decided ${review.issues.length - open.length}`],
          ] as Array<[Filter, string]>
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="issue-list" ref={list}>
        {!issues.length && (
          <div className="checks-empty">
            <ShieldCheck size={20} aria-hidden="true" />
            <p>
              {filter === "open"
                ? "Nothing open. Every check has a recorded decision."
                : filter === "decided"
                  ? "No decisions recorded yet."
                  : "No checks were reported for this revision."}
            </p>
          </div>
        )}

        {issues.map((issue) => {
          const decision = review.issue_decisions?.[issue.fingerprint];
          const isOpen = !decision || decision.kind === "reopen";
          const cellReviewIssue =
            issue.kind === "unreviewed_blank" ||
            issue.kind === "unreadable_value";
          const reason = reasons[issue.id] ?? "";
          return (
            <article
              key={issue.id}
              data-issue={issue.id}
              aria-label={labels[issue.kind]}
              className={cx(
                "issue-card",
                selectedIssue === issue.id && "issue-card-active active-issue",
                !isOpen && "issue-card-decided",
              )}
            >
              <header className="issue-head">
                <span className="issue-icon" aria-hidden="true">
                  {issue.kind === "unreadable_value" ? (
                    <ShieldX size={15} />
                  ) : (
                    <MessageSquareWarning size={15} />
                  )}
                </span>
                <div className="issue-head-main">
                  <h4>{labels[issue.kind]}</h4>
                  <p className="tiny">
                    {Object.keys(issue.evidence).length
                      ? `${Object.keys(issue.evidence).length} evidence fields`
                      : "No evidence fields"}
                  </p>
                </div>
                <Badge tone={isOpen ? "warning" : "success"}>
                  {decision?.kind === "dismiss"
                    ? "Dismissed"
                    : decision?.kind === "resolved"
                      ? "Resolved"
                      : "Open"}
                </Badge>
              </header>

              <p className="issue-reason">{issue.reason}</p>
              {typeof issue.evidence.literal === "string" && (
                <p className="source-literal">
                  Source literal{" "}
                  <code className="mono">{issue.evidence.literal}</code>
                </p>
              )}

              <div className="issue-actions">
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Crosshair size={13} />}
                  disabled={busy}
                  aria-pressed={selectedIssue === issue.id}
                  onClick={() => onInspect(issue)}
                >
                  Inspect
                  {issue.affected_cell_ids.length
                    ? ` ${issue.affected_cell_ids.length} cell${issue.affected_cell_ids.length === 1 ? "" : "s"}`
                    : " source"}
                </Button>
                {!cellReviewIssue && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      setDecideFor((value) =>
                        value === issue.id ? null : issue.id,
                      )
                    }
                  >
                    {isOpen ? "Record a decision" : "Change decision"}
                  </Button>
                )}
              </div>

              {!issue.reference && !issue.affected_cell_ids.length && (
                <p className="tiny muted">
                  No source location was supplied. Compare the original page
                  manually.
                </p>
              )}

              {cellReviewIssue && (
                <p className="tiny muted">
                  Inspect the cell and use its review controls to confirm the
                  source or record it as unreadable.
                </p>
              )}

              {decision && (
                <div className="decision-record">
                  <p>{decision.reason}</p>
                  <p className="tiny muted">
                    {decision.kind === "dismiss" ? "Dismissed" : "Resolved"}
                    {" · "}
                    {new Date(decision.decided_at).toLocaleString()} · review{" "}
                    {decision.revision_id.slice(0, 8)}
                  </p>
                </div>
              )}

              {!cellReviewIssue && decideFor === issue.id && (
                <div className="decision-form" data-decision-form>
                  <Textarea
                    aria-label={`Decision reason for ${labels[issue.kind]}`}
                    placeholder="What did you compare, and why is this decision sound?"
                    rows={2}
                    maxLength={1000}
                    disabled={busy || dirty}
                    value={reason}
                    onChange={(event) =>
                      setReasons((value) => ({
                        ...value,
                        [issue.id]: event.target.value,
                      }))
                    }
                  />
                  <div className="row">
                    <Button
                      size="sm"
                      icon={<Check size={13} />}
                      disabled={busy || dirty || !reason.trim()}
                      onClick={() => {
                        onDecision(issue.id, "dismiss", reason.trim());
                        setDecideFor(null);
                      }}
                    >
                      Dismiss issue
                    </Button>
                    <Button
                      size="sm"
                      variant="ink"
                      disabled={busy || dirty || !reason.trim()}
                      onClick={() => {
                        onDecision(issue.id, "resolved", reason.trim());
                        setDecideFor(null);
                      }}
                    >
                      Record resolved
                    </Button>
                    {decision && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy || dirty || !reason.trim()}
                        onClick={() => {
                          onDecision(issue.id, "reopen", reason.trim());
                          setDecideFor(null);
                        }}
                      >
                        Reopen
                      </Button>
                    )}
                  </div>
                  <p className="tiny muted">
                    Every decision needs a reason. A dismissal is a human
                    judgment, not a parser correction.
                  </p>
                </div>
              )}

              <details className="issue-evidence">
                <summary>Check evidence</summary>
                <pre>{JSON.stringify(issue.evidence, null, 2)}</pre>
              </details>
            </article>
          );
        })}
      </div>

      <details className="check-scope">
        <summary>Check versions and scope</summary>
        <pre>{JSON.stringify(review.check_versions, null, 2)}</pre>
        <p className="small muted">No second extraction pass is enabled.</p>
      </details>
    </section>
  );
}
