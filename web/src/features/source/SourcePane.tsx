import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent,
  type RefObject,
} from "react";
import { Minus, Plus, ScanLine, X } from "lucide-react";
import { errorMessage } from "../../api/client";
import type {
  Cell,
  PageBox,
  PageInfo,
  SourceReference,
} from "../../api/models";
import { cellAddress } from "../table/address";
import { sourceBoxToPixels } from "./coordinates";
import type { PdfPreviews } from "./PdfPreviews";
import { Button, IconButton, Select } from "../../ui/primitives";

type Jump = { page: number; sequence: number };
type Draw = (
  event: PointerEvent<HTMLDivElement>,
  info: PageInfo,
  scale: number,
  phase: "start" | "move" | "end",
) => void;

export function SourcePane({
  documentId,
  previews,
  page,
  pageCount,
  reference,
  referenceLabel,
  cells,
  jump,
  selectRequest,
  onCell,
  onRegion,
  selection,
  onExtractRegion,
  onManualRegion,
}: {
  documentId: string;
  previews: PdfPreviews;
  page: PageInfo;
  pageCount: number;
  reference: SourceReference | null;
  referenceLabel?: string;
  cells: Cell[];
  jump: Jump;
  selectRequest: { sequence: number } | null;
  onCell: (id: string) => void;
  onRegion: (box: PageBox | null) => void;
  selection: PageBox | null;
  onExtractRegion: () => void;
  onManualRegion: () => void;
}) {
  const reader = useRef<HTMLDivElement>(null);
  const scrollFrame = useRef<number | null>(null);
  const [visiblePage, setVisiblePage] = useState(jump.page);
  const [size, setSize] = useState({ width: 600, height: 600 });
  const [sizing, setSizing] = useState("width");
  const [zoom, setZoom] = useState(1);
  const [selecting, setSelecting] = useState(false);
  const [preview, setPreview] = useState<PageBox | null>(null);
  const anchor = useRef<{ x: number; y: number; page: PageInfo } | null>(null);
  const resizeAnchor = useRef<{ page: number; ratio: number } | null>(null);

  useEffect(
    () => () => {
      if (scrollFrame.current !== null)
        cancelAnimationFrame(scrollFrame.current);
    },
    [],
  );

  useEffect(() => {
    if (!selectRequest) return;
    setSelecting(true);
    onRegion(null);
    setPreview(null);
    anchor.current = null;
    // A new request only arrives when the sequence changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectRequest?.sequence]);

  useEffect(() => {
    if (reader.current) {
      const frame = reader.current.querySelector<HTMLElement>(
        `[data-source-page="${jump.page}"]`,
      );
      if (frame) {
        reader.current.scrollTop +=
          frame.getBoundingClientRect().top -
          reader.current.getBoundingClientRect().top -
          8;
      }
      setVisiblePage(jump.page);
    }
  }, [jump]);

  function trackPage() {
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
    scrollFrame.current = requestAnimationFrame(() => {
      scrollFrame.current = null;
      const root = reader.current;
      if (!root || !root.clientHeight) return;
      const sheets = root.querySelectorAll<HTMLElement>("[data-source-page]");
      const line = root.getBoundingClientRect().top + root.clientHeight * 0.38;
      let low = 0,
        high = sheets.length - 1;
      while (low <= high) {
        const middle = Math.floor((low + high) / 2);
        const box = sheets[middle].getBoundingClientRect();
        if (box.bottom <= line) low = middle + 1;
        else if (box.top > line) high = middle - 1;
        else {
          setVisiblePage(Number(sheets[middle].dataset.sourcePage));
          return;
        }
      }
    });
  }

  const cancel = useCallback(() => {
    anchor.current = null;
    setPreview(null);
    setSelecting(false);
    onRegion(null);
  }, [onRegion]);

  useEffect(() => {
    const element = reader.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      if (!element.clientWidth || !element.clientHeight) return;
      const frame = element.querySelector<HTMLElement>(
        `[data-source-page="${visiblePage}"]`,
      );
      if (frame) {
        const box = frame.getBoundingClientRect();
        resizeAnchor.current = {
          page: visiblePage,
          ratio: Math.max(
            0,
            Math.min(
              1,
              (element.getBoundingClientRect().top - box.top) / box.height,
            ),
          ),
        };
      }
      setSize((value) =>
        value.width === element.clientWidth &&
        value.height === element.clientHeight
          ? value
          : { width: element.clientWidth, height: element.clientHeight },
      );
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [visiblePage]);

  useLayoutEffect(() => {
    const element = reader.current,
      saved = resizeAnchor.current;
    if (!element || !saved) return;
    const frame = element.querySelector<HTMLElement>(
      `[data-source-page="${saved.page}"]`,
    );
    if (frame) {
      const box = frame.getBoundingClientRect();
      element.scrollTop +=
        box.top -
        element.getBoundingClientRect().top +
        saved.ratio * box.height;
    }
    resizeAnchor.current = null;
  }, [size.width, size.height]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const element = reader.current;
      const target = element?.querySelector<HTMLElement>(
        '[data-testid="source-highlight"]',
      );
      if (!element || !target) return;
      const source = element.getBoundingClientRect(),
        box = target.getBoundingClientRect();
      if (box.top < source.top + 30 || box.bottom > source.bottom - 30)
        element.scrollTop += box.top - source.top - 90;
    });
    return () => cancelAnimationFrame(frame);
  }, [reference]);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && selecting) cancel();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [selecting, cancel]);

  function position(
    event: PointerEvent<HTMLDivElement>,
    info: PageInfo,
    scale: number,
  ) {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(info.width, (event.clientX - rect.left) / scale)),
      y: Math.max(0, Math.min(info.height, (event.clientY - rect.top) / scale)),
    };
  }

  const draw: Draw = (event, info, scale, phase) => {
    if (!selecting || (phase === "start" && event.button !== 0)) return;
    if (phase === "start") {
      anchor.current = { ...position(event, info, scale), page: info };
      event.currentTarget.setPointerCapture(event.pointerId);
      setPreview(null);
      onRegion(null);
      return;
    }
    const start = anchor.current;
    if (!start || start.page.page_no !== info.page_no) return;
    const end = position(event, info, scale);
    const region: PageBox = {
      page: info.page_no,
      left: Math.min(start.x, end.x),
      top: Math.min(start.y, end.y),
      right: Math.max(start.x, end.x),
      bottom: Math.max(start.y, end.y),
      page_width: info.width,
      page_height: info.height,
    };
    setPreview(region);
    if (phase === "end") {
      anchor.current = null;
      setPreview(null);
      if (region.right - region.left > 2 && region.bottom - region.top > 2) {
        onRegion(region);
        setSelecting(false);
      }
    }
  };

  function scaleFor(info: PageInfo) {
    const width = Math.max(180, size.width - 40) / info.width;
    return sizing === "width"
      ? Math.min(2, width)
      : sizing === "page"
        ? Math.max(0.15, Math.min(width, (size.height - 36) / info.height))
        : zoom;
  }

  const currentScale = scaleFor(page);
  return (
    <section className="source-pane" aria-label="Report source">
      <div className="source-head">
        <div className="source-head-title">
          <h2>Source</h2>
          <span className="source-page-count">
            Page {visiblePage} of {pageCount}
          </span>
        </div>
        <div className="source-toolbar">
          <IconButton
            label="Zoom out"
            disabled={currentScale <= 0.25}
            onClick={() => {
              setZoom(Math.max(0.2, currentScale - 0.15));
              setSizing("custom");
            }}
          >
            <Minus size={15} />
          </IconButton>
          <span className="zoom-label num">
            {Math.round(currentScale * 100)}%
          </span>
          <IconButton
            label="Zoom in"
            disabled={currentScale >= 3}
            onClick={() => {
              setZoom(Math.min(3, currentScale + 0.15));
              setSizing("custom");
            }}
          >
            <Plus size={15} />
          </IconButton>
          <Select
            className="select-compact"
            aria-label="Page sizing"
            value={sizing}
            onChange={(event) => setSizing(event.target.value)}
          >
            <option value="width">Fit width</option>
            <option value="page">Fit page</option>
            <option value="custom">Custom</option>
          </Select>
          <Button
            size="sm"
            variant={selecting ? "primary" : "secondary"}
            aria-pressed={selecting}
            icon={<ScanLine size={14} />}
            onClick={() => {
              if (selecting) cancel();
              else {
                setSelecting(true);
                onRegion(null);
              }
            }}
          >
            {selecting ? "Drawing…" : "Select region"}
          </Button>
        </div>
      </div>

      {reference ? (
        <div className="source-caption">
          <span>
            {referenceLabel ? (
              <>
                Source for <span className="mono">{referenceLabel}</span>
              </>
            ) : (
              "Source location"
            )}
          </span>
          <span className="muted">
            page {reference.page} · {reference.precision} reference
          </span>
        </div>
      ) : (
        <div className="source-caption">
          <span>Scroll to read</span>
          <span className="muted">Select a cell to compare</span>
        </div>
      )}

      {reference && reference.precision !== "cell" && (
        <div className="source-precision-note">
          Exact cell location is unavailable. Compare the table region with the
          page.
        </div>
      )}

      {selecting && (
        <div className="region-help">
          <span>Drag around a table. Press Escape to cancel.</span>
          <Button size="sm" variant="ghost" onClick={cancel}>
            Cancel
          </Button>
        </div>
      )}

      {selection && !selecting && (
        <div className="region-help region-help-active">
          <span>Region selected · page {selection.page}</span>
          <div className="region-help-actions">
            <Button size="sm" variant="primary" onClick={onExtractRegion}>
              Extract region
            </Button>
            <Button size="sm" variant="secondary" onClick={onManualRegion}>
              Manual table
            </Button>
            <Button
              size="sm"
              variant="ghost"
              aria-label="Clear selected region"
              onClick={cancel}
            >
              <X size={14} />
            </Button>
          </div>
        </div>
      )}

      <div
        ref={reader}
        className="source-scroll"
        role="region"
        aria-label="Scrollable report pages"
        tabIndex={0}
        onScroll={trackPage}
      >
        {Array.from({ length: pageCount }, (_, index) => index + 1).map(
          (number) => (
            <PdfSheet
              key={`${documentId}-${number}`}
              documentId={documentId}
              previews={previews}
              number={number}
              fallback={page}
              reader={reader}
              scaleFor={scaleFor}
              reference={reference}
              referenceLabel={referenceLabel}
              cells={cells}
              onCell={onCell}
              selecting={selecting}
              region={preview ?? selection}
              drawing={!!preview}
              onDraw={draw}
              onCancel={cancel}
              active={number === visiblePage}
            />
          ),
        )}
      </div>
      <div className="source-foot">
        <span>
          Page {visiblePage} of {pageCount}
        </span>
        <span>Scroll to read · Click a cell box to compare</span>
      </div>
    </section>
  );
}

function PdfSheet({
  documentId,
  previews,
  number,
  fallback,
  reader,
  scaleFor,
  reference,
  referenceLabel,
  cells,
  onCell,
  selecting,
  region,
  drawing,
  onDraw,
  onCancel,
  active,
}: {
  documentId: string;
  previews: PdfPreviews;
  number: number;
  fallback: PageInfo;
  reader: RefObject<HTMLDivElement | null>;
  scaleFor: (page: PageInfo) => number;
  reference: SourceReference | null;
  referenceLabel?: string;
  cells: Cell[];
  onCell: (id: string) => void;
  selecting: boolean;
  region: PageBox | null;
  drawing: boolean;
  active: boolean;
  onCancel: () => void;
  onDraw: Draw;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false),
    [info, setInfo] = useState<PageInfo | null>(null);
  const [url, setUrl] = useState(""),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0);

  useEffect(
    () =>
      previews.subscribe((evicted) => {
        if (evicted === number) setUrl("");
      }),
    [previews, number],
  );

  useEffect(() => {
    if (!frame.current) return;
    const observer = new IntersectionObserver(
      (entries) => setNear(entries[0].isIntersecting),
      { root: reader.current, rootMargin: "650px 0px" },
    );
    observer.observe(frame.current);
    return () => observer.disconnect();
  }, [reader]);

  useEffect(() => {
    if (!near) return;
    const controller = new AbortController();
    const release = active ? previews.retain(number) : () => {};
    setError("");
    async function load() {
      try {
        const image = await previews.preview(number, active, controller.signal);
        if (!controller.signal.aborted) {
          setInfo(image.metadata);
          setUrl(image.url);
        }
      } catch (error) {
        if (
          !controller.signal.aborted &&
          !(error instanceof DOMException && error.name === "AbortError")
        )
          setError(errorMessage(error));
      }
    }
    void load();
    return () => {
      controller.abort();
      release();
      setUrl("");
    };
  }, [near, previews, number, retry, active]);

  const metadata = info ?? { ...fallback, page_no: number };
  const scale = scaleFor(metadata),
    width = metadata.width * scale,
    height = metadata.height * scale;
  const selected =
    reference?.page === number && reference.box
      ? sourceBoxToPixels(reference.box, { width, height })
      : null;
  const area =
    region?.page === number
      ? sourceBoxToPixels(region, { width, height })
      : null;

  return (
    <div ref={frame} className="pdf-sheet" data-source-page={number}>
      <div className="page-marker">Page {number}</div>
      <div className="source-frame" style={{ width, height }}>
        <div
          className={`source-canvas ${selecting ? "selecting" : ""}`}
          style={{ width, height }}
          onPointerDown={(event) => onDraw(event, metadata, scale, "start")}
          onPointerMove={(event) => onDraw(event, metadata, scale, "move")}
          onPointerUp={(event) => onDraw(event, metadata, scale, "end")}
          onPointerCancel={onCancel}
        >
          {url ? (
            <img
              data-testid={active ? "source-image" : `source-image-${number}`}
              src={url}
              alt={`Original report page ${number}`}
              width={width}
              height={height}
              draggable={false}
            />
          ) : (
            <div className="page-loading" role={active ? "status" : undefined}>
              {error ? (
                <>
                  <p>{error}</p>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setRetry((value) => value + 1)}
                  >
                    Retry page {number}
                  </Button>
                </>
              ) : near ? (
                <span>Loading page {number}…</span>
              ) : (
                <span className="page-idle">Page {number}</span>
              )}
            </div>
          )}
          {!selecting &&
            cells.flatMap((cell) =>
              (cell.source_references.length
                ? cell.source_references
                : [cell.reference]
              )
                .filter(
                  (ref) =>
                    ref.page === number && ref.precision === "cell" && ref.box,
                )
                .map((ref, index) => (
                  <button
                    key={`${cell.id}-${index}`}
                    className="source-cell-target"
                    tabIndex={-1}
                    style={sourceBoxToPixels(ref.box!, { width, height })}
                    aria-label={`Select source cell ${cellAddress(cell)}: ${
                      cell.text || "blank"
                    }`}
                    title={`${cellAddress(cell)} · ${cell.text || "blank"}`}
                    onClick={() => onCell(cell.id)}
                  />
                )),
            )}
          {selected && (
            <div
              data-testid="source-highlight"
              data-page={number}
              data-label={referenceLabel}
              className={`source-highlight precision-${reference?.precision}`}
              style={selected}
            />
          )}
          {area && (
            <div
              data-testid={drawing ? "region-preview" : "region-selection"}
              className="region-highlight"
              style={area}
            />
          )}
        </div>
      </div>
    </div>
  );
}
