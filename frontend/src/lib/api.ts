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
 * Typed wrapper around `fetch`.
 *
 * @param path  Path relative to API_BASE_URL, e.g. "/health"
 * @param init  Standard fetch options
 * @returns     The parsed JSON body, typed as T
 *
 * @throws {ApiError}     the server answered with a non-2xx status
 * @throws {NetworkError} the server could not be reached
 */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      // Health checks must never be served from a stale cache — that would
      // report the API as healthy after it had gone down.
      cache: "no-store",
    });
  } catch (cause) {
    // fetch only rejects on a genuine network failure, so reaching here means
    // the server is unreachable rather than unhappy.
    throw new NetworkError(
      `Cannot reach the API at ${url}. Is the backend running?`,
    );
  }

  if (!response.ok) {
    // Try to surface the backend's JSON error message; fall back to the
    // status text if the body is not JSON (e.g. a proxy's HTML error page).
    let body: unknown;
    let message = `${response.status} ${response.statusText}`;
    try {
      body = await response.json();
      if (body && typeof body === "object" && "message" in body) {
        message = String((body as { message: unknown }).message);
      }
    } catch {
      /* non-JSON body — keep the status-based message */
    }
    throw new ApiError(message, response.status, body);
  }

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
