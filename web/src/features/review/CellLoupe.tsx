import { useEffect, useRef, useState } from "react";
import type { Cell } from "../../api/models";
import type { PdfPreviews } from "../source/PdfPreviews";

export function CellLoupe({
  previews,
  cell,
  nightPaper = false,
}: {
  previews: PdfPreviews;
  cell?: Cell;
  nightPaper?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [caption, setCaption] = useState("");
  const [magnification, setMagnification] = useState("");
  const box = cell?.reference.box ?? null;
  const pageNo = cell?.reference.page ?? 0;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!box || !pageNo || !canvas) {
      setCaption("");
      setMagnification("");
      return;
    }
    const controller = new AbortController();
    const release = previews.retain(pageNo);
    let cancelled = false;

    async function render() {
      try {
        const preview = await previews.preview(pageNo, true, controller.signal);
        if (cancelled || controller.signal.aborted || !canvasRef.current)
          return;

        const img = new Image();
        img.src = preview.url;
        await new Promise<void>((resolve, reject) => {
          img.onload = () => resolve();
          img.onerror = () => reject(new Error("Failed to load loupe image"));
        });
        if (cancelled || controller.signal.aborted || !canvasRef.current)
          return;

        const targetCanvas = canvasRef.current;
        const padX = Math.max(32, (box!.right - box!.left) * 0.45);
        const padY = Math.max(18, (box!.bottom - box!.top) * 0.9);
        const cropL = Math.max(0, box!.left - padX);
        const cropT = Math.max(0, box!.top - padY);
        const cropR = Math.min(box!.page_width, box!.right + padX);
        const cropB = Math.min(box!.page_height, box!.bottom + padY);
        const cropW = Math.max(1, cropR - cropL);
        const cropH = Math.max(1, cropB - cropT);

        const sx = (cropL / box!.page_width) * img.naturalWidth;
        const sy = (cropT / box!.page_height) * img.naturalHeight;
        const sw = Math.max(1, (cropW / box!.page_width) * img.naturalWidth);
        const sh = Math.max(1, (cropH / box!.page_height) * img.naturalHeight);

        const maxW = 248;
        const maxH = 112;
        const scale = Math.min(maxW / sw, maxH / sh);
        const cssW = Math.max(60, Math.round(sw * scale));
        const cssH = Math.max(36, Math.round(sh * scale));
        const dpr = Math.min(2, window.devicePixelRatio || 1);

        targetCanvas.style.width = `${cssW}px`;
        targetCanvas.style.height = `${cssH}px`;
        targetCanvas.width = Math.round(cssW * dpr);
        targetCanvas.height = Math.round(cssH * dpr);

        const ctx = targetCanvas.getContext("2d");
        if (!ctx) return;
        ctx.clearRect(0, 0, targetCanvas.width, targetCanvas.height);
        ctx.drawImage(
          img,
          sx,
          sy,
          sw,
          sh,
          0,
          0,
          targetCanvas.width,
          targetCanvas.height,
        );

        const rx = ((box!.left - cropL) / cropW) * targetCanvas.width;
        const ry = ((box!.top - cropT) / cropH) * targetCanvas.height;
        const rw = ((box!.right - box!.left) / cropW) * targetCanvas.width;
        const rh = ((box!.bottom - box!.top) / cropH) * targetCanvas.height;

        ctx.fillStyle = "rgba(21, 94, 76, 0.16)";
        ctx.fillRect(rx, ry, rw, rh);
        ctx.strokeStyle = "#155e4c";
        ctx.lineWidth = Math.max(1.5, 1.8 * dpr);
        ctx.strokeRect(rx, ry, rw, rh);

        const zoomRatio = cssW / cropW;
        setMagnification(`${zoomRatio.toFixed(1)}×`);
        setCaption(`p.${pageNo} · ${cell!.reference.precision} crop`);
      } catch {
        if (!cancelled) {
          setCaption("");
          setMagnification("");
        }
      }
    }

    void render();
    return () => {
      cancelled = true;
      controller.abort();
      release();
    };
  }, [previews, pageNo, box, cell]);

  if (!box) return null;

  return (
    <div className="cell-loupe">
      <div className="cell-loupe-head">
        <span className="rail-label">Source loupe</span>
        {magnification && (
          <span className="cell-loupe-badge mono">{magnification}</span>
        )}
      </div>
      <div className="cell-loupe-frame">
        <span className="loupe-reticle loupe-reticle-tl" aria-hidden="true" />
        <span className="loupe-reticle loupe-reticle-br" aria-hidden="true" />
        <canvas
          ref={canvasRef}
          className={`cell-loupe-canvas ${nightPaper ? "is-night-paper" : ""}`}
          aria-label={`Zoomed source crop on page ${pageNo}`}
        />
      </div>
      {caption && <span className="tiny muted mono">{caption}</span>}
    </div>
  );
}
