import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent,
  type RefObject,
} from "react";
import { errorMessage } from "../../api/client";
import type { PdfPreviews } from "./PdfPreviews";
import type {
  Cell,
  PageInfo,
  SourceReference,
  PageBox,
} from "../../api/models";
import { sourceBoxToPixels } from "./coordinates";
import { cellAddress } from "./cellContext";

type Jump = { page: number; sequence: number };
type Draw = (
  event: PointerEvent<HTMLDivElement>,
  info: PageInfo,
  scale: number,
  phase: "start" | "move" | "end",
) => void;
export function SourceReader({
  documentId,
  previews,
  currentPage,
  page,
  pageCount,
  reference,
  cells,
  jump,
  onPage,
  onCell,
  onRegion,
  selection,
}: {
  documentId: string;
  previews: PdfPreviews;
  currentPage: number;
  page: PageInfo;
  pageCount: number;
  reference: SourceReference | null;
  cells: Cell[];
  jump: Jump;
  onPage: (page: number) => void;
  onCell: (id: string) => void;
  onRegion: (box: PageBox | null) => void;
  selection: PageBox | null;
}) {
  const reader = useRef<HTMLDivElement>(null);
  const scrollFrame = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (scrollFrame.current !== null)
        cancelAnimationFrame(scrollFrame.current);
    },
    [],
  );
  function trackPage() {
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
    scrollFrame.current = requestAnimationFrame(() => {
      scrollFrame.current = null;
      const root = reader.current;
      if (!root || !root.clientHeight) return;
      const sheets = root.querySelectorAll<HTMLElement>("[data-source-page]");
      const line = root.getBoundingClientRect().top + root.clientHeight * 0.4;
      let low = 0,
        high = sheets.length - 1;
      while (low <= high) {
        const middle = Math.floor((low + high) / 2);
        const box = sheets[middle].getBoundingClientRect();
        if (box.bottom <= line) low = middle + 1;
        else if (box.top > line) high = middle - 1;
        else {
          onPage(Number(sheets[middle].dataset.sourcePage));
          return;
        }
      }
    });
  }
  const activePage = useRef(currentPage);
  activePage.current = currentPage;
  const resizeAnchor = useRef<{ page: number; ratio: number } | null>(null);
  const [size, setSize] = useState({ width: 600, height: 600 });
  const [sizing, setSizing] = useState("width"),
    [zoom, setZoom] = useState(1);
  const [selecting, setSelecting] = useState(false);
  const [preview, setPreview] = useState<PageBox | null>(null);
  const anchor = useRef<{ x: number; y: number; page: PageInfo } | null>(null);
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
        `[data-source-page="${activePage.current}"]`,
      );
      if (frame) {
        const box = frame.getBoundingClientRect();
        resizeAnchor.current = {
          page: activePage.current,
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
  }, []);
  useLayoutEffect(() => {
    const element = reader.current,
      anchor = resizeAnchor.current;
    if (!element || !anchor) return;
    const frame = element.querySelector<HTMLElement>(
      `[data-source-page="${anchor.page}"]`,
    );
    if (frame) {
      const box = frame.getBoundingClientRect();
      element.scrollTop +=
        box.top -
        element.getBoundingClientRect().top +
        anchor.ratio * box.height;
    }
    resizeAnchor.current = null;
  }, [size.width, size.height]);
  useLayoutEffect(() => {
    const element = reader.current,
      target = element?.querySelector<HTMLElement>(
        `[data-source-page="${jump.page}"]`,
      );
    if (element && target)
      element.scrollTop +=
        target.getBoundingClientRect().top -
        element.getBoundingClientRect().top -
        12;
  }, [jump]);
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      const element = reader.current,
        target = element?.querySelector<HTMLElement>(
          '[data-testid="source-highlight"]',
        );
      if (!element || !target) return;
      const source = element.getBoundingClientRect(),
        box = target.getBoundingClientRect();
      if (box.top < source.top + 20 || box.bottom > source.bottom - 20)
        element.scrollTop += box.top - source.top - 80;
    });
    return () => cancelAnimationFrame(id);
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
        ? Math.max(0.15, Math.min(width, (size.height - 55) / info.height))
        : zoom;
  }
  const currentScale = scaleFor(page);
  return (
    <section className="source-panel" aria-label="Report source">
      <div className="panel-heading">
        <h2>Original document</h2>
        <div className="toolbar">
          <button
            aria-label="Zoom out"
            disabled={currentScale <= 0.25}
            onClick={() => {
              setZoom(Math.max(0.2, currentScale - 0.15));
              setSizing("custom");
            }}
          >
            −
          </button>
          <span>{Math.round(currentScale * 100)}%</span>
          <button
            aria-label="Zoom in"
            disabled={currentScale >= 3}
            onClick={() => {
              setZoom(Math.min(3, currentScale + 0.15));
              setSizing("custom");
            }}
          >
            +
          </button>
          <select
            aria-label="Page sizing"
            value={sizing}
            onChange={(event) => setSizing(event.target.value)}
          >
            <option value="width">Fit width</option>
            <option value="page">Fit page</option>
            <option value="custom">Custom zoom</option>
          </select>
          <button onClick={() => setSizing("page")}>Full page</button>
        </div>
      </div>
      <div className="source-caption">
        <span>
          {reference
            ? `Source: ${reference.precision} reference`
            : "Scroll to browse report pages"}
        </span>
        <button
          aria-pressed={selecting}
          onClick={() => {
            if (selecting) cancel();
            else {
              setSelecting(true);
              onRegion(null);
            }
          }}
        >
          Select region
        </button>
      </div>
      {reference && reference.precision !== "cell" && (
        <div className="precision-note">Exact cell location is unavailable</div>
      )}
      {selecting && (
        <div className="draw-help" role="status">
          Drag around a table. Escape cancels.
          <button onClick={cancel}>Cancel selection</button>
        </div>
      )}
      {selection && (
        <div className="draw-help">
          Area selected on page {selection.page}
          <button onClick={cancel}>Clear selected region</button>
        </div>
      )}
      <div
        ref={reader}
        className="source-scroll continuous-reader"
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
              cells={cells}
              onCell={onCell}
              selecting={selecting}
              region={preview ?? selection}
              drawing={!!preview}
              onDraw={draw}
              onCancel={cancel}
              active={number === currentPage}
            />
          ),
        )}
      </div>
      <div className="source-footer">
        Page {currentPage} of {pageCount}
        <span>Scroll to read · Select a cell to compare</span>
      </div>
    </section>
  );
}

function PdfSheet({
  previews,
  number,
  fallback,
  reader,
  scaleFor,
  reference,
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
                  <button onClick={() => setRetry((value) => value + 1)}>
                    Retry page {number}
                  </button>
                </>
              ) : (
                `Loading page ${number}…`
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
                    aria-label={`Select source cell ${cellAddress(cell)}: ${cell.text || "blank"}`}
                    title={`${cellAddress(cell)} · ${cell.text || "blank"}`}
                    onClick={() => onCell(cell.id)}
                  />
                )),
            )}
          {selected && (
            <div
              data-testid="source-highlight"
              data-page={number}
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
