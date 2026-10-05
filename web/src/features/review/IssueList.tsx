import { useState } from "react";
import type { components } from "../../api/types";
import type { ReviewState, SourceReference } from "../../api/models";

const labels: Record<components["schemas"]["Issue"]["kind"], string> = {
  coverage_discrepancy: "Coverage discrepancy",
  duplicate_source_token: "Repeated source location",
  placement_ambiguity: "Placement ambiguity",
  structural_inconsistency: "Structural inconsistency",
  limited_reference: "Limited source reference",
  coverage_unavailable: "Coverage unavailable",
};
type DecisionKind = components["schemas"]["IssueDecisionRecord"]["kind"];
export function IssueList({
  review,
  busy,
  onSource,
  onDecision,
}: {
  review: ReviewState;
  busy: boolean;
  onSource: (reference: SourceReference) => void;
  onDecision: (id: string, kind: DecisionKind, reason: string) => void;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  return (
    <section className="issue-list" aria-label="Source checks">
      <h3>Source checks</h3>
      <p>{review.unresolved_count} unresolved warnings</p>
      {review.issues.length === 0 && (
        <p className="fine-print">
          No discrepancies flagged by the checks that ran. Every cell still
          needs human review.
        </p>
      )}
      {review.issues.map((issue) => {
        const decision = review.issue_decisions?.[issue.fingerprint];
        const label = labels[issue.kind];
        const reason = reasons[issue.id] ?? "";
        return (
          <section key={issue.id} className="issue-card" aria-label={label}>
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
            {typeof issue.evidence.literal === "string" && (
              <p className="source-literal">
                Source literal: <strong>{issue.evidence.literal}</strong>
              </p>
            )}
            {issue.reference && (
              <button
                disabled={busy}
                onClick={() => onSource(issue.reference!)}
              >
                Show source evidence
              </button>
            )}
            {decision && (
              <div className="decision-record">
                <p>{decision.reason}</p>
                <p className="fine-print">
                  Recorded {new Date(decision.decided_at).toLocaleString()} for
                  this evidence. A dismissal is a human decision, not a parser
                  correction.
                </p>
              </div>
            )}
            <label htmlFor={`reason-${issue.id}`}>Decision reason</label>
            <textarea
              id={`reason-${issue.id}`}
              disabled={busy}
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
                disabled={busy || !reason.trim()}
                onClick={() => onDecision(issue.id, "dismiss", reason)}
              >
                Dismiss issue
              </button>
              <button
                disabled={busy || !reason.trim()}
                onClick={() => onDecision(issue.id, "resolved", reason)}
              >
                Record resolved
              </button>
              {decision && (
                <button
                  disabled={busy || !reason.trim()}
                  onClick={() => onDecision(issue.id, "reopen", reason)}
                >
                  Reopen issue
                </button>
              )}
            </div>
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
