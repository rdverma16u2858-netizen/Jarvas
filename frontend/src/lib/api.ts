/**
 * The only place the frontend talks to the backend.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY THIS FILE EXISTS
 *   Scattering `fetch("http://localhost:8000/...")` across components means
 *   the base URL is hardcoded in a dozen places, every call re-implements
 *   error handling, and none of the responses are typed. Everything goes
 *   through `apiFetch` instead.
 *
 * WHY ERRORS BECOME A TYPED CLASS
 *   `fetch` does NOT reject on 404 or 500 — it resolves with `ok: false`.
 *   Code that forgets to check `response.ok` silently treats an error page as
 *   data. `apiFetch` checks once, here, and throws `ApiError` so callers can
 *   handle failures with try/catch and read `.status`.
 */

/**
 * Backend base URL.
 *
 * NEXT_PUBLIC_ prefix is required — Next.js only exposes variables with that
 * prefix to browser code. Without it this is `undefined` in the browser and
 * every request goes to the wrong origin.
 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

/**
 * True when the API lives on this machine.
 *
 * Gates the "start the backend with uvicorn" hint. On a deployed site that
 * instruction is not merely unhelpful, it is wrong — the visitor has no
 * checkout, no terminal, and nothing they could start. Telling them to run a
 * command sends them looking for a problem on their own computer when the
 * fault is a server they do not control.
 */
export const IS_LOCAL_API = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/.test(
  API_BASE_URL,
);

// Imported after API_BASE_URL: auth.ts reads it, and the other direction
// would be a cycle.
import { authHeaders, clearToken } from "./auth";

/** A non-2xx response. Carries the status code so callers can branch on it. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Thrown when the backend cannot be reached at all (not running, DNS, CORS). */
export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

/**
 * The session is over — wrong, missing or expired token.
 *
 * Separate from ApiError so callers can show the login screen instead of an
 * error message. A 401 is not a failure to be reported; it is a state to be
 * recovered from, and conflating the two means the user reads "API error 401"
 * and has no idea they simply need to sign in again.
 */
export class UnauthorizedError extends Error {
  constructor(message = "Please sign in again.") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

/** Notified whenever a request comes back 401, so the app can re-gate. */
const listeners = new Set<() => void>();

export function onUnauthorized(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function announceUnauthorized(): void {
  for (const listener of listeners) listener();
}

/**
 * Typed wrapper around `fetch`.
 *
 * @param path  Path relative to API_BASE_URL, e.g. "/health"
 * @param init  Standard fetch options
 * @returns     The parsed JSON body, typed as T
 *
 * @throws {ApiError}     the server answered with a non-2xx status
 * @throws {NetworkError} the server could not be reached
 */
/**
 * How long to keep retrying a request that never arrived.
 *
 * Free hosting sleeps the container after ~15 minutes idle and the first
 * request afterwards fails while it boots — measured at 30-90 seconds on this
 * image. Every page that loads data would otherwise show "cannot reach the
 * backend" on its first visit after any break, which is what made the app
 * look permanently broken while the server was merely waking.
 *
 * Retrying HERE rather than in each page is the point: quiz, practice,
 * progress, check and the sidebar all inherit it, and a page added later
 * inherits it too without anyone remembering to add it.
 */
const WAKE_RETRIES = 10;
const WAKE_DELAY_MS = 4000;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;

  // GET is the only method safe to retry after a 5xx: the request may have
  // been processed before the error, and re-sending a POST could solve a
  // problem twice or record a duplicate attempt — both of which cost quota.
  // A refused CONNECTION is different: nothing arrived, so any method is safe.
  const method = (init?.method ?? "GET").toUpperCase();
  const idempotent = method === "GET" || method === "HEAD";

  let response: Response | null = null;

  for (let attempt = 0; ; attempt++) {
    try {
      response = await fetch(url, {
        ...init,
        headers: {
          "Content-Type": "application/json",
          // Attached here, once, rather than at every call site. A route that
          // forgets it does not fail loudly — it just 401s in a way that looks
          // like the session expired.
          ...authHeaders(),
          ...init?.headers,
        },
        // Health checks must never be served from a stale cache — that would
        // report the API as healthy after it had gone down.
        cache: "no-store",
      });
    } catch (cause) {
      // A caller-initiated abort is not a network failure. Reporting it as
      // "the backend is down" would tell the user their server had crashed
      // when in fact they pressed Cancel — and it must never be retried.
      if ((cause as Error)?.name === "AbortError") throw cause;

      // Nothing reached the server, so retrying is safe for any method.
      if (attempt < WAKE_RETRIES) {
        await sleep(WAKE_DELAY_MS);
        continue;
      }

      throw new NetworkError(
        `Cannot reach the API at ${url}. Is the backend running?`,
      );
    }

    // A 5xx during a cold start is the container still coming up. Worth
    // waiting out, but only where re-sending cannot cause a second effect.
    if (response.status >= 500 && idempotent && attempt < WAKE_RETRIES) {
      await sleep(WAKE_DELAY_MS);
      continue;
    }

    break;
  }

  // The loop either assigns a response or throws, so this cannot be null —
  // but TypeScript cannot see that through the retry, and an assertion is
  // honest about which invariant is being relied on.
  if (response === null) {
    throw new NetworkError(`Cannot reach the API at ${url}.`);
  }

  if (response.status === 401) {
    // Drop the dead token so the next load shows the login screen rather than
    // retrying with credentials that will never work again.
    clearToken();
    announceUnauthorized();
    throw new UnauthorizedError();
  }

  if (!response.ok) {
    // Try to surface the backend's JSON error message; fall back to the
    // status text if the body is not JSON (e.g. a proxy's HTML error page).
    //
    // FastAPI puts the message in `detail` — that is what HTTPException
    // produces and therefore what every 404 and 503 in this API looks like.
    // `message` is checked too, for anything that does not come from FastAPI.
    let body: unknown;
    let message = `${response.status} ${response.statusText}`;
    try {
      body = await response.json();
      if (body && typeof body === "object") {
        const record = body as Record<string, unknown>;
        const detail = record.detail ?? record.message;
        // A 422 puts an array of field errors in `detail`; stringifying that
        // gives "[object Object]", so it is left to the status-based message.
        if (typeof detail === "string") message = detail;
      }
    } catch {
      /* non-JSON body — keep the status-based message */
    }
    throw new ApiError(message, response.status, body);
  }

  // 204 No Content has no body to parse, and calling .json() on it throws.
  // DELETE /conversations/{id} is exactly this case.
  if (response.status === 204) return undefined as T;

  return (await response.json()) as T;
}

// ── Response types ────────────────────────────────────────────────────────
// These mirror the Pydantic models in backend/app/schemas/. Keeping them in
// sync by hand is fine at this size; once the API grows, generate them from
// the backend's /openapi.json so there is one source of truth.

export type ComponentStatus = "up" | "down" | "degraded";

export interface ComponentHealth {
  status: ComponentStatus;
  detail: string;
  latency_ms: number | null;
}

export interface HealthResponse {
  status: "healthy" | "degraded" | "unhealthy";
  app: string;
  version: string;
  environment: string;
  components: Record<string, ComponentHealth>;
}

/** GET /health — full dependency report. */
export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}
