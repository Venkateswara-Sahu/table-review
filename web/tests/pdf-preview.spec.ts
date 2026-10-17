import { test, expect } from "@playwright/test";
import { signIn } from "./helpers";

test("changing the visible page during a download rechecks memory admission", async ({
  page,
}) => {
  await signIn(page);
  const documents = await page.request.get("/api/v1/documents");
  const document = (await documents.json()).items.find(
    (item: { filename: string }) => item.filename === "long-report.pdf",
  );
  await page.route(/\/pages\/[67]$/, async (route) => {
    const number = Number(route.request().url().split("/").pop());
    await route.fulfill({
      json: {
        page_no: number,
        width: 3000,
        height: number === 6 ? 2000 : 1800,
        digital_text_present: false,
      },
    });
  });
  let release!: () => void, began!: () => void;
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    began = resolve;
  });
  await page.route(/\/pages\/6\/image/, async (route) => {
    began();
    await delayed;
    await route.continue();
  });
  const result = page.evaluate(async (id: string) => {
    const modulePath = "/src/features/review/PdfPreviews.ts";
    const { PdfPreviews } = await import(modulePath);
    const cache = new PdfPreviews(id, {
      maxPages: 24,
      maxBytes: 64 * 1024 * 1024,
    });
    const signal = new AbortController().signal;
    const large = await cache.preview(7, true, signal);
    await cache.preview(5, true, signal);
    const releaseSmall = cache.retain(5);
    Object.assign(window, {
      switchVisiblePreview: () => {
        releaseSmall();
        cache.retain(7);
      },
    });
    const outcome = await cache.preview(6, false, signal).then(
      () => "downloaded",
      (error: Error) => error.name,
    );
    const retained = await fetch(large.url).then(
      () => true,
      () => false,
    );
    cache.dispose();
    return { outcome, retained };
  }, document.id);
  await started;
  await page.evaluate(() =>
    (
      window as unknown as { switchVisiblePreview: () => void }
    ).switchVisiblePreview(),
  );
  release();
  expect(await result).toEqual({ outcome: "AbortError", retained: true });
});

test("initial viewer loading does not overwrite a page number being typed", async ({
  page,
}) => {
  await signIn(page);
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(/\/pages\/1$/, async (route) => {
    await delayed;
    await route.continue();
  });
  await page
    .getByRole("button", { name: "Open report.pdf", exact: true })
    .click();
  await page.getByLabel("Original page", { exact: true }).fill("2");
  release();
  await expect(page.getByTestId("source-image")).toBeVisible();
  await expect(page.getByLabel("Original page", { exact: true })).toHaveValue(
    "2",
  );
  await page.getByRole("button", { name: "View page", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Tables on page 2" }),
  ).toBeVisible();
});

test("small scroll steps keep the selected page synchronized", async ({
  page,
}) => {
  await signIn(page);
  await page
    .getByRole("button", { name: "Open long-report.pdf", exact: true })
    .click();
  await expect(page.getByTestId("source-image")).toBeVisible();
  const reader = page.getByRole("region", {
    name: "Scrollable report pages",
    exact: true,
  });
  const distance = await reader.evaluate((element) => {
    const next = element.querySelector('[data-source-page="2"]')!;
    return (
      next.getBoundingClientRect().top -
      element.getBoundingClientRect().top -
      element.clientHeight * 0.3
    );
  });
  for (let moved = 0; moved < distance; moved += 12) {
    await reader.evaluate((element) => {
      element.scrollTop += 12;
    });
    await page.evaluate(() => new Promise(requestAnimationFrame));
  }
  await expect(page.getByLabel("Original page", { exact: true })).toHaveValue(
    "2",
  );
});

test("viewed PDF pages are reused when returning to them", async ({ page }) => {
  await signIn(page);
  const images = new Map<number, number>();
  page.on("request", (request) => {
    const match = request.url().match(/\/pages\/(\d+)\/image/);
    if (match) {
      const number = Number(match[1]);
      images.set(number, (images.get(number) ?? 0) + 1);
    }
  });
  await page
    .getByRole("button", { name: "Open long-report.pdf", exact: true })
    .click();
  async function go(number: number) {
    await page
      .getByLabel("Original page", { exact: true })
      .fill(String(number));
    await page.getByLabel("Original page", { exact: true }).press("Enter");
    await expect(page.getByTestId("source-image")).toHaveAttribute(
      "alt",
      `Original report page ${number}`,
    );
  }
  // Navigation must survive a resize while the first reader layout settles.
  await page.setViewportSize({ width: 1420, height: 980 });
  await go(5);
  const originalUrl = await page
    .getByTestId("source-image")
    .getAttribute("src");
  const before = images.get(5);
  await go(10);
  await expect(page.locator('[data-source-page="5"]')).not.toBeInViewport();
  await go(5);
  await expect(page.getByTestId("source-image")).toHaveAttribute(
    "src",
    originalUrl!,
  );
  expect(images.get(5)).toBe(before);
  await page.getByRole("button", { name: "All reports", exact: true }).click();
  expect(
    await page.evaluate(
      (url) =>
        fetch(url).then(
          () => false,
          () => true,
        ),
      originalUrl!,
    ),
  ).toBe(true);
});

test("neighbor prefetch respects the memory reserved for the visible page", async ({
  page,
}) => {
  await signIn(page);
  const documents = await page.request.get("/api/v1/documents");
  const document = (await documents.json()).items.find(
    (item: { filename: string }) => item.filename === "long-report.pdf",
  );
  const result = await page.evaluate(async (id: string) => {
    const modulePath = "/src/features/review/PdfPreviews.ts";
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

test("preview cache evicts older pages and releases URLs when closed", async ({
  page,
}) => {
  await signIn(page);
  const documents = await page.request.get("/api/v1/documents");
  const document = (await documents.json()).items.find(
    (item: { filename: string }) => item.filename === "long-report.pdf",
  );
  const result = await page.evaluate(async (id: string) => {
    const modulePath = "/src/features/review/PdfPreviews.ts";
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
  const documents = await page.request.get("/api/v1/documents");
  const document = (await documents.json()).items.find(
    (item: { filename: string }) => item.filename === "long-report.pdf",
  );
  const order: number[] = [];
  page.on("request", (request) => {
    const match = request.url().match(/\/pages\/(\d+)\/image/);
    if (match) order.push(Number(match[1]));
  });
  await page.evaluate(async (id: string) => {
    const modulePath = "/src/features/review/PdfPreviews.ts";
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
      throw new Error("Cancelled queued preview ran");
    cache.dispose();
  }, document.id);
  expect(order).toEqual([10, 6]);
});

test("leaving during metadata loading skips the abandoned image request", async ({
  page,
}) => {
  await signIn(page);
  const documents = await page.request.get("/api/v1/documents");
  const document = (await documents.json()).items.find(
    (item: { filename: string }) => item.filename === "long-report.pdf",
  );
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
    const modulePath = "/src/features/review/PdfPreviews.ts";
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
