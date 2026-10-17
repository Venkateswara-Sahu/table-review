import { useState } from "react";
import type { components } from "../../api/types";
import type { ReviewState } from "../../api/models";

const labels: Record<components["schemas"]["Issue"]["kind"], string> = {
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
type DecisionKind = components["schemas"]["IssueDecisionRecord"]["kind"];
export function IssueList({
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
  onInspect: (issue: components["schemas"]["Issue"]) => boolean;
  selectedIssue: string | null;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState("all");
  const open = review.issues.filter(
    (issue) =>
      !review.issue_decisions?.[issue.fingerprint] ||
      review.issue_decisions[issue.fingerprint].kind === "reopen",
  );
  const issues = review.issues.filter(
    (issue) =>
      filter === "all" ||
      (filter === "open" ? open.includes(issue) : !open.includes(issue)),
  );
  const current = open.findIndex((issue) => issue.id === selectedIssue);
  function stepIssue(direction: number) {
    if (!open.length) return;
    const index =
      current < 0
        ? direction > 0
          ? 0
          : open.length - 1
        : (current + direction + open.length) % open.length;
    const next = open[index];
    if (!onInspect(next)) return;
    setFilter("open");
    requestAnimationFrame(() =>
      document
        .getElementById(`issue-${next.id}`)
        ?.scrollIntoView({ block: "nearest" }),
    );
  }
  return (
    <section className="issue-list" aria-label="Source checks">
      <h3>Source checks</h3>
      <p>{review.unresolved_count} unresolved warnings</p>
      <p data-testid="issue-progress" role="status">
        {open.length
          ? current >= 0
            ? `Unresolved issue ${current + 1} of ${open.length}`
            : `${open.length} unresolved issues · choose Previous or Next`
          : "No unresolved issues"}
        {` · ${review.issues.length - open.length} of ${review.issues.length} checks decided`}
      </p>
      {review.checks_not_run && (
        <p className="fine-print">
          Some checks did not run. This remains an approval warning, separate
          from the issue count.
        </p>
      )}
      <div className="issue-navigation">
        <label>
          Show checks
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="all">All checks</option>
            <option value="open">Unresolved</option>
            <option value="decided">Decided</option>
          </select>
        </label>
        <button disabled={busy || !open.length} onClick={() => stepIssue(-1)}>
          Previous unresolved issue
        </button>
        <button disabled={busy || !open.length} onClick={() => stepIssue(1)}>
          Next unresolved issue
        </button>
      </div>
      {filter !== "all" && !issues.length && (
        <p>
          No {filter === "open" ? "unresolved" : "decided"} issues in this
          revision.
        </p>
      )}
      {review.issues.length === 0 && (
        <p className="fine-print">
          No discrepancies flagged by the checks that ran. Every cell still
          needs human review.
        </p>
      )}
      {issues.map((issue) => {
        const decision = review.issue_decisions?.[issue.fingerprint];
        const label = labels[issue.kind];
        const reason = reasons[issue.id] ?? "";
        const cellReviewIssue =
          issue.kind === "unreviewed_blank" ||
          issue.kind === "unreadable_value";
        return (
          <section
            key={issue.id}
            id={`issue-${issue.id}`}
            className={`issue-card ${selectedIssue === issue.id ? "active-issue" : ""}`}
            aria-label={label}
          >
            <div className="review-status">
              <strong>{label}</strong>
              <span>
                {decision?.kind === "dismiss"
                  ? "Dismissed"
                  : decision?.kind === "resolved"
                    ? "Recorded as resolved"
                    : "Open"}
              </span>
            </div>
            <p>{issue.reason}</p>
            <button
              disabled={busy}
              aria-pressed={selectedIssue === issue.id}
              onClick={() => onInspect(issue)}
            >
              Inspect affected cells
              {issue.affected_cell_ids.length
                ? ` (${issue.affected_cell_ids.length})`
                : " and source"}
            </button>
            {typeof issue.evidence.literal === "string" && (
              <p className="source-literal">
                Source literal: <strong>{issue.evidence.literal}</strong>
              </p>
            )}
            {issue.reference && (
              <button disabled={busy} onClick={() => onInspect(issue)}>
                Show source evidence
              </button>
            )}
            {!issue.reference && (
              <p className="fine-print">
                No source location was supplied for this check. Inspect affected
                cells when available, or compare the original page manually.
              </p>
            )}
            {decision && (
              <div className="decision-record">
                <p>{decision.reason}</p>
                <p className="fine-print">
                  Recorded {new Date(decision.decided_at).toLocaleString()} for
                  this evidence by reviewer {decision.actor_id.slice(0, 8)}. A
                  dismissal is a human decision, not a parser correction.
                </p>
              </div>
            )}
            {cellReviewIssue ? (
              <p className="fine-print">
                Inspect this cell and use its review controls to confirm the
                source or record it as unreadable.
              </p>
            ) : (
              <>
                <label htmlFor={`reason-${issue.id}`}>Decision reason</label>
                <textarea
                  id={`reason-${issue.id}`}
                  disabled={busy || dirty}
                  value={reason}
                  onChange={(event) =>
                    setReasons((value) => ({
                      ...value,
                      [issue.id]: event.target.value,
                    }))
                  }
                  maxLength={1000}
                />
                <div className="edit-toolbar">
                  <button
                    disabled={busy || dirty || !reason.trim()}
                    onClick={() => onDecision(issue.id, "dismiss", reason)}
                  >
                    Dismiss issue
                  </button>
                  <button
                    disabled={busy || dirty || !reason.trim()}
                    onClick={() => onDecision(issue.id, "resolved", reason)}
                  >
                    Record resolved
                  </button>
                  {decision && (
                    <button
                      disabled={busy || dirty || !reason.trim()}
                      onClick={() => onDecision(issue.id, "reopen", reason)}
                    >
                      Reopen issue
                    </button>
                  )}
                </div>
              </>
            )}
            <details>
              <summary>Check evidence</summary>
              <pre>{JSON.stringify(issue.evidence, null, 2)}</pre>
            </details>
          </section>
        );
      })}
      <details>
        <summary>Check versions and scope</summary>
        <pre>{JSON.stringify(review.check_versions, null, 2)}</pre>
        <p>No second extraction pass is enabled.</p>
      </details>
    </section>
  );
}
