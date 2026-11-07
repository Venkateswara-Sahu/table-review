import { useState } from "react";
import {
  BookOpenText,
  Check,
  CircleHelp,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import type {
  CellContext,
  EditOperation,
  SourceReference,
} from "../../api/models";
import { Badge, Button, InlineNote, Select } from "../../ui/primitives";

type Kind = "period" | "currency" | "scale" | "measure";

const fields: Array<[Kind, string]> = [
  ["period", "Reporting period"],
  ["currency", "Currency"],
  ["scale", "Scale wording"],
  ["measure", "Measure"],
];

export function ContextPanel({
  context,
  loading,
  error,
  selectedIds,
  disabled,
  onSource,
  onRetry,
  onSave,
}: {
  context: CellContext | null;
  loading: boolean;
  error: string;
  selectedIds: string[];
  disabled: boolean;
  onSource: (reference: SourceReference) => void;
  onRetry: () => void;
  onSave: (operation: EditOperation) => void;
}) {
  const [choices, setChoices] = useState<Partial<Record<Kind, string>>>({});

  if (loading && !context)
    return (
      <p role="status" className="muted small">
        Loading source context…
      </p>
    );

  if (error)
    return (
      <InlineNote tone="danger" title="Context could not be loaded">
        <p>{error}</p>
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Retry source context
        </Button>
      </InlineNote>
    );

  if (!context)
    return (
      <div className="context-empty">
        <CircleHelp size={22} aria-hidden="true" />
        <p className="small muted">
          Select a cell to inspect its row and column headers, period, currency
          and scale.
        </p>
      </div>
    );

  const sources = new Map(context.sources.map((source) => [source.id, source]));
  const path = [...context.row_headers, ...context.column_headers];

  return (
    <div className="context-panel">
      <div className="more-intro">
        <BookOpenText size={16} aria-hidden="true" />
        <p className="small">
          Check the source wording, then confirm it for{" "}
          {selectedIds.length === 1
            ? "this cell"
            : `all ${selectedIds.length} selected cells`}
          . Values stay as written; confirming scale does not multiply them.
        </p>
      </div>

      {disabled && (
        <InlineNote tone="warning">
          <p>Save or discard unsaved edits before confirming context.</p>
        </InlineNote>
      )}

      <section className="context-path">
        <h4>Header path</h4>
        {path.length ? (
          <ul>
            {path.map((source) => (
              <li key={source.id}>
                <span>
                  {source.text || "Blank header"}
                  {source.origin === "current_draft" && (
                    <Badge tone="info" className="badge-mini">
                      edited
                    </Badge>
                  )}
                </span>
                <button
                  type="button"
                  className="link-button"
                  disabled={disabled}
                  onClick={() => onSource(source.references[0])}
                >
                  View source
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="small muted">
            No mapped header path. Set header rows and label columns in
            Structure.
          </p>
        )}
      </section>

      {context.warnings.map((warning) => (
        <InlineNote tone="warning" key={warning}>
          <p>{warning}</p>
        </InlineNote>
      ))}

      <div className="context-fields">
        {fields.map(([kind, title]) => {
          const proposals = context.proposals.filter(
            (item) => item.kind === kind,
          );
          const confirmed = context.confirmed.find(
            (item) => item.kind === kind,
          );
          const choice =
            choices[kind] ?? confirmed?.id ?? proposals[0]?.id ?? "";
          const candidate = proposals.find((item) => item.id === choice);
          return (
            <fieldset className="context-field" key={kind}>
              <legend>{title}</legend>
              {proposals.length ? (
                <>
                  <Select
                    aria-label={`Source wording for ${title.toLowerCase()}`}
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
                  </Select>
                  {candidate?.source_ids.map((identity) => {
                    const source = sources.get(identity);
                    return source ? (
                      <div className="context-evidence" key={identity}>
                        <blockquote>{source.text}</blockquote>
                        <div className="row">
                          {source.references.map((reference, index) => (
                            <button
                              type="button"
                              className="link-button"
                              key={index}
                              disabled={disabled}
                              onClick={() => onSource(reference)}
                            >
                              Source {index + 1} · page {reference.page}
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null;
                  })}
                  <div className="row">
                    <Button
                      size="sm"
                      variant="primary"
                      icon={<Check size={13} />}
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
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<RotateCcw size={13} />}
                      disabled={disabled || !confirmed}
                      onClick={() =>
                        onSave({
                          kind: "reopen_context",
                          cell_ids: selectedIds,
                          context_kind: kind,
                        })
                      }
                    >
                      Reopen
                    </Button>
                  </div>
                </>
              ) : (
                <p className="small muted">
                  No source-backed {title.toLowerCase()} proposal. Check the
                  document; this field remains unconfirmed.
                </p>
              )}
              {confirmed ? (
                <p className="context-confirmed small">
                  <Sparkles size={12} aria-hidden="true" />
                  Confirmed: {confirmed.value}
                </p>
              ) : (
                <p className="context-unconfirmed small">Unconfirmed</p>
              )}
            </fieldset>
          );
        })}
      </div>
    </div>
  );
}
