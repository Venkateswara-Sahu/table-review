import { useRef, useState } from "react";
import { apiRequest, errorMessage } from "../../api/client";
import type { components } from "../../api/types";

type Preview = components["schemas"]["AdoptionPreview"];
type Adopt = components["schemas"]["AdoptInput"];
type Revision = components["schemas"]["RevisionView"];
export type AdoptionActions = {
  blocked: boolean;
  begin: () => ((tableId: string) => Promise<void>) | null;
};

function describe(value: unknown) {
  if (!value || typeof value !== "object") return "No cell";
  const cell = Object.fromEntries(Object.entries(value));
  return `${String(cell.text || "blank")} · ${String(cell.row_span)} × ${String(cell.col_span)} · ${String(cell.role)}`;
}

export function CandidateChoice({
  targetId,
  candidates,
  actions,
  onOpen,
}: {
  targetId: string;
  candidates: string[];
  actions: AdoptionActions;
  onOpen: (id: string) => void;
}) {
  const [chosen, setChosen] = useState("");
  return (
    <details className="adoption-review">
      <summary>
        Inspect possible replacement for earlier table {targetId.slice(0, 8)}
      </summary>
      <button onClick={() => onOpen(targetId)}>Open earlier table</button>
      <label>
        Candidate extraction
        <select
          value={chosen}
          onChange={(event) => setChosen(event.target.value)}
        >
          <option value="">Choose after comparing the source areas</option>
          {candidates.map((id, index) => (
            <option key={id} value={id}>
              Candidate {index + 1} · {id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      {chosen && (
        <>
          <button onClick={() => onOpen(chosen)}>Open candidate table</button>
          <AdoptionReview
            key={chosen}
            targetId={targetId}
            candidateId={chosen}
            actions={actions}
          />
        </>
      )}
    </details>
  );
}

export function AdoptionReview({
  targetId,
  candidateId,
  actions,
}: {
  targetId: string;
  candidateId: string;
  actions: AdoptionActions;
}) {
  const [snapshot, setSnapshot] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [same, setSame] = useState(false);
  const [replacement, setReplacement] = useState(false);
  const [status, setStatus] = useState("");
  const [previous, setPrevious] = useState<number[]>([]);
  const sequence = useRef(0);
  const retry = useRef<{ key: string; body: Adopt } | null>(null);
  async function load(offset = 0, trail: number[] = []) {
    setPrevious(trail);
    const request = ++sequence.current;
    setBusy(true);
    setError("");
    setStatus("");
    setSame(false);
    setReplacement(false);
    retry.current = null;
    try {
      const value = await apiRequest<Preview>(
        `/api/v1/tables/${targetId}/adoption-preview?candidate_table_id=${encodeURIComponent(candidateId)}&offset=${offset}`,
      );
      if (request === sequence.current) setSnapshot(value);
    } catch (error) {
      if (request === sequence.current) setError(errorMessage(error));
    } finally {
      if (request === sequence.current) setBusy(false);
    }
  }
  async function save() {
    if (!snapshot || !same || !replacement) return;
    const accept = actions.begin();
    if (!accept) return;
    setBusy(true);
    setError("");
    retry.current ??= {
      key: crypto.randomUUID(),
      body: {
        base_revision_id: snapshot.base_revision_id,
        candidate_table_id: snapshot.candidate_table_id,
        candidate_revision_id: snapshot.candidate_revision_id,
        target_visibility_version: snapshot.target_visibility_version,
        candidate_visibility_version: snapshot.candidate_visibility_version,
        confirmed_same_table: true,
        confirmed_replacement: true,
      },
    };
    try {
      await apiRequest<Revision>(`/api/v1/tables/${targetId}/adopt`, {
        method: "POST",
        headers: { "Idempotency-Key": retry.current.key },
        body: JSON.stringify(retry.current.body),
      });
      retry.current = null;
      setSnapshot(null);
      setSame(false);
      setReplacement(false);
      setStatus(
        "Adopted as a new draft. Review its values, source context and any subtotal relations again.",
      );
      try {
        await accept(targetId);
      } catch {
        setError(
          "Replacement was saved, but reopening failed. Use Open earlier table to load its new draft.",
        );
      }
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  const changes = snapshot?.difference.changed_cells;
  return (
    <section
      className="adoption-review"
      aria-label="Adopt extraction candidate"
    >
      <button disabled={busy || actions.blocked} onClick={() => void load()}>
        Review replacement
      </button>
      {actions.blocked && (
        <p>Save or discard your current work before replacing a table.</p>
      )}
      {status && <p role="status">{status}</p>}
      {error && (
        <p role="alert">
          {error} Retry the same adoption after a lost response, or refresh the
          comparison after a conflict.
        </p>
      )}
      {busy && (
        <p role="status">
          {snapshot ? "Saving replacement…" : "Loading current comparison…"}
        </p>
      )}
      {snapshot && (
        <>
          <h4>Replace the earlier table with this original extraction?</h4>
          <p>
            This comparison uses the current saved draft. Candidate-table edits
            are excluded; the original extracted result is pinned.
          </p>
          <p>
            Current: {snapshot.current_shape.join(" × ")} · Candidate:{" "}
            {snapshot.candidate_shape.join(" × ")} · Original page{" "}
            {snapshot.candidate_reference.page}.
          </p>
          <p>
            {snapshot.changed_cell_count} changed cell positions. Position
            differences do not establish which value is correct.
          </p>
          <details>
            <summary>Table structure and source differences</summary>
            <pre>
              {JSON.stringify(snapshot.difference.table_changes, null, 2)}
            </pre>
          </details>
          {Array.isArray(changes) && changes.length > 0 && (
            <div className="adoption-differences">
              <table>
                <thead>
                  <tr>
                    <th>Position</th>
                    <th>Current saved cell</th>
                    <th>Candidate cell</th>
                  </tr>
                </thead>
                <tbody>
                  {changes.map((value, index) => {
                    const item =
                      value && typeof value === "object"
                        ? Object.fromEntries(Object.entries(value))
                        : {};
                    return (
                      <tr key={index}>
                        <th>
                          Row {Number(item.row) + 1}, col {Number(item.col) + 1}
                        </th>
                        <td>{describe(item.before)}</td>
                        <td>{describe(item.after)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className="run-actions">
            <button
              disabled={busy || !previous.length}
              onClick={() =>
                void load(previous[previous.length - 1], previous.slice(0, -1))
              }
            >
              Previous differences
            </button>
            <button
              disabled={busy || !snapshot.has_more}
              onClick={() =>
                void load(snapshot.next_offset, [
                  ...previous,
                  snapshot.next_offset -
                    (Array.isArray(changes) ? changes.length : 0),
                ])
              }
            >
              Next differences
            </button>
            <button disabled={busy} onClick={() => void load()}>
              Refresh current comparison
            </button>
          </div>
          <p>
            Old corrections stay in History; they will not be merged into the
            candidate. All cell reviews, issue decisions and approval reset.{" "}
            {snapshot.current_context_confirmations} context confirmations and{" "}
            {snapshot.current_subtotal_declarations} subtotal declarations will
            be cleared from the new draft. Previous revisions and exports remain
            available.
          </p>
          <fieldset disabled={busy || actions.blocked}>
            <legend>Confirm the replacement</legend>
            <label>
              <input
                type="checkbox"
                checked={same}
                onChange={(event) => setSame(event.target.checked)}
              />
              I compared the source areas and these represent the same table
            </label>
            <label>
              <input
                type="checkbox"
                checked={replacement}
                onChange={(event) => setReplacement(event.target.checked)}
              />
              Use the complete candidate; retain my old corrections only in
              History
            </label>
            <div className="run-actions">
              <button
                disabled={!same || !replacement}
                onClick={() => void save()}
              >
                Adopt as new draft
              </button>
            </div>
          </fieldset>
          <button
            disabled={busy}
            onClick={() => {
              sequence.current++;
              setSnapshot(null);
              retry.current = null;
              setError("");
            }}
          >
            Keep current draft
          </button>
        </>
      )}
    </section>
  );
}
