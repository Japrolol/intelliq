import type { ApiMessage } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly kind: "http" | "network";

  constructor(message: string, status = 0, kind: "http" | "network" = "http") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
  }
}

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function readCsrfCookie(): string | undefined {
  const entry = document.cookie
    .split("; ")
    .find((cookie) => cookie.startsWith("intelliq_csrf="));
  return entry ? decodeURIComponent(entry.slice("intelliq_csrf=".length)) : undefined;
}

async function ensureCsrfToken(): Promise<string | undefined> {
  // The backend rotates this non-HttpOnly cookie with the local session. Read
  // it per mutation so logout/login and refresh cannot reuse an old token.
  return readCsrfCookie();
}

async function readMessage(response: Response): Promise<string> {
  const body = (await response.json().catch(() => null)) as ApiMessage | null;
  if (body && typeof body.detail === "string") return body.detail;
  if (body && body.detail && typeof body.detail === "object") {
    const code =
      typeof body.detail.code === "string" ? humanizeCode(body.detail.code) : undefined;
    const message =
      typeof body.detail.message === "string" ? body.detail.message : undefined;
    if (message && code) return `${message} (${code})`;
    if (message || code)
      return (
        message || code || response.statusText || "The request could not be completed."
      );
  }
  if (body && typeof body.message === "string") return body.message;
  return response.statusText || "The request could not be completed.";
}

function humanizeCode(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  if (
    MUTATING_METHODS.has(method) &&
    typeof navigator !== "undefined" &&
    !navigator.onLine
  ) {
    throw new ApiError(
      "This action needs a connection. Reconnect before changing IntelliQ data.",
      0,
      "network",
    );
  }
  const csrf = !["GET", "HEAD", "OPTIONS"].includes(method)
    ? await ensureCsrfToken()
    : undefined;
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      credentials: "include",
      ...options,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
        ...(options.body && !isFormData ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
  } catch {
    throw new ApiError(
      "IntelliQ could not reach the API. Check that the local server is running.",
      0,
      "network",
    );
  }

  if (!response.ok) {
    throw new ApiError(await readMessage(response), response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const jsonBody = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});

export const putJsonBody = (body: unknown): RequestInit => ({
  method: "PUT",
  body: JSON.stringify(body),
});

export const formBody = (body: FormData): RequestInit => ({
  method: "POST",
  body,
});

export function scopedPath(organizationId: string, resource: string): string {
  return `/organizations/${encodeURIComponent(organizationId)}${resource}`;
}

export function getErrorMessage(
  error: unknown,
  fallback = "The API did not return the requested data.",
): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return fallback;
}
