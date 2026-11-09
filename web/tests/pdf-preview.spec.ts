import { test, expect } from "@playwright/test";
import type { Document } from "../src/api/models";
import { goToPage, openReport, signIn } from "./helpers";

async function longReport(page: import("@playwright/test").Page) {
  const documents = await page.request.get("/api/v1/documents");
  const document = (
    (await documents.json()) as { items: Document[] }
  ).items.find((item) => item.filename === "long-report.pdf");
  if (!document) throw new Error("long-report.pdf is missing");
  return document;
}

test("viewed pages reuse their preview URL and release it when the workspace closes", async ({
  page,
}) => {
  await openReport(page, "long-report.pdf");
  const images = new Map<number, number>();
  page.on("request", (request) => {
    const match = request.url().match(/\/pages\/(\d+)\/image/);
    if (match) {
      const number = Number(match[1]);
      images.set(number, (images.get(number) ?? 0) + 1);
    }
  });

  await goToPage(page, 5);
  const source = page.getByTestId("source-image");
  await expect(source).toHaveAttribute("alt", "Original report page 5");
  const originalUrl = await source.getAttribute("src");
  expect(originalUrl).not.toBeNull();
  const before = images.get(5) ?? 0;
  expect(before).toBeGreaterThan(0);

  await goToPage(page, 10);
  await expect(page.getByTestId("source-image")).toHaveAttribute(
    "alt",
    "Original report page 10",
  );
  await expect(page.locator('[data-source-page="5"]')).not.toBeInViewport();

  await goToPage(page, 5);
  await expect(page.getByTestId("source-image")).toHaveAttribute(
    "src",
    originalUrl!,
  );
  expect(images.get(5)).toBe(before);

  await page.getByRole("link", { name: "Back to reports" }).click();
  await expect
    .poll(() =>
      page.evaluate(
        (url) =>
          fetch(url).then(
            () => false,
            () => true,
          ),
        originalUrl!,
      ),
    )
    .toBe(true);
});

test("neighbor prefetch respects the memory reserved for the visible page", async ({
  page,
}) => {
  await signIn(page);
  const document = await longReport(page);
  const result = await page.evaluate(async (id: string) => {
    const modulePath = "/src/features/source/PdfPreviews.ts";
    const { PdfPreviews } = await import(modulePath);
    const cache = new PdfPreviews(id, {
      maxPages: 24,
      maxBytes: 5 * 1024 * 1024,
    });
    const signal = new AbortController().signal;
    const release = cache.retain(5);
    const visible = await cache.preview(5, true, signal);
    const neighbor = await cache.preview(6, false, signal).then(
      () => "downloaded",
      (error: Error) => error.name,
    );
    const retained = await fetch(visible.url).then(
      () => true,
      () => false,
    );
    release();
    await cache.preview(6, true, signal);
    cache.dispose();
    return { neighbor, retained };
  }, document.id);
  expect(result).toEqual({ neighbor: "AbortError", retained: true });
});

test("the cache evicts older pages and releases URLs when disposed", async ({
  page,
}) => {
  await signIn(page);
  const document = await longReport(page);
  const result = await page.evaluate(async (id: string) => {
    const modulePath = "/src/features/source/PdfPreviews.ts";
    const { PdfPreviews } = await import(modulePath);
    const cache = new PdfPreviews(id, {
      maxPages: 2,
      maxBytes: 96 * 1024 * 1024,
    });
    const signal = new AbortController().signal;
    const first = await cache.preview(5, true, signal);
    const second = await cache.preview(6, true, signal);
    const reused = await cache.preview(5, true, signal);
    await cache.preview(7, true, signal);
    const revoked = async (url: string) =>
      fetch(url).then(
        () => false,
        () => true,
      );
    const oldestRevoked = await revoked(second.url);
    const reusedAvailable = !(await revoked(first.url));
    cache.dispose();
    const byteCache = new PdfPreviews(id, {
      maxPages: 24,
      maxBytes: 5 * 1024 * 1024,
    });
    const large = await byteCache.preview(5, true, signal);
    await byteCache.preview(6, true, signal);
    const byteEvicted = await revoked(large.url);
    byteCache.dispose();
    return {
      reused: first.url === reused.url,
      oldestRevoked,
      reusedAvailable,
      disposed: await revoked(first.url),
      byteEvicted,
    };
  }, document.id);
  expect(result).toEqual({
    reused: true,
    oldestRevoked: true,
    reusedAvailable: true,
    disposed: true,
    byteEvicted: true,
  });
});

test("active previews precede queued neighbors and cancelled requests are skipped", async ({
  page,
}) => {
  await signIn(page);
  const document = await longReport(page);
  const order: number[] = [];
  page.on("request", (request) => {
    const match = request.url().match(/\/pages\/(\d+)\/image/);
    if (match) order.push(Number(match[1]));
  });
  await page.evaluate(async (id: string) => {
    const modulePath = "/src/features/source/PdfPreviews.ts";
    const { PdfPreviews } = await import(modulePath);
    const cache = new PdfPreviews(id);
    const keep = new AbortController(),
      cancel = new AbortController();
    const neighbor = cache.preview(6, false, keep.signal);
    const skipped = cache
      .preview(7, false, cancel.signal)
      .catch((error: Error) => error.name);
    const active = cache.preview(10, true, keep.signal);
    const duplicate = cache.preview(10, true, keep.signal);
    cancel.abort();
    await Promise.all([neighbor, active, duplicate]);
    if ((await skipped) !== "AbortError")
      throw new Error("A cancelled queued preview ran");
    cache.dispose();
  }, document.id);
  expect(order).toEqual([10, 6]);
});

test("leaving during metadata loading skips the abandoned image request", async ({
  page,
}) => {
  await signIn(page);
  const document = await longReport(page);
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  let began!: () => void;
  const started = new Promise<void>((resolve) => {
    began = resolve;
  });
  await page.route(/\/pages\/5$/, async (route) => {
    began();
    await delayed;
    await route.continue();
  });
  const images: number[] = [];
  page.on("request", (request) => {
    const match = request.url().match(/\/pages\/(\d+)\/image/);
    if (match) images.push(Number(match[1]));
  });
  const result = page.evaluate(async (id: string) => {
    const modulePath = "/src/features/source/PdfPreviews.ts";
    const { PdfPreviews } = await import(modulePath);
    const cache = new PdfPreviews(id);
    const cancel = new AbortController();
    Object.assign(window, { cancelPreview: () => cancel.abort() });
    const old = cache
      .preview(5, true, cancel.signal)
      .catch((error: Error) => error.name);
    await cache.preview(10, true, new AbortController().signal);
    const outcome = await old;
    cache.dispose();
    return outcome;
  }, document.id);
  await started;
  await page.evaluate(() =>
    (window as unknown as { cancelPreview: () => void }).cancelPreview(),
  );
  release();
  expect(await result).toBe("AbortError");
  expect(images).toEqual([10]);
});

test("disposing revives the controller so a later mount can load again", async ({
  page,
}) => {
  await signIn(page);
  const document = await longReport(page);
  const result = await page.evaluate(async (id: string) => {
    const modulePath = "/src/features/source/PdfPreviews.ts";
    const { PdfPreviews } = await import(modulePath);
    const cache = new PdfPreviews(id);
    const signal = new AbortController().signal;
    const first = await cache.preview(5, true, signal);
    cache.dispose();
    const revoked = await fetch(first.url).then(
      () => false,
      () => true,
    );
    const second = await cache.preview(5, true, signal);
    const available = await fetch(second.url).then(
      () => true,
      () => false,
    );
    cache.dispose();
    return {
      revoked,
      available,
      refreshed: first.url !== second.url,
    };
  }, document.id);
  expect(result).toEqual({
    revoked: true,
    available: true,
    refreshed: true,
  });
});
