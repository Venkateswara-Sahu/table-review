import { useEffect, useState } from "react";
import { Grid2X2, ScanLine } from "lucide-react";
import { useCreateManualTable } from "../../api/hooks";
import type { PageBox, Table } from "../../api/models";
import { useNotices } from "../../app/notices";
import { Button, Field, InlineNote, Input } from "../../ui/primitives";
import { Dialog, DialogContent } from "../../ui/dialog";

export function ManualTableDialog({
  open,
  onOpenChange,
  documentId,
  region,
  onCreated,
  onRequestRegion,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string;
  region: PageBox | null;
  onCreated: (table: Table) => void;
  onRequestRegion: () => void;
}) {
  const create = useCreateManualTable(documentId);
  const { notify } = useNotices();
  const [rows, setRows] = useState("12");
  const [columns, setColumns] = useState("4");
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setRows("12");
      setColumns("4");
      setError("");
    }
  }, [open]);

  async function submit() {
    if (!region) return;
    const rowCount = Number(rows);
    const columnCount = Number(columns);
    if (
      !Number.isInteger(rowCount) ||
      rowCount < 1 ||
      rowCount > 500 ||
      !Number.isInteger(columnCount) ||
      columnCount < 1 ||
      columnCount > 60
    ) {
      setError("Use 1–500 rows and 1–60 columns.");
      return;
    }
    try {
      const table = await create.mutateAsync({
        region,
        rows: rowCount,
        columns: columnCount,
      });
      notify(
        "Blank manual table created. Enter values from the source.",
        "success",
      );
      onCreated(table);
      onOpenChange(false);
    } catch (caught) {
      setError((caught as Error).message);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Create a manual table"
        description="Build a blank grid for a source area the parser cannot read. Every value is entered by hand."
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              icon={<Grid2X2 size={15} />}
              disabled={!region}
              loading={create.isPending}
              onClick={() => void submit()}
            >
              Create grid
            </Button>
          </>
        }
      >
        {region ? (
          <div className="manual-form">
            <InlineNote tone="info" icon={<ScanLine size={15} />}>
              <p>
                Source region on page {region.page}. The grid is anchored to
                this rectangle; open the table to compare with the page.
              </p>
            </InlineNote>
            <div className="form-grid form-grid-2">
              <Field label="Rows" error={error || undefined}>
                <Input
                  type="number"
                  min={1}
                  max={500}
                  value={rows}
                  onChange={(event) => setRows(event.target.value)}
                />
              </Field>
              <Field label="Columns">
                <Input
                  type="number"
                  min={1}
                  max={60}
                  value={columns}
                  onChange={(event) => setColumns(event.target.value)}
                />
              </Field>
            </div>
            <p className="tiny muted">
              You can insert or delete rows and columns later. Structure changes
              always start a fresh review.
            </p>
          </div>
        ) : (
          <div className="manual-empty">
            <p>
              Draw a rectangle around the table on the source page first. The
              manual grid needs a source region to anchor its cells.
            </p>
            <Button
              variant="primary"
              icon={<ScanLine size={15} />}
              onClick={() => {
                onOpenChange(false);
                onRequestRegion();
              }}
            >
              Select a region
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
