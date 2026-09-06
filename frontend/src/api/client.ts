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
  /**
   * Sent as `X-User-ID`.
   *
   * Three states, and the difference matters:
   *
   * - **a string** — act as that person. An administrator acting on somebody
   *   else passes their own id here explicitly.
   * - **omitted** — act as whoever is selected in the workspace, via
   *   `setActiveUserId`. This is the default for user-scoped calls, so a new
   *   endpoint is scoped correctly without anybody remembering to thread an
   *   id through it.
   * - **`null`** — send no identity at all, deliberately. The shared corpus
   *   endpoints use this: `/documents`, `/search` and `/sync` are company-wide,
   *   and an identity header there would imply a per-user corpus that does not
   *   exist.
   */
  userId?: string | null;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
}

/**
 * Who the workspace is currently acting as.
 *
 * Module-level rather than passed through every call because it is ambient by
 * nature: one Digital Twin is selected at a time, and every user-scoped
 * request is made as them. `TwinProvider` is the only writer — it sets this
 * whenever the selection changes — and `request` reads it when a caller did
 * not name somebody explicitly.
 *
 * This is identity, not authentication. The backend trusts the header exactly
 * as sent, so this holds a convenience, not a credential.
 */
let activeUserId: string | null = null;

/** Set by `TwinProvider`. Null while no twin is selected, or none exists. */
export function setActiveUserId(userId: string | null): void {
  activeUserId = userId;
}

export function getActiveUserId(): string | null {
  return activeUserId;
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

/**
 * The identity a request should carry.
 *
 * `undefined` means "nobody said", which is the ordinary case for a
 * user-scoped call and resolves to the selected twin. An explicit `null` is a
 * decision to send none, and is honoured — that is what keeps the shared
 * corpus endpoints identity-free even while a twin is selected.
 */
function identityFor(userId: string | null | undefined): string | null {
  return userId !== undefined ? userId : activeUserId;
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, userId, query, signal } = options;

  const response = await fetch(buildUrl(path, query), {
    method,
    headers: headersFor(identityFor(userId), body !== undefined),
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (!response.ok) throw await toApiError(response);

  if (response.status === 204) return undefined as T;

  return (await response.json()) as T;
}

/**
 * A GET that returns a file rather than JSON.
 *
 * Fetched rather than linked. A plain `<a href>` download cannot carry the
 * identity header, which would mean putting the user id in the query string —
 * an identity in a URL is one that ends up in every access log and browser
 * history, and this endpoint returns somebody's private report.
 *
 * Returns the bytes and the filename the server chose, so the caller does not
 * invent one.
 */
export async function download(
  path: string,
  options: RequestOptions = {},
): Promise<{ blob: Blob; filename: string | null }> {
  const { userId, query, signal } = options;

  const response = await fetch(buildUrl(path, query), {
    headers: headersFor(identityFor(userId), false),
    signal,
  });

  if (!response.ok) throw await toApiError(response);

  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename=("?)([^";]+)\1/.exec(disposition);

  return { blob: await response.blob(), filename: match?.[2] ?? null };
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
  const identity = identityFor(options.userId);
  if (identity) headers.set("X-User-ID", identity);

  const response = await fetch(buildUrl(path), {
    method: "POST",
    headers,
    body: form,
    signal: options.signal,
  });

  if (!response.ok) throw await toApiError(response);

  return (await response.json()) as T;
}
