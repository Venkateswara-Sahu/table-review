import { useEffect, useRef, useState } from "react";
import { apiBlob } from "../api/client";
import { FileIcon } from "./FileIcon";

type QueueItem = {
  documentId: string;
  signal: AbortSignal;
  resolve: (blob: Blob) => void;
  reject: (error: unknown) => void;
};

const queue: QueueItem[] = [];
let draining = false;

async function drain() {
  if (draining) return;
  draining = true;
  try {
    while (queue.length) {
      const next = queue.shift()!;
      if (next.signal.aborted) {
        next.reject(new DOMException("Aborted", "AbortError"));
        continue;
      }
      try {
        const blob = await apiBlob(
          `/api/v1/documents/${encodeURIComponent(next.documentId)}/thumbnail`,
          next.signal,
        );
        next.resolve(blob);
      } catch (error) {
        next.reject(error);
      }
    }
  } finally {
    draining = false;
  }
}

function enqueueThumbnail(
  documentId: string,
  signal: AbortSignal,
): Promise<Blob> {
  return new Promise<Blob>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    queue.push({ documentId, signal, resolve, reject });
    queueMicrotask(() => void drain());
  });
}

export function FolioThumbnail({
  documentId,
  ready,
}: {
  documentId: string;
  ready: boolean;
}) {
  const hostRef = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [url, setUrl] = useState("");

  useEffect(() => {
    const el = hostRef.current;
    if (!el || !ready) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "120px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ready]);

  useEffect(() => {
    if (!ready || !visible) return;
    const controller = new AbortController();
    let objectUrl = "";
    enqueueThumbnail(documentId, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        /* Fallback to the archival SVG folio icon silently. */
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, ready, visible]);

  return (
    <span ref={hostRef} className="folio-thumb" aria-hidden="true">
      {url ? (
        <img
          src={url}
          alt=""
          className="folio-thumb-img"
          decoding="async"
          width={38}
          height={50}
        />
      ) : (
        <FileIcon />
      )}
    </span>
  );
}
