import { useId } from "react";
import { ArrowRight, Locate } from "lucide-react";
import type {
  Cell,
  ReviewState,
  SourceReference,
  TableContent,
} from "../../api/models";
import { Button, Select } from "../../ui/primitives";
import { cellAddress } from "../table/address";
import { cellName, verdictLabel } from "./cellMeta";
import type { EditDraft } from "./draft";

export function CellRail({
  cell,
  content,
  review,
  draft,
  onDraft,
  onSave,
  busy,
  dirty,
  onSource,
}: {
  cell?: Cell;
  content: TableContent;
  review?: ReviewState;
  draft: EditDraft;
  onDraft: (value: EditDraft) => void;
  onSave: (operation: import("../../api/models").EditOperation) => void;
  busy: boolean;
  dirty: boolean;
  onSource: (reference: SourceReference) => void;
}) {
  const id = useId();

  if (!cell) {
    return (
      <p className="rail-empty" role="status">
        Select a cell in the table to review it.
      </p>
    );
  }

  const record = review?.cell_reviews?.[cell.id];
  const references = cell.source_references.length
    ? cell.source_references
    : [cell.reference];
  const textDirty = draft.text !== cell.text;

  return (
    <div className="cell-rail">
      <header className="rail-cell-head">
        <span className="cell-address-badge mono">{cellAddress(cell)}</span>
        <div className="rail-cell-title">
          <strong>{cellName(content, cell)}</strong>
          <span>
            {cell.row_span}×{cell.col_span} · {cell.role.replaceAll("_", " ")}
          </span>
        </div>
      </header>

      <section className="rail-section">
        <div className="rail-label">Original literal</div>
        <p className="rail-literal mono">{cell.raw_text || "(blank)"}</p>
      </section>

      <section className="rail-section">
        <label className="rail-label" htmlFor={`${id}-interpretation`}>
          Interpretation
        </label>
        <Select
          id={`${id}-interpretation`}
          value={draft.interpretation}
          disabled={busy}
          onChange={(event) =>
            onDraft({
              ...draft,
              interpretation: event.target.value as Cell["interpretation"],
            })
          }
        >
          <option value="text">Text — preserve literal</option>
          <option value="en_us_number">English US number</option>
          <option value="percent">Percent — explicit % only</option>
          <option value="uncertain">Uncertain</option>
        </Select>
        <Button
          size="sm"
          className="rail-inline-button"
          disabled={
            busy || textDirty || draft.interpretation === cell.interpretation
          }
          onClick={() =>
            onSave({
              kind: "set_interpretation",
              cell_id: cell.id,
              interpretation: draft.interpretation,
            })
          }
        >
          Save type
        </Button>
        {textDirty && (
          <p className="rail-hint">
            Save or discard the value edit in the tray before changing type.
          </p>
        )}
      </section>

      <section className="rail-section">
        <div className="rail-label">Source</div>
        <div className="rail-evidence">
          {references.map((reference, index) => (
            <button
              type="button"
              key={index}
              className="source-link"
              onClick={() => onSource(reference)}
            >
              <span className="mono">p.{reference.page}</span>
              <span>{reference.precision} reference</span>
              <Locate size={12} aria-hidden="true" />
            </button>
          ))}
        </div>
        <button
          type="button"
          className="rail-locate"
          onClick={() => onSource(cell.reference)}
        >
          Locate in source
          <ArrowRight size={12} aria-hidden="true" />
        </button>
      </section>

      {record && record.verdict !== "unreviewed" && (
        <p className="cell-record small muted">
          {verdictLabel[record.verdict]}
          {record.reason ? ` — “${record.reason}”` : ""}
          {record.actor_id ? ` · reviewer ${record.actor_id.slice(0, 8)}` : ""}
          {record.decided_at
            ? ` · ${new Date(record.decided_at).toLocaleString()}`
            : ""}
        </p>
      )}

      <details className="rail-notes">
        <summary>Table notes</summary>
        <p>
          {content.unit_notes.length
            ? content.unit_notes.join("; ")
            : "No table notes. Confirm units, period and scale in Details → Source context."}
        </p>
      </details>
    </div>
  );
}
