/**
 * The shared password, client side.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY localStorage AND NOT A COOKIE
 *   The API is on a different origin from the site, so a cookie would need
 *   SameSite=None, Secure, and credentialed CORS — three things to get
 *   exactly right, each of which fails in a way that looks like something
 *   else. A token in a header has none of that.
 *
 *   The trade is that a cross-site scripting hole could read the token. That
 *   is worth naming rather than glossing: this app renders no user-supplied
 *   HTML, and KaTeX runs with `trust: false`, so there is no obvious injection
 *   point today. If one is ever added, this is the thing it would reach.
 *
 * WHY THE TOKEN IS READ FRESH ON EVERY REQUEST
 *   Caching it in a module variable means a token cleared in one tab keeps
 *   working in another until reload. Reading storage per request costs
 *   nothing and keeps the tabs honest with each other.
 */

const STORAGE_KEY = "mathbot.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private browsing can throw on access rather than returning null.
    return null;
  }
}

export function setToken(token: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, token);
  } catch {
    /* storage unavailable — the session lasts until reload, which still works */
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to do */
  }
}

/** The Authorization header, or nothing when signed out. */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ── talking to the server ─────────────────────────────────────────────────

import { API_BASE_URL, NetworkError } from "./api";

export interface AuthStatus {
  /** False on a local instance with no password set — no gate is shown. */
  required: boolean;
  authenticated: boolean;
}

/**
 * Ask whether a password is needed, and whether ours still works.
 *
 * Deliberately a server question. Hardcoding "this build needs a password"
 * in the client would mean the same code behaves differently by accident
 * between localhost and the deployed instance.
 */
export async function fetchAuthStatus(): Promise<AuthStatus> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/auth/status`, {
      headers: authHeaders(),
      cache: "no-store",
    });
  } catch (cause) {
    // MUST be a NetworkError, not the raw TypeError fetch throws. The gate
    // decides whether to keep retrying by checking for exactly this type, so
    // letting the native error through made a sleeping server look
    // permanently dead and skipped the wait entirely.
    throw new NetworkError(
      `Cannot reach the API at ${API_BASE_URL}. Is the backend running?`,
    );
  }

  // A 5xx during a cold start is the same situation as a refused connection:
  // the container is coming up and has not finished. Worth waiting out.
  if (response.status >= 500) {
    throw new NetworkError(`The API returned ${response.status} — it may be starting.`);
  }

  if (!response.ok) throw new Error(`auth status ${response.status}`);
  return (await response.json()) as AuthStatus;
}

export async function login(password: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });

  if (!response.ok) {
    let message = "That password is not right.";
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* non-JSON body */
    }
    throw new Error(message);
  }

  const { token } = (await response.json()) as { token: string };
  setToken(token);
}
