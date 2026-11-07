import { useState } from "react";
import {
  Calculator,
  ChevronDown,
  FileJson,
  History as HistoryIcon,
  ListTree,
  Settings2,
  Trash2,
} from "lucide-react";
import type {
  Cell,
  EditOperation,
  Revision,
  ReviewState,
  SourceReference,
  Table,
  TableContent,
  TableView,
} from "../../api/models";
import { useContext } from "../../api/hooks";
import { Button } from "../../ui/primitives";
import type { Selection } from "../table/selection";
import { ContextPanel } from "./ContextPanel";
import { HistoryPanel } from "./HistoryPanel";
import { StructurePanel } from "./StructurePanel";
import { SubtotalsPanel } from "./SubtotalsPanel";

type Section = "structure" | "context" | "subtotals" | "history" | "details";

export function MorePanel({
  view,
  revision,
  review,
  cell,
  selected,
  selection,
  busy,
  dirty,
  subtotalDirty,
  onSave,
  onSource,
  onSelect,
  onRestore,
  onRemove,
  onSubtotalDirty,
  onPreview,
}: {
  view: TableView;
  revision: Revision;
  review?: ReviewState;
  cell?: Cell;
  selected: Cell[];
  selection: Selection;
  busy: boolean;
  dirty: boolean;
  subtotalDirty: boolean;
  onSave: (operation: EditOperation) => void;
  onSource: (reference: SourceReference) => void;
  onSelect: (id: string) => void;
  onRestore: (id: string) => void;
  onRemove: () => void;
  onSubtotalDirty: (value: boolean) => void;
  onPreview: (revision: Revision) => void;
}) {
  const [open, setOpen] = useState<Section | null>(null);
  const context = useContext(
    view.table.id,
    revision.id,
    cell?.id,
    open === "context",
  );

  function toggle(section: Section) {
    setOpen((value) => (value === section ? null : section));
  }

  return (
    <div className="more-panel">
      <Accordion
        icon={<Settings2 size={15} />}
        title="Table structure"
        description="Rows, columns, merged cells, headers and unit notes"
        open={open === "structure"}
        onToggle={() => toggle("structure")}
      >
        <StructurePanel
          content={revision.content}
          revisionId={revision.id}
          selection={selection}
          cell={cell}
          busy={busy}
          dirty={dirty}
          onSave={onSave}
        />
      </Accordion>

      <Accordion
        icon={<ListTree size={15} />}
        title="Source context"
        description="Header path, period, currency and scale wording"
        open={open === "context"}
        onToggle={() => toggle("context")}
      >
        <ContextPanel
          context={context.data ?? null}
          loading={context.isPending || context.isFetching}
          error={context.isError ? context.error.message : ""}
          selectedIds={selected.map((item) => item.id)}
          disabled={busy || dirty}
          onSource={onSource}
          onRetry={() => void context.refetch()}
          onSave={onSave}
        />
      </Accordion>

      <Accordion
        icon={<Calculator size={15} />}
        title="Declared subtotals"
        description="Signed sums of selected currency amounts"
        open={open === "subtotals"}
        onToggle={() => toggle("subtotals")}
      >
        <SubtotalsPanel
          tableId={view.table.id}
          revisionId={revision.id}
          content={revision.content}
          current={cell}
          selected={selected}
          active={open === "subtotals"}
          refreshKey={`${review?.generation ?? 0}`}
          disabled={busy || dirty}
          onSave={onSave}
          onDirty={onSubtotalDirty}
          onSelect={onSelect}
          onContext={() => setOpen("context")}
        />
      </Accordion>

      <Accordion
        icon={<HistoryIcon size={15} />}
        title="History and restore"
        description="Timeline, earlier revisions and restore as a new draft"
        open={open === "history"}
        onToggle={() => toggle("history")}
      >
        <HistoryPanel
          tableId={view.table.id}
          revisionId={revision.id}
          originalRevisionId={view.table.original_revision_id}
          cellId={cell?.id}
          busy={busy || dirty || subtotalDirty}
          onRestore={onRestore}
          onPreview={onPreview}
        />
      </Accordion>

      <Accordion
        icon={<FileJson size={15} />}
        title="Revision details"
        description="Extraction manifest, checksums and table actions"
        open={open === "details"}
        onToggle={() => toggle("details")}
      >
        <div className="revision-details">
          <dl className="key-values">
            <div>
              <dt>Revision</dt>
              <dd className="mono">{revision.id}</dd>
            </div>
            <div>
              <dt>Checksum</dt>
              <dd className="mono break">{revision.checksum}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{new Date(revision.created_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>
                {view.table.origin === "manual" ? "Manual grid" : "Extracted"}
              </dd>
            </div>
          </dl>
          <details>
            <summary>Extraction manifest</summary>
            <pre>{JSON.stringify(revision.extraction_manifest, null, 2)}</pre>
          </details>
          <div className="danger-zone">
            <div>
              <h4>Remove this table</h4>
              <p className="small muted">
                Cell reviews, issue decisions and approval are cleared. Saved
                content, history and existing exports stay with the report. You
                can restore it from Removed tables while the report is retained.
              </p>
            </div>
            <Button
              size="sm"
              variant="danger"
              icon={<Trash2 size={13} />}
              disabled={busy || dirty || subtotalDirty}
              onClick={onRemove}
            >
              Remove table
            </Button>
          </div>
        </div>
      </Accordion>
    </div>
  );
}

function Accordion({
  icon,
  title,
  description,
  open,
  onToggle,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className={`more-accordion ${open ? "more-accordion-open" : ""}`}>
      <button
        type="button"
        className="more-accordion-trigger"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className="more-accordion-icon" aria-hidden="true">
          {icon}
        </span>
        <span>
          <strong>{title}</strong>
          <span className="small muted">{description}</span>
        </span>
        <ChevronDown size={16} className="more-chevron" aria-hidden="true" />
      </button>
      {open && <div className="more-accordion-body">{children}</div>}
    </section>
  );
}
