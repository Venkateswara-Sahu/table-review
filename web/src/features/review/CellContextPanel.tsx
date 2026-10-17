import { useState } from "react";
import type {
  CellContext,
  EditOperation,
  SourceReference,
} from "../../api/models";

type Kind = "period" | "currency" | "scale" | "measure";
const fields: [Kind, string][] = [
  ["period", "Reporting period"],
  ["currency", "Currency"],
  ["scale", "Scale wording"],
  ["measure", "Measure"],
];
export function CellContextPanel({
  context,
  error,
  selectedIds,
  disabled,
  onSource,
  onRetry,
  onSave,
}: {
  context: CellContext | null;
  error: string;
  selectedIds: string[];
  disabled: boolean;
  onRetry: () => void;
  onSource: (reference: SourceReference) => void;
  onSave: (operation: EditOperation) => void;
}) {
  const [choices, setChoices] = useState<Partial<Record<Kind, string>>>({});
  if (error)
    return (
      <div>
        <p role="alert">Context could not be loaded. {error}</p>
        <button disabled={disabled} onClick={onRetry}>
          Retry source context
        </button>
      </div>
    );
  if (!context) return <p role="status">Loading source context…</p>;
  const sources = new Map(context.sources.map((source) => [source.id, source]));
  const path = [...context.row_headers, ...context.column_headers];
  return (
    <div className="source-context">
      <p className="context-intro">
        Check the source wording, then confirm it for{" "}
        {selectedIds.length === 1
          ? "this cell"
          : `all ${selectedIds.length} selected cells`}
        . Values stay as written; confirming scale does not multiply them.
      </p>
      {disabled && (
        <p className="context-notice">
          Save or discard unsaved edits before confirming context.
        </p>
      )}
      <div className="context-path" aria-label="Header sources">
        <h3>Header path</h3>
        {path.length ? (
          path.map((source) => (
            <div key={source.id}>
              <span>
                {source.text || "Blank header"}
                {source.origin === "current_draft" ? " (edited)" : ""}
              </span>
              <button
                disabled={disabled}
                onClick={() => onSource(source.references[0])}
              >
                View header source: {source.text || "blank"}
              </button>
            </div>
          ))
        ) : (
          <p>
            No mapped header path. Set header rows and label columns in
            Structure.
          </p>
        )}
      </div>
      {context.warnings.map((warning) => (
        <p className="context-notice" key={warning}>
          {warning}
        </p>
      ))}
      {fields.map(([kind, title]) => {
        const proposals = context.proposals.filter(
          (item) => item.kind === kind,
        );
        const confirmed = context.confirmed.find((item) => item.kind === kind);
        const choice = choices[kind] ?? confirmed?.id ?? proposals[0]?.id ?? "";
        const candidate = proposals.find((item) => item.id === choice);
        return (
          <fieldset key={kind} className="context-field">
            <legend>{title}</legend>
            {proposals.length ? (
              <>
                <label htmlFor={`context-${kind}`}>
                  Source wording for {title.toLowerCase()}
                </label>
                <select
                  id={`context-${kind}`}
                  value={choice}
                  disabled={disabled}
                  onChange={(event) =>
                    setChoices((value) => ({
                      ...value,
                      [kind]: event.target.value,
                    }))
                  }
                >
                  {proposals.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.value}
                    </option>
                  ))}
                </select>
                {candidate?.source_ids.map((identity) => {
                  const source = sources.get(identity);
                  return source ? (
                    <div className="context-evidence" key={identity}>
                      <blockquote>{source.text}</blockquote>
                      {source.references.map((reference, i) => (
                        <button
                          key={i}
                          disabled={disabled}
                          onClick={() => onSource(reference)}
                        >
                          View {title.toLowerCase()} source {i + 1} (page{" "}
                          {reference.page}, {reference.precision} reference)
                        </button>
                      ))}
                    </div>
                  ) : null;
                })}
                <div className="context-actions">
                  <button
                    className="primary"
                    disabled={disabled || !candidate || !selectedIds.length}
                    onClick={() =>
                      candidate &&
                      onSave({
                        kind: "confirm_context",
                        cell_ids: selectedIds,
                        candidate_id: candidate.id,
                      })
                    }
                  >
                    Confirm {title.toLowerCase()}
                    {selectedIds.length > 1 ? " for selection" : ""}
                  </button>
                  <button
                    disabled={disabled || !confirmed}
                    onClick={() =>
                      onSave({
                        kind: "reopen_context",
                        cell_ids: selectedIds,
                        context_kind: kind,
                      })
                    }
                  >
                    Reopen {title.toLowerCase()}
                  </button>
                </div>
              </>
            ) : (
              <p>
                No source-backed {title.toLowerCase()} proposal. Check the
                document; this field remains unconfirmed.
              </p>
            )}
            {confirmed ? (
              <p className="context-confirmed">
                Confirmed for this cell: {confirmed.value}. Recorded{" "}
                {confirmed.confirmed_at
                  ? new Date(confirmed.confirmed_at).toLocaleString()
                  : ""}{" "}
                by {confirmed.confirmed_by}.
              </p>
            ) : (
              <p className="context-unconfirmed">Unconfirmed for this cell</p>
            )}
          </fieldset>
        );
      })}
    </div>
  );
}
