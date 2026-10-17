import { apiBlob, apiRequest } from "../../api/client";
import type { PageInfo } from "../../api/models";

type Preview = { url: string; bytes: number; metadata: PageInfo };
type Pending = {
  number: number;
  readers: Map<AbortSignal, boolean>;
  promise: Promise<Preview>;
  resolve: (value: Preview) => void;
  reject: (error: unknown) => void;
};

const aborted = () => new DOMException("Preview closed", "AbortError");
const live = (task: Pending) =>
  [...task.readers].some(([signal]) => !signal.aborted);
const priority = (task: Pending) =>
  [...task.readers].some(([signal, active]) => !signal.aborted && active)
    ? 0
    : 1;

/** Private, report-lifetime memory; no browser storage or HTTP cache. */
export class PdfPreviews {
  private controller = new AbortController();
  private info = new Map<number, Promise<PageInfo>>();
  private images = new Map<number, Preview>();
  private pins = new Map<number, number>();
  private pending = new Map<number, Pending>();
  private queue: Pending[] = [];
  private running = false;
  private bytes = 0;
  private listeners = new Set<(number: number) => void>();

  constructor(
    private documentId: string,
    private limits = { maxPages: 24, maxBytes: 96 * 1024 * 1024 },
  ) {}

  metadata(number: number): Promise<PageInfo> {
    if (this.controller.signal.aborted) return Promise.reject(aborted());
    let value = this.info.get(number);
    if (!value) {
      value = apiRequest<PageInfo>(
        `/api/v1/documents/${this.documentId}/pages/${number}`,
        { signal: this.controller.signal },
      ).catch((error) => {
        this.info.delete(number);
        throw error;
      });
      this.info.set(number, value);
    }
    return value;
  }

  retain(number: number) {
    this.pins.set(number, (this.pins.get(number) ?? 0) + 1);
    return () => {
      const remaining = (this.pins.get(number) ?? 1) - 1;
      if (remaining) this.pins.set(number, remaining);
      else this.pins.delete(number);
      this.trim();
    };
  }

  subscribe(listener: (number: number) => void) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  preview(
    number: number,
    active: boolean,
    signal: AbortSignal,
  ): Promise<Preview> {
    if (this.controller.signal.aborted || signal.aborted)
      return Promise.reject(aborted());
    const cached = this.images.get(number);
    if (cached) {
      this.images.delete(number);
      this.images.set(number, cached);
      return Promise.resolve(cached);
    }
    const existing = this.pending.get(number);
    if (existing) {
      existing.readers.set(signal, active);
      return existing.promise;
    }
    let resolve!: Pending["resolve"], reject!: Pending["reject"];
    const promise = new Promise<Preview>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    const task: Pending = {
      number,
      readers: new Map([[signal, active]]),
      promise,
      resolve,
      reject,
    };
    this.pending.set(number, task);
    this.queue.push(task);
    if (!this.running) {
      this.running = true;
      queueMicrotask(() => void this.drain());
    }
    return promise;
  }

  private async drain() {
    while (this.queue.length) {
      this.queue.sort((a, b) => priority(a) - priority(b));
      const task = this.queue.shift()!;
      try {
        if (this.controller.signal.aborted || !live(task)) throw aborted();
        const metadata = await this.metadata(task.number);
        if (this.controller.signal.aborted || !live(task)) throw aborted();
        const estimated =
          Math.ceil(metadata.width * 1.5) *
          Math.ceil(metadata.height * 1.5) *
          4;
        const reserved = () =>
          [...this.images].reduce(
            (sum, [number, image]) =>
              sum + (this.pins.has(number) ? image.bytes : 0),
            0,
          );
        // Only the active sheet is pinned. Background work waits until there is
        // room alongside it; scrolling makes a deferred neighbor active.
        if (estimated + reserved() > this.limits.maxBytes) throw aborted();
        const blob = await apiBlob(
          `/api/v1/documents/${this.documentId}/pages/${task.number}/image?scale=1.5`,
          this.controller.signal,
        );
        if (this.controller.signal.aborted) throw aborted();
        const preview = {
          metadata,
          url: URL.createObjectURL(blob),
          // Bound decoded RGBA memory as well as compressed PNG bytes.
          bytes: Math.max(blob.size, estimated),
        };
        // The active-page pins may have changed during this download.
        if (preview.bytes + reserved() > this.limits.maxBytes) {
          URL.revokeObjectURL(preview.url);
          throw aborted();
        }
        this.images.set(task.number, preview);
        this.bytes += preview.bytes;
        this.trim();
        if (this.images.get(task.number) !== preview) throw aborted();
        task.resolve(preview);
      } catch (error) {
        task.reject(error);
      } finally {
        this.pending.delete(task.number);
      }
    }
    this.running = false;
  }

  private trim() {
    for (const [number, preview] of this.images) {
      if (
        this.images.size <= this.limits.maxPages &&
        this.bytes <= this.limits.maxBytes
      )
        break;
      if (this.pins.has(number)) continue; // Visible sheets keep their image.
      URL.revokeObjectURL(preview.url);
      this.bytes -= preview.bytes;
      this.images.delete(number);
      for (const listener of this.listeners) listener(number);
    }
  }

  dispose() {
    this.controller.abort();
    for (const preview of this.images.values())
      URL.revokeObjectURL(preview.url);
    for (const task of this.queue) task.reject(aborted());
    this.queue = [];
    this.images.clear();
    this.info.clear();
    this.pins.clear();
    this.pending.clear();
    this.listeners.clear();
    this.bytes = 0;
  }
}
