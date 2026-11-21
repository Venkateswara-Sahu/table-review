import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent,
  type RefObject,
} from "react";
import {
  ListTree,
  Minus,
  Plus,
  ScanLine,
  Search,
  Sparkles,
  Table2,
  X,
} from "lucide-react";
import { errorMessage } from "../../api/client";
import {
  useDocumentOutline,
  useDocumentSearch,
  useTableCandidates,
} from "../../api/hooks";
import type {
  Cell,
  OutlineItem,
  PageBox,
  PageInfo,
  SearchMatch,
  SourceReference,
} from "../../api/models";
import { cellAddress } from "../table/address";
import { sourceBoxToPixels } from "./coordinates";
import type { PdfPreviews } from "./PdfPreviews";
import { Button, IconButton, Input, Select } from "../../ui/primitives";

type Jump = { page: number; sequence: number };
type NavTab = "outline" | "search" | "candidates" | null;
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
  onVisiblePage,
  onRegion,
  selection,
  onExtractRegion,
  onManualRegion,
  onJumpPage,
  onQuickExtractPage,
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
  onVisiblePage: (page: number) => void;
  onRegion: (box: PageBox | null) => void;
  selection: PageBox | null;
  onExtractRegion: () => void;
  onManualRegion: () => void;
  onJumpPage?: (page: number) => void;
  onQuickExtractPage?: (page: number) => void;
}) {
  const reader = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const scrollFrame = useRef<number | null>(null);
  const [visiblePage, setVisiblePage] = useState(jump.page);
  const [size, setSize] = useState({ width: 600, height: 600 });
  const [sizing, setSizing] = useState("width");
  const [zoom, setZoom] = useState(1);
  const [selecting, setSelecting] = useState(false);
  const [preview, setPreview] = useState<PageBox | null>(null);
  const [navTab, setNavTab] = useState<NavTab>(null);
  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [activeMatch, setActiveMatch] = useState<SearchMatch | null>(null);
  const anchor = useRef<{ x: number; y: number; page: PageInfo } | null>(null);
  const resizeAnchor = useRef<{ page: number; ratio: number } | null>(null);

  const outlineQuery = useDocumentOutline(documentId, navTab === "outline");
  const searchQueryRes = useDocumentSearch(
    documentId,
    searchQuery,
    navTab === "search",
  );
  const candidatesQuery = useTableCandidates(
    documentId,
    navTab === "candidates",
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearchQuery(searchInput.trim());
    }, 260);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        setNavTab("search");
        requestAnimationFrame(() => {
          searchInputRef.current?.focus();
          searchInputRef.current?.select();
        });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  function navigateToPage(targetPage: number) {
    const clamped = Math.max(1, Math.min(pageCount, targetPage));
    if (onJumpPage) {
      onJumpPage(clamped);
    }
    if (reader.current) {
      const frame = reader.current.querySelector<HTMLElement>(
        `[data-source-page="${clamped}"]`,
      );
      if (frame) {
        reader.current.scrollTop +=
          frame.getBoundingClientRect().top -
          reader.current.getBoundingClientRect().top -
          8;
      }
    }
    setVisiblePage(clamped);
  }

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
          const pageNo = Number(sheets[middle].dataset.sourcePage);
          setVisiblePage(pageNo);
          if (pageNo !== visiblePage) onVisiblePage(pageNo);
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
      <div className="source-tools">
        <IconButton
          label="Document outline"
          aria-pressed={navTab === "outline"}
          className={navTab === "outline" ? "is-active" : undefined}
          onClick={() =>
            setNavTab((cur) => (cur === "outline" ? null : "outline"))
          }
        >
          <ListTree size={14} />
        </IconButton>
        <IconButton
          label="Find text in PDF (Ctrl+F)"
          aria-pressed={navTab === "search"}
          className={navTab === "search" ? "is-active" : undefined}
          onClick={() => {
            setNavTab((cur) => (cur === "search" ? null : "search"));
            requestAnimationFrame(() => searchInputRef.current?.focus());
          }}
        >
          <Search size={14} />
        </IconButton>
        <IconButton
          label="Find likely tables"
          aria-pressed={navTab === "candidates"}
          className={navTab === "candidates" ? "is-active" : undefined}
          onClick={() =>
            setNavTab((cur) => (cur === "candidates" ? null : "candidates"))
          }
        >
          <Table2 size={14} />
        </IconButton>
        <span className="rule-v" aria-hidden="true" />
        <IconButton
          label="Zoom out"
          disabled={currentScale <= 0.25}
          onClick={() => {
            setZoom(Math.max(0.2, currentScale - 0.15));
            setSizing("custom");
          }}
        >
          <Minus size={14} />
        </IconButton>
        <span className="zoom-value num">
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
          <Plus size={14} />
        </IconButton>
        <Select
          className="select-compact source-fit"
          aria-label="Page sizing"
          value={sizing}
          onChange={(event) => setSizing(event.target.value)}
        >
          <option value="width">Fit width</option>
          <option value="page">Fit page</option>
          <option value="custom">Custom</option>
        </Select>
        <span className="rule-v" aria-hidden="true" />
        <Button
          size="sm"
          variant={selecting ? "primary" : "ghost"}
          className="source-region-button"
          aria-pressed={selecting}
          icon={<ScanLine size={13} />}
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

      {navTab !== null && (
        <aside className="source-nav-drawer" aria-label="Document navigator">
          <header className="source-nav-head">
            <div className="source-nav-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={navTab === "outline"}
                className={navTab === "outline" ? "active" : undefined}
                onClick={() => setNavTab("outline")}
              >
                Contents
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={navTab === "search"}
                className={navTab === "search" ? "active" : undefined}
                onClick={() => setNavTab("search")}
              >
                Find
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={navTab === "candidates"}
                className={navTab === "candidates" ? "active" : undefined}
                onClick={() => setNavTab("candidates")}
              >
                Tables
              </button>
            </div>
            <IconButton
              label="Close navigator"
              size="sm"
              onClick={() => setNavTab(null)}
            >
              <X size={13} />
            </IconButton>
          </header>

          <div className="source-nav-body">
            {navTab === "outline" && (
              <div className="source-nav-section">
                {outlineQuery.isPending ? (
                  <p className="small muted">Loading contents…</p>
                ) : outlineQuery.isError ? (
                  <p className="small error">{outlineQuery.error.message}</p>
                ) : !(outlineQuery.data?.items.length ?? 0) ? (
                  <p className="small muted">
                    This PDF has no embedded outline bookmarks. Try Find or
                    Tables to jump directly.
                  </p>
                ) : (
                  <ul className="outline-list">
                    {outlineQuery.data!.items.map((item, idx) => (
                      <OutlineNode
                        key={`${idx}-${item.title}`}
                        item={item}
                        onJump={navigateToPage}
                      />
                    ))}
                  </ul>
                )}
              </div>
            )}

            {navTab === "search" && (
              <div className="source-nav-section">
                <div className="source-search-bar">
                  <Input
                    ref={searchInputRef}
                    data-pdf-search-input
                    aria-label="Search report text"
                    placeholder="Find revenue, EPS, balance sheet…"
                    value={searchInput}
                    onChange={(event) => setSearchInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        if (
                          event.nativeEvent.isComposing ||
                          event.keyCode === 229
                        )
                          return;
                        setSearchQuery(searchInput.trim());
                      } else if (event.key === "Escape") {
                        setNavTab(null);
                      }
                    }}
                  />
                </div>
                {!searchQuery ? (
                  <p className="tiny muted">
                    Type text to search all {pageCount} pages.
                  </p>
                ) : searchQueryRes.isPending ? (
                  <p className="small muted">Searching PDF…</p>
                ) : searchQueryRes.isError ? (
                  <p className="small error">{searchQueryRes.error.message}</p>
                ) : !searchQueryRes.data?.matches.length ? (
                  <p className="small muted">No matches for “{searchQuery}”.</p>
                ) : (
                  <>
                    <p className="tiny muted">
                      {searchQueryRes.data.total_matches} match
                      {searchQueryRes.data.total_matches === 1 ? "" : "es"}
                      {searchQueryRes.data.truncated
                        ? " (showing first 80)"
                        : ""}
                    </p>
                    <ul className="search-match-list">
                      {searchQueryRes.data.matches.map((match, idx) => {
                        const isSelected = activeMatch === match;
                        const before = match.snippet.slice(
                          0,
                          match.match_start,
                        );
                        const hit = match.snippet.slice(
                          match.match_start,
                          match.match_end,
                        );
                        const after = match.snippet.slice(match.match_end);
                        return (
                          <li key={`${match.page_no}-${idx}`}>
                            <button
                              type="button"
                              className={`search-match-item ${
                                isSelected ? "is-selected" : ""
                              }`}
                              onClick={() => {
                                setActiveMatch(match);
                                navigateToPage(match.page_no);
                              }}
                            >
                              <span className="mono tiny search-match-page">
                                p.{match.page_no}
                              </span>
                              <span className="search-match-snippet">
                                {before}
                                <mark>{hit}</mark>
                                {after}
                              </span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </>
                )}
              </div>
            )}

            {navTab === "candidates" && (
              <div className="source-nav-section">
                {candidatesQuery.isPending ? (
                  <p className="small muted">Scanning pages for tables…</p>
                ) : candidatesQuery.isError ? (
                  <p className="small error">{candidatesQuery.error.message}</p>
                ) : !(candidatesQuery.data?.candidates.length ?? 0) ? (
                  <p className="small muted">
                    No born-digital financial tables auto-detected. Scanned
                    pages still support region or OCR extraction.
                  </p>
                ) : (
                  <ul className="candidate-list">
                    {candidatesQuery.data!.candidates.map((cand) => (
                      <li key={cand.page_no} className="candidate-card">
                        <button
                          type="button"
                          className="candidate-main"
                          onClick={() => navigateToPage(cand.page_no)}
                        >
                          <div className="row spread">
                            <span className="mono tiny">p.{cand.page_no}</span>
                            <span className="candidate-score tiny">
                              <Sparkles size={10} aria-hidden="true" />
                              {Math.round(cand.score * 100)}%
                            </span>
                          </div>
                          <strong className="candidate-heading">
                            {cand.heading ||
                              `Financial data page ${cand.page_no}`}
                          </strong>
                          <span className="tiny muted">
                            {cand.numeric_lines} numeric rows ·{" "}
                            {cand.signals
                              .slice(0, 3)
                              .map((s) => s.replaceAll("_", " "))
                              .join(", ")}
                          </span>
                        </button>
                        {onQuickExtractPage && (
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => onQuickExtractPage(cand.page_no)}
                          >
                            Extract
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </aside>
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
              searchBoxes={
                activeMatch?.page_no === number ? activeMatch.boxes : []
              }
            />
          ),
        )}
      </div>
      <div className="source-foot">
        <span className="page-pill">
          Page <b>{visiblePage}</b>
          <span className="page-pill-total">/ {pageCount}</span>
        </span>
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
  searchBoxes = [],
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
  searchBoxes?: PageBox[];
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
          {searchBoxes.map((box, index) => (
            <div
              key={index}
              className="source-search-highlight"
              style={sourceBoxToPixels(box, { width, height })}
            />
          ))}
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

function OutlineNode({
  item,
  onJump,
}: {
  item: OutlineItem;
  onJump: (page: number) => void;
}) {
  return (
    <li className="outline-node">
      <button
        type="button"
        className="outline-item"
        disabled={item.page_no == null}
        style={{ paddingLeft: `${8 + Math.min(item.depth, 5) * 12}px` }}
        onClick={() => item.page_no != null && onJump(item.page_no)}
      >
        <span className="outline-title">{item.title}</span>
        {item.page_no != null && (
          <span className="mono tiny muted">p.{item.page_no}</span>
        )}
      </button>
      {item.children && item.children.length > 0 && (
        <ul className="outline-list">
          {item.children.map((child, idx) => (
            <OutlineNode
              key={`${idx}-${child.title}`}
              item={child}
              onJump={onJump}
            />
          ))}
        </ul>
      )}
    </li>
  );
}
