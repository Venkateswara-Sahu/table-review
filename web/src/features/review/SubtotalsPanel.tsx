import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Calculator, Check, Plus, Trash2 } from "lucide-react";
import { apiRequest } from "../../api/client";
import type {
  Cell,
  EditOperation,
  SubtotalGroup,
  SubtotalResults,
  SubtotalTerm,
  TableContent,
} from "../../api/models";
import { Badge, Button, InlineNote, Input, Select } from "../../ui/primitives";
import { cellAddress } from "../table/address";

export function SubtotalsPanel({
  tableId,
  revisionId,
  content,
  current,
  selected,
  active,
  refreshKey,
  disabled,
  onSave,
  onDirty,
  onSelect,
  onContext,
}: {
  tableId: string;
  revisionId: string;
  content: TableContent;
  current?: Cell;
  selected: Cell[];
  active: boolean;
  refreshKey: string;
  disabled: boolean;
  onSave: (operation: EditOperation) => void;
  onDirty: (value: boolean) => void;
  onSelect: (id: string) => void;
  onContext: () => void;
}) {
  const [id, setId] = useState<string>(() => crypto.randomUUID());
  const [name, setName] = useState("");
  const [total, setTotal] = useState("");
  const [terms, setTerms] = useState<SubtotalTerm[]>([]);
  const [tolerance, setTolerance] = useState("0");
  const [confirmed, setConfirmed] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [setupOpen, setSetupOpen] = useState(!content.subtotal_groups.length);
  const results = useQuery({
    queryKey: ["subtotals", tableId, revisionId, refreshKey],
    queryFn: () =>
      apiRequest<SubtotalResults>(
        `/api/v1/tables/${tableId}/revisions/${revisionId}/subtotals`,
      ),
    enabled: active,
  });

  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);

  // A saved group arrives in the new revision content; the form no longer holds
  // unsaved setup once that revision is in hand.
  useEffect(() => {
    if (content.subtotal_groups.some((group) => group.id === id)) {
      setDirty(false);
      setConfirmed(false);
    }
  }, [revisionId, content.subtotal_groups, id]);

  function change() {
    setDirty(true);
    setConfirmed(false);
  }

  function label(identity: string) {
    const cell = content.cells.find((value) => value.id === identity);
    return cell
      ? `${cellAddress(cell)}: ${cell.text || "blank"}`
      : "Missing cell";
  }

  function load(group?: SubtotalGroup) {
    setId(group?.id ?? crypto.randomUUID());
    setName(group?.name ?? "");
    setTotal(group?.total_cell_id ?? "");
    setTerms(group?.terms ?? []);
    setTolerance(group?.tolerance ?? "0");
    setConfirmed(false);
    // Opening a saved relation is a read. Only an actual field change makes
    // the setup dirty, so merely reviewing it cannot block navigation.
    setDirty(false);
    setSetupOpen(!!group || !content.subtotal_groups.length);
  }

  const items = results.data?.items ?? [];

  return (
    <div className="subtotal-panel">
      <div className="more-intro">
        <Calculator size={16} aria-hidden="true" />
        <p className="small">
          Check a signed sum of selected currency amounts. Confirm period,
          currency and scale for every participating cell first. No scale
          conversion, inferred formula or financial approval.
        </p>
      </div>

      <details
        className="subtotal-setup"
        open={setupOpen}
        onToggle={(event) => setSetupOpen(event.currentTarget.open)}
      >
        <summary>
          {content.subtotal_groups.some((group) => group.id === id)
            ? "Review saved relation"
            : "New subtotal relation"}
        </summary>
        <div className="subtotal-form">
          <div className="row spread">
            <label className="field">
              <span className="field-label">Subtotal name</span>
              <Input
                maxLength={80}
                value={name}
                disabled={disabled}
                placeholder="e.g. Total operating expenses"
                onChange={(event) => {
                  setName(event.target.value);
                  change();
                }}
              />
            </label>
            <Button size="sm" variant="ghost" onClick={onContext}>
              Open source context
            </Button>
          </div>

          <div className="subtotal-total">
            <span className="field-label">Reported total</span>
            <span className={total ? "mono small" : "small faint"}>
              {total ? label(total) : "Select the total cell in the grid"}
            </span>
            <Button
              size="sm"
              disabled={disabled || !current}
              onClick={() => {
                if (!current) return;
                setTotal(current.id);
                setTerms((value) =>
                  value.filter((term) => term.cell_id !== current.id),
                );
                change();
              }}
            >
              Use current cell
            </Button>
          </div>

          <div className="subtotal-inputs-head">
            <span className="field-label">Input cells</span>
            <Button
              size="sm"
              icon={<Plus size={13} />}
              disabled={disabled || !selected.length}
              onClick={() => {
                setTerms((value) => [
                  ...value,
                  ...selected
                    .filter(
                      (cell) =>
                        cell.id !== total &&
                        !value.some((term) => term.cell_id === cell.id),
                    )
                    .map((cell) => ({ cell_id: cell.id, sign: 1 as const })),
                ]);
                change();
              }}
            >
              Add selection
            </Button>
          </div>
          {!terms.length ? (
            <p className="tiny muted">
              Select numeric cells in the grid (Shift + click for a range), then
              add them here. Each physical cell counts once.
            </p>
          ) : (
            <ul className="subtotal-inputs">
              {terms.map((term) => (
                <li key={term.cell_id}>
                  <Select
                    aria-label={`Sign for ${label(term.cell_id)}`}
                    value={term.sign}
                    disabled={disabled}
                    onChange={(event) => {
                      setTerms((value) =>
                        value.map((item) =>
                          item.cell_id === term.cell_id
                            ? {
                                ...item,
                                sign: event.target.value === "-1" ? -1 : 1,
                              }
                            : item,
                        ),
                      );
                      change();
                    }}
                  >
                    <option value="1">+ Add</option>
                    <option value="-1">− Subtract</option>
                  </Select>
                  <button
                    type="button"
                    className="link-button truncate"
                    onClick={() => onSelect(term.cell_id)}
                  >
                    {label(term.cell_id)}
                  </button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove ${label(term.cell_id)}`}
                    onClick={() => {
                      setTerms((value) =>
                        value.filter((item) => item.cell_id !== term.cell_id),
                      );
                      change();
                    }}
                  >
                    <Trash2 size={13} />
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <label className="field">
            <span className="field-label">Allowed absolute difference</span>
            <Input
              inputMode="decimal"
              value={tolerance}
              disabled={disabled}
              onChange={(event) => {
                setTolerance(event.target.value);
                change();
              }}
            />
            <span className="field-hint">
              In the same displayed units. Zero means exact equality. No
              automatic rounding allowance.
            </span>
          </label>

          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={disabled}
              onChange={(event) => {
                setConfirmed(event.target.checked);
                setDirty(true);
              }}
            />
            I checked the source scope and the selected relation
          </label>

          <div className="row">
            <Button
              size="sm"
              variant="ink"
              icon={<Check size={13} />}
              disabled={
                disabled ||
                !confirmed ||
                !name.trim() ||
                !total ||
                !terms.length ||
                terms.length > 100
              }
              onClick={() =>
                onSave({
                  kind: "confirm_subtotal",
                  group_id: id,
                  name: name.trim(),
                  total_cell_id: total,
                  terms,
                  tolerance,
                  confirmed_scope: true,
                })
              }
            >
              Confirm subtotal
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={disabled}
              onClick={() => load()}
            >
              Discard setup
            </Button>
          </div>
        </div>
      </details>

      <section className="subtotal-results">
        <h4>Saved checks</h4>
        {results.isError ? (
          <InlineNote tone="danger">
            <p>{results.error.message}</p>
            <Button size="sm" onClick={() => void results.refetch()}>
              Retry
            </Button>
          </InlineNote>
        ) : results.isPending ? (
          <p className="small muted" role="status">
            Loading subtotal results…
          </p>
        ) : !items.length ? (
          <p className="small muted">
            No declared subtotal checks. Source-transcription checks remain in
            Checks.
          </p>
        ) : (
          items.map((result) => (
            <article
              className={`subtotal-result ${
                result.state === "match"
                  ? "subtotal-match"
                  : result.state === "difference"
                    ? "subtotal-difference"
                    : ""
              }`}
              key={result.group.id}
            >
              <div className="row spread">
                <h5>{result.group.name}</h5>
                <Badge
                  tone={
                    result.state === "match"
                      ? "success"
                      : result.state === "difference"
                        ? "danger"
                        : "warning"
                  }
                  dot
                >
                  {result.state === "match"
                    ? "Within tolerance"
                    : result.state === "difference"
                      ? "Differs"
                      : result.state === "needs_confirmation"
                        ? "Confirm again"
                        : "Unavailable"}
                </Badge>
              </div>
              <p className="small muted">{result.reason}</p>
              <p className="tiny mono subtotal-expression">
                {label(result.group.total_cell_id)} ={" "}
                {result.group.terms
                  .map(
                    (term) =>
                      `${term.sign === 1 ? "+" : "−"} ${label(term.cell_id)}`,
                  )
                  .join(" ")}
              </p>
              {result.input_sum != null && (
                <dl className="subtotal-math">
                  <div>
                    <dt>Input sum</dt>
                    <dd className="num">{result.input_sum}</dd>
                  </div>
                  <div>
                    <dt>Reported total</dt>
                    <dd className="num">{result.reported_total}</dd>
                  </div>
                  <div>
                    <dt>Difference</dt>
                    <dd className="num">{result.difference}</dd>
                  </div>
                </dl>
              )}
              <div className="row">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={disabled}
                  onClick={() => load(result.group)}
                >
                  Review relation
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={disabled || dirty}
                  onClick={() =>
                    onSave({
                      kind: "remove_subtotal",
                      group_id: result.group.id,
                    })
                  }
                >
                  Remove
                </Button>
                <details>
                  <summary className="tiny muted">Evidence</summary>
                  <pre>{JSON.stringify(result, null, 2)}</pre>
                </details>
              </div>
            </article>
          ))
        )}
      </section>
    </div>
  );
}
