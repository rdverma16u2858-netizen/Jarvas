/**
 * The login screen, and the decision about whether to show one.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY THE SERVER DECIDES
 *   Whether a password is needed is a property of the deployment, not of the
 *   build. `/auth/status` answers it, so the same bundle runs ungated on
 *   localhost and gated in public without a build flag that could be set
 *   wrongly in exactly one of those places.
 *
 * WHY NOTHING RENDERS UNTIL THE ANSWER ARRIVES
 *   Rendering the app first and swapping in a login screen a moment later
 *   would fire a page's worth of requests that all 401, and flash the
 *   interface at someone who is not signed in. The brief blank is the honest
 *   state: it is not yet known whether this person may see anything.
 *
 * ON WHAT THIS PROTECTS
 *   One password, shared, keeping a public URL from spending someone's API
 *   quota and exposing their study history. It is not multi-user auth and
 *   does not pretend to be — see backend/app/core/auth.py.
 */

"use client";

import { useCallback, useEffect, useState } from "react";

import { NetworkError, onUnauthorized } from "@/lib/api";
import { clearToken, fetchAuthStatus, login } from "@/lib/auth";

type Gate = "checking" | "open" | "locked" | "unreachable";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [gate, setGate] = useState<Gate>("checking");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const check = useCallback(async () => {
    try {
      const status = await fetchAuthStatus();
      setGate(!status.required || status.authenticated ? "open" : "locked");
    } catch (caught) {
      // Distinguished from "locked": a backend that is down is a different
      // problem from a password that is wrong, and telling someone to sign in
      // when the server is off sends them chasing the wrong thing.
      setGate(caught instanceof NetworkError ? "unreachable" : "unreachable");
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  // Any request coming back 401 mid-session re-gates the app, so an expired
  // token surfaces as a login screen rather than as errors on every action.
  useEffect(() => onUnauthorized(() => setGate("locked")), []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !password) return;

    setBusy(true);
    setError("");
    try {
      await login(password);
      setPassword("");
      setGate("open");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  if (gate === "open") return <>{children}</>;

  if (gate === "checking") {
    return (
      <main className="flex min-h-dvh items-center justify-center px-4">
        <span className="font-mono text-[11px] tracking-[0.16em] text-muted uppercase">
          Loading
        </span>
      </main>
    );
  }

  if (gate === "unreachable") {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-4">
        <h1 className="font-display text-2xl font-bold tracking-tight">
          Cannot reach the backend
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          The API is not responding. If you are running this locally, start it
          and then reload.
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg border border-line bg-slate p-3 font-mono text-[11px] leading-relaxed text-muted">
          cd backend{"\n"}python -m uvicorn app.main:app --reload --port 8000
        </pre>
        <button
          onClick={() => {
            setGate("checking");
            void check();
          }}
          className="mt-4 self-start rounded-xl border border-line px-4 py-2 text-[13px] text-muted transition-colors hover:border-accent hover:text-accent"
        >
          Try again
        </button>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-4">
      <h1 className="font-display text-3xl font-bold tracking-tight">MathBot</h1>
      <p className="mt-1.5 text-sm leading-relaxed text-muted">
        This instance is password protected.
      </p>

      <form onSubmit={submit} className="mt-6">
        <label className="block">
          <span className="mb-1.5 block font-mono text-[10px] tracking-[0.14em] text-muted uppercase">
            Password
          </span>
          <input
            type="password"
            autoFocus
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
            className="w-full rounded-xl border border-line bg-slate px-3.5 py-2.5 text-[15px] text-paper focus:border-accent focus:outline-none disabled:opacity-50"
          />
        </label>

        {error && <p className="mt-2.5 text-[13px] text-wrong">{error}</p>}

        <button
          type="submit"
          disabled={busy || !password}
          className="mt-4 w-full rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
        >
          {busy ? "Checking…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

/** Sign out. Placed in the sidebar rather than here, where it would only be
 *  visible on the screen you see when signed OUT. */
export function signOut(): void {
  clearToken();
  window.location.reload();
}
