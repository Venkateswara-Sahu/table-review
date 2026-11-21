import { useEffect, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Layers } from "lucide-react";
import { apiRequest } from "../../api/client";
import { queryKeys, useAllTables, useStitchTables } from "../../api/hooks";
import type { Table, TableView } from "../../api/models";
import { useNotices } from "../../app/notices";
import { Dialog, DialogContent } from "../../ui/dialog";
import { Button, Field, InlineNote, Input } from "../../ui/primitives";

export function StitchTablesDialog({
  open,
  onOpenChange,
  documentId,
  onStitched,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string;
  onStitched: (table: Table, pageNo: number) => void;
}) {
  const { notify, notifyError } = useNotices();
  const tablesQuery = useAllTables(documentId, open);
  const stitch = useStitchTables(documentId);
  const [selectedOrder, setSelectedOrder] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [dropHeaders, setDropHeaders] = useState(true);

  const activeTables = tablesQuery.data?.items ?? [];
  const tableViews = useQueries({
    queries: activeTables.map((table) => ({
      queryKey: queryKeys.table(table.id),
      queryFn: () => apiRequest<TableView>(`/api/v1/tables/${table.id}`),
      enabled: open,
      staleTime: 30_000,
    })),
  });

  useEffect(() => {
    if (open) {
      setSelectedOrder([]);
      setTitle("");
      setDropHeaders(true);
    }
  }, [open]);

  const infoById = new Map(
    activeTables.map((table, index) => {
      const view = tableViews[index]?.data;
      const pageNo =
        view?.revision.content.source_reference?.page ??
        view?.revision.content.cells[0]?.reference.page ??
        1;
      const label = view?.revision.content.title || `Table ${index + 1}`;
      const shape = view
        ? `${view.revision.content.rows}×${view.revision.content.columns}`
        : "";
      return [table.id, { table, pageNo, label, shape }];
    }),
  );

  function toggle(id: string) {
    setSelectedOrder((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  }

  function move(id: string, delta: -1 | 1) {
    setSelectedOrder((prev) => {
      const idx = prev.indexOf(id);
      if (idx < 0) return prev;
      const target = idx + delta;
      if (target < 0 || target >= prev.length) return prev;
      const copy = [...prev];
      [copy[idx], copy[target]] = [copy[target], copy[idx]];
      return copy;
    });
  }

  async function submit() {
    if (selectedOrder.length < 2) return;
    try {
      const created = await stitch.mutateAsync({
        table_ids: selectedOrder,
        title: title.trim() || null,
        drop_repeated_headers: dropHeaders,
      });
      const firstInfo = infoById.get(selectedOrder[0]);
      notify("Multi-page table created", "success");
      onOpenChange(false);
      onStitched(created, firstInfo?.pageNo ?? 1);
    } catch (error) {
      notifyError(error);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Stitch multi-page tables"
        description="Merge continuation tables across pages into a single table while keeping every cell linked to its original PDF page."
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              icon={<Layers size={15} />}
              disabled={selectedOrder.length < 2 || stitch.isPending}
              loading={stitch.isPending}
              onClick={() => void submit()}
            >
              Stitch {selectedOrder.length} tables
            </Button>
          </>
        }
      >
        <div className="stitch-dialog">
          {tablesQuery.isPending ? (
            <p className="small muted" role="status">
              Loading report tables…
            </p>
          ) : activeTables.length < 2 ? (
            <InlineNote tone="info">
              <p>
                Extract at least two tables across pages first to stitch them
                together.
              </p>
            </InlineNote>
          ) : (
            <>
              <Field label="Combined table title (optional)">
                <Input
                  value={title}
                  maxLength={200}
                  placeholder="e.g. Consolidated Statements of Cash Flows"
                  onChange={(event) => setTitle(event.target.value)}
                />
              </Field>

              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={dropHeaders}
                  onChange={(event) => setDropHeaders(event.target.checked)}
                />
                Automatically drop repeated column headers on continuation pages
              </label>

              <div>
                <span className="field-label">
                  Select tables to combine (click in top-to-bottom order)
                </span>
                <ul className="multi-table-checklist">
                  {activeTables.map((table) => {
                    const info = infoById.get(table.id);
                    const orderIdx = selectedOrder.indexOf(table.id);
                    return (
                      <li key={table.id} className="stitch-item">
                        <label className="checkbox-row">
                          <input
                            type="checkbox"
                            checked={orderIdx >= 0}
                            onChange={() => toggle(table.id)}
                          />
                          {orderIdx >= 0 && (
                            <span className="stitch-order mono">
                              #{orderIdx + 1}
                            </span>
                          )}
                          <span className="multi-table-label">
                            {info?.label ?? "Table"}
                          </span>
                          <span className="tiny muted mono">
                            p.{info?.pageNo ?? "?"} {info?.shape ?? ""}
                          </span>
                        </label>
                        {orderIdx >= 0 && (
                          <div className="stitch-reorder">
                            <button
                              type="button"
                              className="icon-button"
                              aria-label="Move up"
                              disabled={orderIdx === 0}
                              onClick={() => move(table.id, -1)}
                            >
                              <ArrowUp size={12} />
                            </button>
                            <button
                              type="button"
                              className="icon-button"
                              aria-label="Move down"
                              disabled={orderIdx === selectedOrder.length - 1}
                              onClick={() => move(table.id, 1)}
                            >
                              <ArrowDown size={12} />
                            </button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
