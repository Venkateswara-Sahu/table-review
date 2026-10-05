import { useEffect, useRef, useState, type PointerEvent } from "react";
import { apiBlob, errorMessage } from "../../api/client";
import type { PageInfo, SourceReference, PageBox } from "../../api/models";
import { sourceBoxToPixels } from "./coordinates";

export function SourcePage({
  documentId,
  page,
  reference,
  tableBox,
  onRegion,
}: {
  documentId: string;
  page: PageInfo;
  reference?: SourceReference | null;
  tableBox?: PageBox | null;
  onRegion?: (box: PageBox) => void;
}) {
  const [zoom, setZoom] = useState(1);
  const [fullPage, setFullPage] = useState(false);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [selecting, setSelecting] = useState(false);
  const [selection, setSelection] = useState<PageBox | null>(null);
  const anchor = useRef<{ x: number; y: number } | null>(null);
  const canvas = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true,
      objectUrl = "";
    const controller = new AbortController();
    setUrl("");
    setError("");
    apiBlob(
      `/api/v1/documents/${documentId}/pages/${page.page_no}/image?scale=1.5`,
      controller.signal,
    )
      .then((blob) => {
        if (active) {
          objectUrl = URL.createObjectURL(blob);
          setUrl(objectUrl);
        }
      })
      .catch((error) => {
        if (
          active &&
          !(error instanceof DOMException && error.name === "AbortError")
        )
          setError(errorMessage(error));
      });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, page.page_no]);
  useEffect(() => {
    setSelection(null);
    setFullPage(false);
    setSelecting(false);
  }, [documentId, page.page_no]);
  const crop =
    !fullPage && !selecting && tableBox?.page === page.page_no
      ? tableBox
      : null;
  const width = page.width * zoom,
    height = page.height * zoom;
  const box =
    reference?.page === page.page_no && reference.box
      ? sourceBoxToPixels(reference.box, { width, height })
      : null;
  function position(event: PointerEvent) {
    const rect = canvas.current!.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(page.width, (event.clientX - rect.left) / zoom)),
      y: Math.max(0, Math.min(page.height, (event.clientY - rect.top) / zoom)),
    };
  }
  function begin(event: PointerEvent<HTMLDivElement>) {
    if (!selecting) return;
    anchor.current = position(event);
    event.currentTarget.setPointerCapture(event.pointerId);
    setSelection(null);
  }
  function finish(event: PointerEvent<HTMLDivElement>) {
    if (!selecting || !anchor.current) return;
    const end = position(event),
      start = anchor.current;
    anchor.current = null;
    const region: PageBox = {
      page: page.page_no,
      left: Math.min(start.x, end.x),
      top: Math.min(start.y, end.y),
      right: Math.max(start.x, end.x),
      bottom: Math.max(start.y, end.y),
      page_width: page.width,
      page_height: page.height,
    };
    if (region.right - region.left > 2 && region.bottom - region.top > 2) {
      setSelection(region);
      onRegion?.(region);
    }
  }
  const selected = selection && sourceBoxToPixels(selection, { width, height });
  return (
    <section className="source-panel" aria-label="Report source">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">ORIGINAL SOURCE</p>
          <h2>Page {page.page_no}</h2>
        </div>
        <div className="toolbar">
          <button
            aria-label="Zoom out"
            disabled={zoom <= 0.5}
            onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}
          >
            −
          </button>
          <span aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button
            aria-label="Zoom in"
            disabled={zoom >= 3}
            onClick={() => setZoom((value) => Math.min(3, value + 0.25))}
          >
            +
          </button>
          <button onClick={() => setFullPage((value) => !value)}>
            {fullPage ? "Table crop" : "Full page"}
          </button>
          {onRegion && (
            <button
              aria-pressed={selecting}
              onClick={() => {
                setSelecting((value) => !value);
                setFullPage(true);
              }}
            >
              Select region
            </button>
          )}
        </div>
      </div>
      {reference && (
        <div className="source-caption">
          <span>Source: {reference.precision} reference</span>
          {reference.precision !== "cell" && (
            <span className="muted">Exact cell location is unavailable</span>
          )}
        </div>
      )}
      {selecting && (
        <p role="status">
          Drag a rectangle around the table on the original page.
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="source-scroll">
        <div
          className="source-frame"
          style={{
            width: crop ? (crop.right - crop.left) * zoom : width,
            height: crop ? (crop.bottom - crop.top) * zoom : height,
          }}
        >
          <div
            ref={canvas}
            className={`source-canvas ${selecting ? "selecting" : ""}`}
            onPointerDown={begin}
            onPointerUp={finish}
            style={{
              width,
              height,
              left: crop ? -crop.left * zoom : 0,
              top: crop ? -crop.top * zoom : 0,
            }}
          >
            {url ? (
              <img
                data-testid="source-image"
                src={url}
                alt={`Original report page ${page.page_no}`}
                width={width}
                height={height}
                draggable={false}
              />
            ) : (
              <p role="status">Loading source…</p>
            )}
            {box && (
              <div
                data-testid="source-highlight"
                data-page={page.page_no}
                className="source-highlight"
                style={box}
              />
            )}
            {selected && <div className="region-highlight" style={selected} />}
          </div>
        </div>
      </div>
    </section>
  );
}
