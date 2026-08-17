/**
 * The one place this application talks to the backend.
 *
 * Every request goes through `request()`, so the base URL, the identity
 * header and error translation are decided once. A component that wants data
 * calls a named function in `api/`; none of them contain a `fetch`.
 */

import type { ApiErrorBody } from "./types";

/**
 * Requests go to a relative path by default, which the Vite dev server
 * proxies to the backend (see `vite.config.ts`). The backend registers no
 * CORS middleware, so a browser would refuse a cross-origin call — proxying
 * in development and serving the built files from the same origin in
 * production keeps every request same-origin and needs no backend change.
 */
export const API_BASE_URL: string =
  // Optional-chained rather than read directly: Vite replaces this at build
  // time, and leaving it unguarded makes the module unimportable anywhere
  // Vite is not — including the test runner.
  (import.meta.env?.VITE_API_BASE_URL ?? "/api").replace(/\/$/, "");

/** A failed call, carrying enough for the UI to say something specific. */
export class ApiError extends Error {
  readonly status: number;
  /** The backend's exception class name, e.g. `ProfileNotFoundError`. */
  readonly code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  /** No profile yet, no document by that id — a normal empty state. */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  /**
   * The caller sent no usable `X-User-ID`. Worth separating, because the
   * remedy is "choose a user", not "try again".
   */
  get isIdentityProblem(): boolean {
    return this.status === 401 || this.status === 422;
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** Sent as `X-User-ID`. Required by chat, profile and memory. */
  userId?: string | null;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
}

export function buildUrl(
  path: string,
  query?: RequestOptions["query"],
  baseUrl: string = API_BASE_URL,
): string {
  const url = `${baseUrl}${path.startsWith("/") ? path : `/${path}`}`;

  if (!query) return url;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.append(key, String(value));
  }

  const rendered = params.toString();

  return rendered ? `${url}?${rendered}` : url;
}

/**
 * Turn a failed response into an `ApiError`.
 *
 * The backend returns `{detail, error}` for every mapped domain error, so
 * `detail` is a sentence written for a person and is shown as-is. Anything
 * else — a proxy error page, a crash — gets a generic message rather than
 * whatever HTML happened to come back.
 */
export async function toApiError(response: Response): Promise<ApiError> {
  let detail = `Request failed (HTTP ${response.status}).`;
  let code = "HttpError";

  try {
    const body = (await response.json()) as Partial<ApiErrorBody>;
    if (typeof body.detail === "string" && body.detail) detail = body.detail;
    if (typeof body.error === "string" && body.error) code = body.error;
  } catch {
    // Not JSON. The status is all there is, and it is already in `detail`.
  }

  return new ApiError(detail, response.status, code);
}

function headersFor(userId: string | null | undefined, json: boolean): Headers {
  const headers = new Headers();

  if (json) headers.set("Content-Type", "application/json");
  // Identity, not authentication: the backend trusts this exactly as sent.
  if (userId) headers.set("X-User-ID", userId);

  return headers;
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, userId, query, signal } = options;

  const response = await fetch(buildUrl(path, query), {
    method,
    headers: headersFor(userId, body !== undefined),
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (!response.ok) throw await toApiError(response);

  if (response.status === 204) return undefined as T;

  return (await response.json()) as T;
}

/**
 * A multipart POST. Separate from `request` because the browser has to set
 * the multipart boundary itself — setting `Content-Type` by hand here is the
 * classic way to make a file upload fail with a parse error on the server.
 */
export async function upload<T>(
  path: string,
  form: FormData,
  options: { userId?: string | null; signal?: AbortSignal } = {},
): Promise<T> {
  const headers = new Headers();
  if (options.userId) headers.set("X-User-ID", options.userId);

  const response = await fetch(buildUrl(path), {
    method: "POST",
    headers,
    body: form,
    signal: options.signal,
  });

  if (!response.ok) throw await toApiError(response);

  return (await response.json()) as T;
}
