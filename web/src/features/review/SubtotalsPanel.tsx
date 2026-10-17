import { useEffect, useState } from "react";
import { apiRequest, errorMessage } from "../../api/client";
import type { Cell, EditOperation, TableContent } from "../../api/models";
import type { components } from "../../api/types";
import { cellAddress } from "./cellContext";

type Group = components["schemas"]["SubtotalGroup"];
type Results = components["schemas"]["SubtotalResults"];
type Term = components["schemas"]["SubtotalTerm"];

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
  const [terms, setTerms] = useState<Term[]>([]);
  const [tolerance, setTolerance] = useState("0");
  const [confirmed, setConfirmed] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [setupOpen, setSetupOpen] = useState(!content.subtotal_groups.length);
  const [results, setResults] = useState<{
    key: string;
    value: Results;
  } | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const key = `${revisionId}/${refreshKey}`;
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setError("");
    apiRequest<Results>(
      `/api/v1/tables/${tableId}/revisions/${revisionId}/subtotals`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setResults({ key, value });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => controller.abort();
  }, [active, tableId, revisionId, key, attempt]);
  function change() {
    setDirty(true);
    setConfirmed(false);
  }
  function label(identity: string) {
    const cell = content.cells.find((cell) => cell.id === identity);
    return cell
      ? `${cellAddress(cell)}: ${cell.text || "blank"}`
      : "Missing cell";
  }
  function load(group?: Group) {
    if (dirty && !window.confirm("Discard unsaved subtotal setup?")) return;
    setId(group?.id ?? crypto.randomUUID());
    setName(group?.name ?? "");
    setTotal(group?.total_cell_id ?? "");
    setTerms(group?.terms ?? []);
    setTolerance(group?.tolerance ?? "0");
    setConfirmed(false);
    setDirty(!!group);
    setSetupOpen(!!group || !content.subtotal_groups.length);
  }
  const visible = results?.key === key ? results.value : null;
  return (
    <section className="subtotal-panel" aria-label="Declared subtotal setup">
      <h3>Declared subtotals</h3>
      <p>
        Check a signed sum of selected currency amounts. Confirm period,
        currency and scale for every participating cell in Context first. No
        scale conversion, inferred formula or financial approval.
      </p>
      <details
        open={setupOpen}
        onToggle={(event) => setSetupOpen(event.currentTarget.open)}
      >
        <summary>Set up or revise a subtotal</summary>
        <button disabled={disabled} onClick={onContext}>
          Open source context for current selection
        </button>
        <fieldset disabled={disabled}>
          <legend>
            {content.subtotal_groups.some((group) => group.id === id)
              ? "Review saved relation"
              : "New relation"}
          </legend>
          <label>
            Subtotal name
            <input
              maxLength={80}
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                change();
              }}
            />
          </label>
          <p>
            Total:{" "}
            {total
              ? label(total)
              : "Select its cell in the grid, then use the button below."}
          </p>
          <button
            disabled={!current}
            onClick={() => {
              if (current) {
                setTotal(current.id);
                setTerms((value) =>
                  value.filter((term) => term.cell_id !== current.id),
                );
                change();
              }
            }}
          >
            Use current cell as total
          </button>
          <p>
            Inputs: select numeric cells in the grid. Shift + click selects a
            range. Each physical cell is counted once.
          </p>
          <button
            disabled={!selected.length}
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
            Add selected cells as inputs
          </button>
          {!terms.length && <p>No inputs selected.</p>}
          <ul className="subtotal-inputs">
            {terms.map((term) => (
              <li key={term.cell_id}>
                <label>
                  Sign for {label(term.cell_id)}
                  <select
                    value={term.sign}
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
                    <option value="1">Add signed value (+)</option>
                    <option value="-1">Subtract signed value (−)</option>
                  </select>
                </label>
                <button onClick={() => onSelect(term.cell_id)}>
                  View source: {label(term.cell_id)}
                </button>
                <button
                  onClick={() => {
                    setTerms((value) =>
                      value.filter((item) => item.cell_id !== term.cell_id),
                    );
                    change();
                  }}
                >
                  Remove input: {label(term.cell_id)}
                </button>
              </li>
            ))}
          </ul>
          <label>
            Allowed absolute difference
            <input
              inputMode="decimal"
              value={tolerance}
              onChange={(event) => {
                setTolerance(event.target.value);
                change();
              }}
            />
          </label>
          <p className="fine-print">
            In the same displayed units. Zero means exact equality. No automatic
            rounding allowance. A minus sign or parentheses in the source
            remains part of the signed value.
          </p>
          <label className="subtotal-confirm">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => {
                setConfirmed(event.target.checked);
                setDirty(true);
              }}
            />
            I checked the source scope and selected relation
          </label>
          <p className="fine-print">
            These are currency amounts for the same period and scale; the
            selected inputs and signs make up this total. Check any unit
            exceptions in the source.
          </p>
          <div className="edit-toolbar">
            <button
              disabled={
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
                  name,
                  total_cell_id: total,
                  terms,
                  tolerance,
                  confirmed_scope: true,
                })
              }
            >
              Confirm and save subtotal
            </button>
            <button onClick={() => load()}>Discard subtotal setup</button>
          </div>
        </fieldset>
      </details>
      <section aria-label="Saved subtotal checks">
        <h3>Saved checks</h3>
        {error ? (
          <>
            <p role="alert">{error}</p>
            <button onClick={() => setAttempt((value) => value + 1)}>
              Retry subtotal results
            </button>
          </>
        ) : !visible ? (
          <p role="status">Loading subtotal results…</p>
        ) : !visible.items.length ? (
          <p>
            No declared subtotal checks. Source-transcription checks remain in
            Checks.
          </p>
        ) : (
          visible.items.map((result) => (
            <article className="subtotal-result" key={result.group.id}>
              <h4>{result.group.name}</h4>
              <strong>
                {result.state === "match"
                  ? "Within declared tolerance"
                  : result.state === "difference"
                    ? "Declared sum differs"
                    : result.state === "needs_confirmation"
                      ? "Confirm again"
                      : "Unavailable"}
              </strong>
              <p>{result.reason}</p>
              <p>
                Total {label(result.group.total_cell_id)} ={" "}
                {result.group.terms
                  .map(
                    (term) =>
                      `${term.sign === 1 ? "+" : "−"} ${label(term.cell_id)}`,
                  )
                  .join(" ")}
              </p>
              {result.input_sum != null && (
                <dl>
                  <dt>Input sum</dt>
                  <dd>{result.input_sum}</dd>
                  <dt>Reported total</dt>
                  <dd>{result.reported_total}</dd>
                  <dt>Difference (reported − sum)</dt>
                  <dd>{result.difference}</dd>
                  <dt>Allowed difference</dt>
                  <dd>{result.group.tolerance}</dd>
                </dl>
              )}
              <p>
                Confirmed scope:{" "}
                {Object.entries(result.scope ?? {})
                  .map(([kind, value]) => `${kind}: ${value}`)
                  .join(" · ") || "Unavailable"}
              </p>
              <button disabled={disabled} onClick={() => load(result.group)}>
                Review this relation
              </button>
              <details>
                <summary>Recorded input evidence</summary>
                <pre>{JSON.stringify(result, null, 2)}</pre>
              </details>
              <details>
                <summary>Remove check</summary>
                <p>
                  History and earlier exports retain this declaration. Approval
                  must be renewed.
                </p>
                <button
                  disabled={disabled || dirty}
                  onClick={() =>
                    onSave({
                      kind: "remove_subtotal",
                      group_id: result.group.id,
                    })
                  }
                >
                  Remove declaration
                </button>
              </details>
            </article>
          ))
        )}
      </section>
    </section>
  );
}
