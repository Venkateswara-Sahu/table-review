import { useEffect, useState } from "react";
import { ScanLine, Sparkles } from "lucide-react";
import { useCapabilities, useExtract } from "../../api/hooks";
import type {
  ExtractionBackend,
  ExtractionMode,
  PageBox,
  Task,
} from "../../api/models";
import { useNotices } from "../../app/notices";
import { Button, Field, InlineNote, Input, Select } from "../../ui/primitives";
import { Dialog, DialogContent } from "../../ui/dialog";

function parsePages(value: string, pageCount: number) {
  const pages = new Set<number>();
  for (const part of value.split(",")) {
    const token = part.trim();
    if (!token) continue;
    const range = /^(\d+)\s*[-–]\s*(\d+)$/.exec(token);
    if (range) {
      const start = Number(range[1]);
      const end = Number(range[2]);
      if (start > end) throw new Error("A page range must run low to high.");
      if (end - start + 1 > 20)
        throw new Error("A range can include at most 20 pages.");
      for (let page = start; page <= end; page++) pages.add(page);
    } else if (/^\d+$/.test(token)) {
      pages.add(Number(token));
    } else {
      throw new Error(`“${token}” is not a page number or range.`);
    }
  }
  const list = [...pages].sort((a, b) => a - b);
  if (!list.length) throw new Error("Choose at least one page.");
  if (list.length > 20) throw new Error("Extraction accepts at most 20 pages.");
  for (const page of list)
    if (page < 1 || page > pageCount)
      throw new Error(`Page ${page} is outside this report.`);
  return list;
}

export function ExtractDialog({
  open,
  onOpenChange,
  documentId,
  pageNo,
  pageCount,
  region,
  onStarted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string;
  pageNo: number;
  pageCount: number;
  region: PageBox | null;
  onStarted: (task: Task) => void;
}) {
  const capabilities = useCapabilities(open);
  const extract = useExtract(documentId);
  const { notify } = useNotices();
  const [pages, setPages] = useState(String(pageNo));
  const [backend, setBackend] = useState<ExtractionBackend>("auto");
  const [mode, setMode] = useState<ExtractionMode>("accurate");
  const [error, setError] = useState("");

  const available = capabilities.data?.available_backends;
  const doclingReady = available ? available.docling : true;
  const vlmReady = available ? available.vlm : true;
  const defaultBackend = capabilities.data?.default_extraction_backend;

  useEffect(() => {
    if (open) {
      setPages(region ? String(region.page) : String(pageNo));
      setError("");
      if (defaultBackend) {
        setBackend(defaultBackend);
      }
    }
  }, [open, pageNo, region, defaultBackend]);

  async function submit() {
    setError("");
    let selected: number[];
    try {
      selected = parsePages(pages, pageCount);
    } catch (caught) {
      setError((caught as Error).message);
      return;
    }
    if (region && (selected.length !== 1 || selected[0] !== region.page)) {
      setError("The selected region belongs to a different page.");
      return;
    }
    try {
      const task = await extract.mutateAsync({
        pages: selected,
        region,
        backend,
        mode,
      });
      notify(
        `Extraction queued for ${selected.length === 1 ? `page ${selected[0]}` : `${selected.length} pages`}.`,
        "info",
      );
      onStarted(task);
      onOpenChange(false);
    } catch (caught) {
      setError((caught as Error).message);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={region ? "Extract selected region" : "Extract tables"}
        description={
          region
            ? "OCR reads the drawn area as a table. The original PDF stays untouched."
            : "Choose up to 20 pages. Digital pages extract in milliseconds via native mode."
        }
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              icon={<ScanLine size={15} />}
              loading={extract.isPending}
              onClick={() => void submit()}
            >
              Start extraction
            </Button>
          </>
        }
      >
        <div className="extract-form">
          {region ? (
            <InlineNote tone="info" icon={<Sparkles size={15} />}>
              <p>
                Region on page {region.page} ·{" "}
                <span className="mono">
                  {Math.round(region.right - region.left)} ×{" "}
                  {Math.round(region.bottom - region.top)}
                </span>{" "}
                page units. Region extraction uses OCR and returns a draft for
                review.
              </p>
            </InlineNote>
          ) : (
            <Field
              label="Pages"
              hint="Numbers or ranges, for example 42, 43-48. Maximum 20 pages."
              error={error || undefined}
            >
              <Input
                autoFocus
                value={pages}
                onChange={(event) => setPages(event.target.value)}
                placeholder="e.g. 42, 43-45"
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    if (event.nativeEvent.isComposing || event.keyCode === 229)
                      return;
                    void submit();
                  }
                }}
              />
            </Field>
          )}

          {!region && (
            <div className="page-quick-picks">
              {[...new Set([pageNo, pageNo + 1, pageNo + 2])]
                .filter((page) => page >= 1 && page <= pageCount)
                .map((page) => (
                  <button
                    key={page}
                    type="button"
                    className={
                      parsePagesSafe(pages, pageCount).includes(page)
                        ? "quick-pick active"
                        : "quick-pick"
                    }
                    onClick={() => {
                      const current = parsePagesSafe(pages, pageCount);
                      const next = current.includes(page)
                        ? current.filter((value) => value !== page)
                        : [...current, page].slice(0, 20);
                      setPages(next.sort((a, b) => a - b).join(", "));
                      setError("");
                    }}
                  >
                    Page {page}
                  </button>
                ))}
            </div>
          )}

          <div className="extract-options-grid">
            <Field label="Extraction engine">
              <Select
                value={backend}
                onChange={(event) =>
                  setBackend(event.target.value as ExtractionBackend)
                }
              >
                <option value="auto">
                  {doclingReady
                    ? "Auto (Fast native + Docling fallback)"
                    : "Auto (Fast native — digital PDFs)"}
                </option>
                <option value="native_fast">
                  Fast Native (Digital PDF · ~15ms/page)
                </option>
                <option value="docling" disabled={!doclingReady}>
                  {doclingReady
                    ? "Docling (OCR & complex layout)"
                    : "Docling + OCR (models not preloaded)"}
                </option>
                <option value="vlm" disabled={!vlmReady}>
                  {vlmReady
                    ? "Vision LLM (Configured endpoint)"
                    : "Vision LLM (not configured)"}
                </option>
              </Select>
            </Field>
            {(backend === "auto" || backend === "docling") && doclingReady && (
              <Field label="Table structure mode">
                <Select
                  value={mode}
                  onChange={(event) =>
                    setMode(event.target.value as ExtractionMode)
                  }
                >
                  <option value="accurate">Accurate (Multi-row headers)</option>
                  <option value="fast">Fast (Lower latency)</option>
                </Select>
              </Field>
            )}
          </div>
          {available && (!doclingReady || !vlmReady) && (
            <p className="tiny muted">
              {!doclingReady
                ? "Fast Native is ready for digital PDFs. Run with --with-docling to enable OCR for scanned pages."
                : "Fast Native and Docling OCR are ready."}
            </p>
          )}

          {region && error && (
            <InlineNote tone="danger">
              <p>{error}</p>
            </InlineNote>
          )}

          <p className="tiny muted">
            Existing tables and review work are kept. New tables start
            unchecked. A processing run never blocks reading the source.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function parsePagesSafe(value: string, pageCount: number) {
  try {
    return parsePages(value, pageCount);
  } catch {
    return [];
  }
}
