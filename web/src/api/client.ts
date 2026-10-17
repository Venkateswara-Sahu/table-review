import { setActiveStudy } from "../features/review/StudyRecorder";

let csrfToken = "";
let sessionGeneration = 0;
const requests = new Set<AbortController>();

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: unknown = {},
  ) {
    super(message);
  }
}

export function setCsrf(token: string) {
  csrfToken = token;
}

export function clearPrivateState() {
  setActiveStudy(null);
  csrfToken = "";
  sessionGeneration++;
  for (const controller of requests) controller.abort();
  requests.clear();
}

async function request(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  if (!path.startsWith("/api/v1/")) throw new Error("Invalid API path");
  const controller = new AbortController();
  const generation = sessionGeneration;
  const originalSignal = options.signal;
  const abort = () => controller.abort();
  if (originalSignal?.aborted) controller.abort();
  originalSignal?.addEventListener("abort", abort, { once: true });
  requests.add(controller);
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (
    !["GET", "HEAD", "OPTIONS"].includes(
      (options.method ?? "GET").toUpperCase(),
    ) &&
    csrfToken
  ) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  try {
    for (let attempt = 0; ; attempt++) {
      const response = await fetch(path, {
        ...options,
        headers,
        credentials: "include",
        cache: "no-store",
        signal: controller.signal,
      });
      if (generation !== sessionGeneration)
        throw new DOMException("Session changed", "AbortError");
      if (!response.ok) {
        let code = "request_failed",
          message = "The request could not be completed",
          details: unknown = {};
        try {
          const value: unknown = await response.json();
          if (value && typeof value === "object") {
            if ("code" in value && typeof value.code === "string")
              code = value.code;
            if ("message" in value && typeof value.message === "string")
              message = value.message;
            if ("details" in value) details = value.details;
          }
        } catch {
          /* A proxy failure can have a non-JSON body. */
        }
        // The server's PDF slot already waits for admission. Retry only these
        // idempotent reads, at most twice, using the same abort/session boundary.
        if (
          response.status === 429 &&
          code === "pdf_busy" &&
          (options.method ?? "GET").toUpperCase() === "GET" &&
          /^\/api\/v1\/documents\/[^/]+\/pages\/\d+(?:\/image)?(?:\?|$)/.test(
            path,
          ) &&
          attempt < 2
        )
          continue;
        if (
          response.status === 401 &&
          !(path === "/api/v1/session" && options.method === "POST")
        ) {
          clearPrivateState();
          window.dispatchEvent(new Event("session-lost"));
        }
        throw new ApiError(response.status, code, message, details);
      }
      return response;
    }
  } finally {
    requests.delete(controller);
    originalSignal?.removeEventListener("abort", abort);
  }
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const generation = sessionGeneration;
  const response = await request(path, options);
  const value = response.status === 204 ? undefined : await response.json();
  if (generation !== sessionGeneration)
    throw new DOMException("Session changed", "AbortError");
  return value as T;
}

export async function apiBlob(
  path: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const generation = sessionGeneration;
  const response = await request(path, { signal });
  const blob = await response.blob();
  if (generation !== sessionGeneration)
    throw new DOMException("Session changed", "AbortError");
  return blob;
}

export function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "The request could not be completed";
}
