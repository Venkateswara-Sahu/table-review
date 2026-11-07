import { useId, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  CircleSlash2,
  Eraser,
  RotateCcw,
  ScanText,
} from "lucide-react";
import type {
  Cell,
  CellVerdict,
  ReviewState,
  SourceReference,
  TableContent,
} from "../../api/models";
import {
  Badge,
  Button,
  InlineNote,
  Select,
  Textarea,
} from "../../ui/primitives";
import { cellAddress } from "../table/address";
import type { EditDraft } from "./draft";

const verdictLabel: Record<CellVerdict, string> = {
  confirmed_value: "Value confirmed",
  confirmed_blank: "Blank confirmed",
  unreadable: "Unreadable",
  unreviewed: "Not reviewed",
};

const verdictTone: Record<
  CellVerdict,
  "success" | "warning" | "danger" | "neutral"
> = {
  confirmed_value: "success",
  confirmed_blank: "success",
  unreadable: "danger",
  unreviewed: "neutral",
};

export function CellInspector({
  cell,
  content,
  selected,
  draft,
  onDraft,
  onSave,
  onDiscard,
  busy,
  dirty,
  review,
  onVerdict,
  onSource,
  onNextUnchecked,
}: {
  cell?: Cell;
  content: TableContent;
  selected: Cell[];
  draft: EditDraft;
  onDraft: (value: EditDraft) => void;
  onSave: (operation: import("../../api/models").EditOperation) => void;
  onDiscard: () => void;
  busy: boolean;
  dirty: boolean;
  review?: ReviewState;
  onVerdict: (verdict: CellVerdict, reason?: string) => void;
  onSource: (reference: SourceReference) => void;
  onNextUnchecked: () => void;
}) {
  const id = useId();
  const editor = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState("");
  const [unreadableOpen, setUnreadableOpen] = useState(false);

  if (!cell) {
    return (
      <div className="inspector-empty">
        <p className="muted">Select a cell in the table to inspect it.</p>
      </div>
    );
  }

  const verdict = review?.cell_reviews?.[cell.id]?.verdict ?? "unreviewed";
  const record = review?.cell_reviews?.[cell.id];
  const references = cell.source_references.length
    ? cell.source_references
    : [cell.reference];
  const anyValue = selected.some((item) => !!item.text.trim());
  const anyBlank = selected.some((item) => !item.text.trim());
  const reviewedSelected = selected.filter(
    (item) =>
      review?.cell_reviews?.[item.id] &&
      review.cell_reviews[item.id].verdict !== "unreviewed",
  );
  const totalReviewed = review?.checked_cell_ids.length ?? 0;
  const remaining = content.cells.length - totalReviewed;

  return (
    <div className="cell-inspector">
      <header className="cell-head">
        <span className="cell-address-badge mono">{cellAddress(cell)}</span>
        <div className="cell-head-main">
          <p className="cell-head-value">
            {cell.text || <span className="faint">Blank cell</span>}
          </p>
          <p className="cell-head-meta">
            Row {cell.row + 1} · Column {cell.col + 1} · {cell.row_span}×
            {cell.col_span}
            {cell.role !== "data" ? ` · ${cell.role.replaceAll("_", " ")}` : ""}
          </p>
        </div>
        <Badge tone={verdictTone[verdict]} dot>
          {verdictLabel[verdict]}
        </Badge>
      </header>

      {selected.length > 1 && (
        <div className="bulk-strip">
          <div>
            <strong>{selected.length} cells selected</strong>
            <span className="muted small">
              {" "}
              · verdicts apply to the whole selection
            </span>
          </div>
          <div className="bulk-actions">
            <Button
              size="sm"
              disabled={busy || dirty || anyBlank}
              icon={<Check size={14} />}
              onClick={() => onVerdict("confirmed_value")}
            >
              Confirm values
            </Button>
            <Button
              size="sm"
              disabled={busy || dirty || anyValue}
              onClick={() => onVerdict("confirmed_blank")}
            >
              Confirm blanks
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy || dirty || !reviewedSelected.length}
              icon={<Eraser size={14} />}
              onClick={() => onVerdict("unreviewed")}
            >
              Clear
            </Button>
          </div>
        </div>
      )}

      <div className="cell-edit">
        <div className="field-label-row">
          <label className="field-label" htmlFor={`${id}-text`}>
            Cell value
          </label>
          <span className="tiny muted">Ctrl / ⌘ + Enter saves</span>
        </div>
        <Textarea
          ref={editor}
          id={`${id}-text`}
          data-cell-text-editor
          className="cell-textarea"
          value={draft.text}
          disabled={busy}
          rows={2}
          onChange={(event) => onDraft({ ...draft, text: event.target.value })}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              onDiscard();
            }
            if (
              (event.ctrlKey || event.metaKey) &&
              event.key === "Enter" &&
              !busy &&
              draft.text !== cell.text
            ) {
              event.preventDefault();
              onSave({
                kind: "set_text",
                cell_id: cell.id,
                text: draft.text,
              });
            }
          }}
        />
        <div className="cell-edit-row">
          <label className="cell-interpretation">
            <span className="field-label">Interpretation</span>
            <Select
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
          </label>
          <div className="cell-edit-buttons">
            <Button
              size="sm"
              disabled={busy || draft.interpretation === cell.interpretation}
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
            <Button
              size="sm"
              variant="primary"
              disabled={busy || draft.text === cell.text}
              onClick={() =>
                onSave({
                  kind: "set_text",
                  cell_id: cell.id,
                  text: draft.text,
                })
              }
            >
              Save value
            </Button>
          </div>
        </div>
        <div className="cell-literal">
          <span className="tiny muted">Original literal</span>
          <code className="mono">{cell.raw_text || "(blank)"}</code>
        </div>
      </div>

      <div className="verdict-bar">
        <div className="verdict-actions">
          <Button
            size="sm"
            variant={verdict === "confirmed_value" ? "primary" : "secondary"}
            disabled={busy || dirty || anyBlank}
            icon={<Check size={14} />}
            onClick={() => onVerdict("confirmed_value")}
          >
            Confirm value
          </Button>
          <Button
            size="sm"
            disabled={busy || dirty || anyValue}
            onClick={() => onVerdict("confirmed_blank")}
          >
            Confirm blank
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy || dirty}
            aria-expanded={unreadableOpen}
            icon={<CircleSlash2 size={14} />}
            onClick={() => setUnreadableOpen((value) => !value)}
          >
            Unreadable
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy || dirty || !reviewedSelected.length}
            icon={<RotateCcw size={14} />}
            onClick={() => onVerdict("unreviewed")}
          >
            Clear review
          </Button>
        </div>
        <div className="verdict-progress tiny muted">
          <span className="num">
            {totalReviewed}/{content.cells.length}
          </span>{" "}
          reviewed
          <button
            type="button"
            className="link-button"
            disabled={busy || dirty || remaining === 0}
            onClick={onNextUnchecked}
          >
            Next unchecked <ArrowRight size={12} aria-hidden="true" />
          </button>
        </div>
      </div>

      {unreadableOpen && (
        <InlineNote tone="warning" icon={<ScanText size={15} />}>
          <div className="unreadable-form">
            <label className="field-label" htmlFor={`${id}-reason`}>
              Why is the source unreadable?
            </label>
            <Textarea
              id={`${id}-reason`}
              rows={2}
              maxLength={1000}
              value={reason}
              disabled={busy || dirty}
              onChange={(event) => setReason(event.target.value)}
            />
            <div className="row">
              <Button
                size="sm"
                variant="primary"
                disabled={busy || dirty || !reason.trim()}
                onClick={() => {
                  onVerdict("unreadable", reason.trim());
                  setReason("");
                  setUnreadableOpen(false);
                }}
              >
                Record unreadable
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setUnreadableOpen(false)}
              >
                Cancel
              </Button>
            </div>
            <p className="tiny muted">
              The draft text stays unchanged. The cell keeps a warning and
              approval must be recorded with warnings.
            </p>
          </div>
        </InlineNote>
      )}

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

      <details className="provenance">
        <summary>Source and table notes</summary>
        <div className="provenance-body">
          <p className="small">
            {content.unit_notes.length
              ? content.unit_notes.join("; ")
              : "No table notes. Confirm units, period and scale in More → Source context."}
          </p>
          <div className="provenance-links">
            {references.map((reference, index) => (
              <button
                type="button"
                key={index}
                className="source-link"
                onClick={() => onSource(reference)}
              >
                <span className="mono">p.{reference.page}</span>
                <span>{reference.precision} reference</span>
                <ArrowRight size={12} aria-hidden="true" />
              </button>
            ))}
          </div>
        </div>
      </details>
    </div>
  );
}
