import { useRef, useState } from "react";
import { ArrowRight, GitCompareArrows, Play } from "lucide-react";
import { apiRequest, errorMessage, idempotencyKey } from "../../api/client";
import type { AdoptionPreview, Revision } from "../../api/models";
import { useNotices } from "../../app/notices";
import { Badge, Button, InlineNote } from "../../ui/primitives";

export function AdoptionPanel({
  targetId,
  candidateId,
  disabled,
  onOpenTable,
  onAdopted,
}: {
  targetId: string;
  candidateId: string;
  disabled: boolean;
  onOpenTable: (id: string) => void;
  onAdopted: (targetId: string, revision: Revision) => void;
}) {
  const { notify } = useNotices();
  const [snapshot, setSnapshot] = useState<AdoptionPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [same, setSame] = useState(false);
  const [replacement, setReplacement] = useState(false);
  const [offset, setOffset] = useState(0);
  const retry = useRef<{ key: string; body: Record<string, unknown> } | null>(
    null,
  );

  async function load(nextOffset = 0) {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      const value = await apiRequest<AdoptionPreview>(
        `/api/v1/tables/${targetId}/adoption-preview?candidate_table_id=${encodeURIComponent(
          candidateId,
        )}&offset=${nextOffset}`,
      );
      setSnapshot(value);
      setOffset(nextOffset);
      setSame(false);
      setReplacement(false);
      retry.current = null;
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function adopt() {
    if (!snapshot || !same || !replacement) return;
    retry.current ??= {
      key: idempotencyKey(),
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
    setBusy(true);
    setError("");
    try {
      const revision = await apiRequest<Revision>(
        `/api/v1/tables/${targetId}/adopt`,
        {
          method: "POST",
          headers: { "Idempotency-Key": retry.current.key },
          body: JSON.stringify(retry.current.body),
        },
      );
      retry.current = null;
      setSnapshot(null);
      setSame(false);
      setReplacement(false);
      setStatus(
        "Adopted as a new draft. Every value, source relation and subtotal needs review again.",
      );
      notify("Candidate adopted as a new draft.", "success");
      onAdopted(targetId, revision);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const changes = Array.isArray(snapshot?.difference.changed_cells)
    ? snapshot!.difference.changed_cells
    : [];

  if (!snapshot)
    return (
      <div className="adoption-cta">
        <Button
          size="sm"
          variant="secondary"
          icon={<GitCompareArrows size={14} />}
          disabled={disabled || busy}
          loading={busy}
          onClick={() => void load()}
        >
          Compare replacement
        </Button>
        {status && <p className="tiny muted">{status}</p>}
        {error && (
          <p className="error tiny" role="alert">
            {error}
          </p>
        )}
      </div>
    );

  return (
    <section className="adoption-panel" aria-label="Adopt extraction candidate">
      <div className="row spread">
        <h4>Replace with this original extraction?</h4>
        <Badge tone="info">Positional comparison</Badge>
      </div>
      <p className="small muted">
        Current {snapshot.current_shape.join(" × ")} · candidate{" "}
        {snapshot.candidate_shape.join(" × ")} · source page{" "}
        {snapshot.candidate_reference.page}. Candidate-table edits are excluded;
        the original extracted result is pinned.
      </p>
      <p className="small">
        {snapshot.changed_cell_count} changed cell position
        {snapshot.changed_cell_count === 1 ? "" : "s"}. Position differences do
        not establish which value is correct.
      </p>

      {!!changes.length && (
        <div className="adoption-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Position</th>
                <th>Current saved</th>
                <th>Candidate</th>
              </tr>
            </thead>
            <tbody>
              {changes.slice(0, 20).map((value, index) => {
                const item =
                  value && typeof value === "object"
                    ? (value as Record<string, unknown>)
                    : {};
                return (
                  <tr key={index}>
                    <th scope="row" className="mono">
                      {Number(item.row) + 1}:{Number(item.col) + 1}
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

      <div className="row">
        <Button
          size="sm"
          variant="ghost"
          disabled={busy || offset === 0}
          onClick={() =>
            void load(Math.max(0, offset - (snapshot.next_offset || 0)))
          }
        >
          Previous
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={busy || !snapshot.has_more}
          onClick={() => void load(snapshot.next_offset)}
        >
          Next differences
        </Button>
        <Button size="sm" variant="ghost" onClick={() => void load(offset)}>
          Refresh
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={() => {
            setSnapshot(null);
            setError("");
            setStatus("");
          }}
        >
          Keep current
        </Button>
      </div>

      <p className="small">
        Old corrections stay in History; they are not merged into the candidate.
        All cell reviews, issue decisions and approval reset.{" "}
        {snapshot.current_context_confirmations} context confirmations and{" "}
        {snapshot.current_subtotal_declarations} subtotal declarations will be
        cleared. Previous revisions and exports remain.
      </p>

      <fieldset className="adoption-confirm" disabled={busy || disabled}>
        <legend>Confirm replacement</legend>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={same}
            onChange={(event) => setSame(event.target.checked)}
          />
          I compared the source areas and these represent the same table
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={replacement}
            onChange={(event) => setReplacement(event.target.checked)}
          />
          Use the complete candidate; keep my old corrections only in History
        </label>
        <Button
          size="sm"
          variant="ink"
          icon={<Play size={13} />}
          disabled={!same || !replacement || busy}
          onClick={() => void adopt()}
        >
          Adopt as new draft
        </Button>
      </fieldset>

      <div className="row">
        <button
          type="button"
          className="link-button small"
          onClick={() => onOpenTable(targetId)}
        >
          Open earlier table
        </button>
        <ArrowRight size={12} aria-hidden="true" />
        <button
          type="button"
          className="link-button small"
          onClick={() => onOpenTable(candidateId)}
        >
          Open candidate table
        </button>
      </div>

      {error && (
        <InlineNote tone="danger">
          <p>{error}</p>
        </InlineNote>
      )}
    </section>
  );
}

function describe(value: unknown) {
  if (!value || typeof value !== "object") return "No cell";
  const cell = value as Record<string, unknown>;
  return `${String(cell.text || "blank")} · ${String(cell.row_span)} × ${String(
    cell.col_span,
  )} · ${String(cell.role)}`;
}
