import { useEffect, useId, useState } from "react";
import {
  Check,
  CheckCheck,
  ChevronDown,
  ClipboardCopy,
  FileSpreadsheet,
  Filter,
  MoreHorizontal,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Redo2,
  ShieldCheck,
  Tag,
  Undo2,
} from "lucide-react";
import type {
  BulkCellReviewInput,
  Snapshot,
  TableContent,
} from "../../api/models";
import { Menu } from "../../ui/Menu";
import { Popover, PopoverContent, PopoverTrigger } from "../../ui/popover";
import { Button, IconButton } from "../../ui/primitives";
import { tableShape } from "./cellMeta";

export function SheetToolbar({
  tableId,
  tableLabel,
  tableOptions,
  content,
  approval,
  dirtyLabel,
  busy,
  dirty,
  subtotalDirty,
  reviewLoaded,
  readyForApproval,
  canUndo,
  canRedo,
  railOpen,
  exceptionsOnly = false,
  exceptionRowCount = 0,
  reviewedCount = 0,
  totalCount = 0,
  onToggleExceptionsOnly,
  onOpenTable,
  onSaveTitle,
  onSaveTableContext,
  onUndo,
  onRedo,
  onBulkConfirm,
  onCopy,
  onFinish,
  onOpenDetails,
  onToggleRail,
}: {
  tableId: string;
  tableLabel: string;
  tableOptions: Array<{ id: string; label: string }>;
  content: TableContent;
  approval?: Snapshot | null;
  dirtyLabel: string;
  busy: boolean;
  dirty: boolean;
  subtotalDirty: boolean;
  reviewLoaded: boolean;
  readyForApproval: boolean;
  canUndo: boolean;
  canRedo: boolean;
  railOpen: boolean;
  exceptionsOnly?: boolean;
  exceptionRowCount?: number;
  reviewedCount?: number;
  totalCount?: number;
  onToggleExceptionsOnly?: () => void;
  onOpenTable: (id: string) => void;
  onSaveTitle: (title: string | null) => void;
  onSaveTableContext: (ctx: {
    currency: string | null;
    scale: string | null;
    period: string | null;
    measure: string | null;
  }) => void;
  onUndo: () => void;
  onRedo: () => void;
  onBulkConfirm: (scope: BulkCellReviewInput["scope"]) => void;
  onCopy: (format: "tsv" | "markdown" | "json") => void;
  onFinish: () => void;
  onOpenDetails: () => void;
  onToggleRail: () => void;
}) {
  const uid = useId();
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(content.title ?? "");
  const [ctxOpen, setCtxOpen] = useState(false);
  const [currency, setCurrency] = useState(
    content.table_context?.currency ?? "",
  );
  const [scale, setScale] = useState(content.table_context?.scale ?? "");
  const [period, setPeriod] = useState(content.table_context?.period ?? "");
  const [measure, setMeasure] = useState(content.table_context?.measure ?? "");

  useEffect(() => {
    setTitleDraft(content.title ?? "");
    setEditingTitle(false);
  }, [tableId, content.title]);

  useEffect(() => {
    if (ctxOpen) {
      setCurrency(content.table_context?.currency ?? "");
      setScale(content.table_context?.scale ?? "");
      setPeriod(content.table_context?.period ?? "");
      setMeasure(content.table_context?.measure ?? "");
    }
  }, [ctxOpen, content.table_context]);

  function submitTitle() {
    setEditingTitle(false);
    const cleaned = titleDraft.trim();
    const current = (content.title ?? "").trim();
    if (cleaned === current) return;
    onSaveTitle(cleaned || null);
  }

  const contextSummary = [
    content.table_context?.currency,
    content.table_context?.scale,
    content.table_context?.period,
    content.table_context?.measure,
  ]
    .filter((v): v is string => !!v?.trim())
    .join(" · ");

  return (
    <header className="sheet-head">
      <div className="sheet-title">
        <div className="sheet-title-copy">
          <label className="table-switch">
            <span className="sr-only">Switch table</span>
            <select
              value={tableId}
              disabled={busy}
              onChange={(event) => onOpenTable(event.target.value)}
            >
              {tableOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
              {!tableOptions.some((option) => option.id === tableId) && (
                <option value={tableId}>{tableLabel}</option>
              )}
            </select>
            <ChevronDown size={12} aria-hidden="true" />
          </label>

          {editingTitle ? (
            <input
              type="text"
              className="sheet-title-input"
              aria-label="Table title"
              maxLength={200}
              autoFocus
              value={titleDraft}
              placeholder="Name this table…"
              onChange={(event) => setTitleDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  if (event.nativeEvent.isComposing || event.keyCode === 229)
                    return;
                  submitTitle();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  setTitleDraft(content.title ?? "");
                  setEditingTitle(false);
                }
              }}
              onBlur={submitTitle}
            />
          ) : (
            <button
              type="button"
              className="sheet-name-button"
              disabled={busy}
              title={content.title || "Click to name this table"}
              onClick={() => setEditingTitle(true)}
            >
              <span className={content.title ? "" : "muted"}>
                {content.title || "Untitled table"}
              </span>
              <Pencil size={11} aria-hidden="true" />
            </button>
          )}

          <span className="sheet-shape">{tableShape(content)}</span>
        </div>

        <Popover open={ctxOpen} onOpenChange={setCtxOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              className={`table-context-pill ${contextSummary ? "has-context" : ""}`}
              disabled={busy}
              title="Set table-wide currency, scale, period, and measure"
            >
              <Tag size={11} aria-hidden="true" />
              <span>{contextSummary || "Units & Period"}</span>
            </button>
          </PopoverTrigger>
          <PopoverContent align="start" width={290}>
            <form
              className="context-popover-form"
              onSubmit={(event) => {
                event.preventDefault();
                setCtxOpen(false);
                onSaveTableContext({
                  currency: currency.trim() || null,
                  scale: scale.trim() || null,
                  period: period.trim() || null,
                  measure: measure.trim() || null,
                });
              }}
            >
              <div className="context-popover-title">Table-wide context</div>
              <div className="context-popover-grid">
                <label className="field">
                  <span className="field-label">Currency</span>
                  <input
                    id={`${uid}-cur`}
                    className="input"
                    value={currency}
                    maxLength={32}
                    placeholder="USD"
                    onChange={(e) => setCurrency(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Scale</span>
                  <input
                    id={`${uid}-scale`}
                    className="input"
                    value={scale}
                    maxLength={64}
                    placeholder="millions"
                    onChange={(e) => setScale(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Period</span>
                  <input
                    id={`${uid}-per`}
                    className="input"
                    value={period}
                    maxLength={120}
                    placeholder="FY 2025"
                    onChange={(e) => setPeriod(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Measure</span>
                  <input
                    id={`${uid}-meas`}
                    className="input"
                    value={measure}
                    maxLength={120}
                    placeholder="Revenue"
                    onChange={(e) => setMeasure(e.target.value)}
                  />
                </label>
              </div>
              <div className="row spread">
                <Button
                  size="sm"
                  variant="ghost"
                  type="button"
                  onClick={() => setCtxOpen(false)}
                >
                  Cancel
                </Button>
                <Button size="sm" variant="primary" type="submit">
                  Apply
                </Button>
              </div>
            </form>
          </PopoverContent>
        </Popover>

        {approval && (
          <span className="review-approved-inline">
            <ShieldCheck size={12} aria-hidden="true" />
            {approval.label === "reviewed"
              ? "Approved"
              : "Approved with warnings"}
          </span>
        )}
      </div>

      <span className="spacer" />

      {dirtyLabel && (
        <span
          className={`review-dirty-chip ${
            subtotalDirty || dirty ? "is-active" : ""
          }`}
          role="status"
        >
          {dirtyLabel}
        </span>
      )}

      <div className="sheet-toolbar-actions">
        {(exceptionRowCount > 0 || exceptionsOnly) &&
          onToggleExceptionsOnly && (
            <button
              type="button"
              className={`exception-filter-pill ${exceptionsOnly ? "is-active" : ""}`}
              aria-pressed={exceptionsOnly}
              title="Filter grid to rows with open issues, unreadable cells, or subtotal differences"
              onClick={onToggleExceptionsOnly}
            >
              <Filter size={12} aria-hidden="true" />
              <span>
                Exceptions
                {exceptionRowCount > 0 ? ` (${exceptionRowCount})` : ""}
              </span>
            </button>
          )}
        <IconButton
          label="Undo (Ctrl+Z)"
          size="sm"
          disabled={!canUndo}
          onClick={onUndo}
        >
          <Undo2 size={14} />
        </IconButton>
        <IconButton
          label="Redo (Ctrl+Shift+Z)"
          size="sm"
          disabled={!canRedo}
          onClick={onRedo}
        >
          <Redo2 size={14} />
        </IconButton>

        <Menu
          label="Confirm clean cells"
          trigger={
            <Button
              size="sm"
              variant="ghost"
              disabled={busy || dirty || subtotalDirty || !reviewLoaded}
              icon={<Check size={13} />}
            >
              Confirm
            </Button>
          }
          items={[
            {
              label: "Confirm all clean cells",
              onSelect: () => onBulkConfirm("clean_all"),
            },
            {
              label: "Confirm current row",
              onSelect: () => onBulkConfirm("row"),
            },
            {
              label: "Confirm current column",
              onSelect: () => onBulkConfirm("column"),
            },
            {
              label: "Confirm selected cells",
              onSelect: () => onBulkConfirm("cells"),
            },
          ]}
        />

        <Menu
          label="Copy formatted table"
          trigger={
            <IconButton label="Copy table" size="sm" disabled={busy}>
              <ClipboardCopy size={14} />
            </IconButton>
          }
          items={[
            {
              label: "Copy for Excel / Sheets (TSV)",
              icon: <FileSpreadsheet size={13} />,
              onSelect: () => onCopy("tsv"),
            },
            {
              label: "Copy as Markdown",
              onSelect: () => onCopy("markdown"),
            },
            {
              label: "Copy as JSON",
              onSelect: () => onCopy("json"),
            },
          ]}
        />

        <Button
          size="sm"
          variant={readyForApproval ? "primary" : "ghost"}
          className="finish-button"
          icon={<CheckCheck size={15} />}
          disabled={busy || dirty || subtotalDirty || !reviewLoaded}
          onClick={onFinish}
        >
          Finish
        </Button>

        <IconButton label="Table details" size="sm" onClick={onOpenDetails}>
          <MoreHorizontal size={15} />
        </IconButton>

        <IconButton
          label={railOpen ? "Hide inspector panel" : "Show inspector panel"}
          size="sm"
          onClick={onToggleRail}
        >
          {railOpen ? (
            <PanelRightClose size={15} />
          ) : (
            <PanelRightOpen size={15} />
          )}
        </IconButton>
      </div>
      {totalCount > 0 && (
        <progress
          className="sheet-progress"
          value={reviewedCount}
          max={totalCount}
          aria-label={`Table review progress: ${reviewedCount} of ${totalCount} cells reviewed`}
        />
      )}
    </header>
  );
}
