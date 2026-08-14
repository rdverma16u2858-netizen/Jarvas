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

import { Brand } from "./Brand";
import { IS_LOCAL_API, NetworkError, onUnauthorized } from "@/lib/api";
import { clearToken, fetchAuthStatus, login } from "@/lib/auth";

type Gate = "checking" | "waking" | "open" | "locked" | "unreachable";

/**
 * How long to keep trying before calling it dead.
 *
 * Free hosting spins a container down after ~15 minutes idle, and the first
 * request afterwards FAILS while it boots — a cold start on this image takes
 * 30-90 seconds. Giving up on that first failure is wrong: the server is not
 * broken, it is asleep, and it will answer shortly.
 *
 * Without this the app shows "cannot reach the backend" permanently after
 * every break, which reads as a broken deployment and is the single most
 * misleading thing it could say.
 */
// Sized against what was actually observed, not a guess: a free-tier cold
// start on this image runs 30-90 seconds, and on mobile data the round trips
// on top push it further. Giving up at 60s meant the gate failed on exactly
// the visits it exists to cover.
const WAKE_ATTEMPTS = 36;
const WAKE_INTERVAL_MS = 5000;

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [gate, setGate] = useState<Gate>("checking");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [waited, setWaited] = useState(0);

  const check = useCallback(async () => {
    for (let attempt = 0; attempt < WAKE_ATTEMPTS; attempt++) {
      try {
        const status = await fetchAuthStatus();
        setGate(!status.required || status.authenticated ? "open" : "locked");
        return;
      } catch (caught) {
        // A NetworkError here is usually a sleeping container rather than a
        // dead one. Anything else — a malformed response, a 500 — will not
        // improve by waiting, so it fails immediately.
        if (!(caught instanceof NetworkError)) {
          setGate("unreachable");
          return;
        }
        if (attempt === 0) setGate("waking");
        setWaited((attempt + 1) * (WAKE_INTERVAL_MS / 1000));
        await new Promise((resolve) => setTimeout(resolve, WAKE_INTERVAL_MS));
      }
    }
    setGate("unreachable");
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

  // The server is asleep, not broken. Saying so — and showing the wait
  // advancing — is the difference between "it is coming" and "it is dead".
  if (gate === "waking") {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-4">
        <Brand size="lg" />
        <div className="mt-12 flex items-center justify-center gap-3">
          <span className="relative flex h-1.5 w-1.5 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-muted opacity-60" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-muted" />
          </span>
          <p className="font-mono text-[11px] tracking-[0.14em] text-muted uppercase">
            Waking the server · {waited}s
          </p>
        </div>
        <p className="mx-auto mt-4 max-w-sm text-center text-[13px] leading-relaxed text-muted/70">
          Free hosting puts the backend to sleep after a quiet spell. The first
          visit takes up to a minute and a half to start it again; after that it
          is quick until the next idle period.
        </p>
      </main>
    );
  }

  if (gate === "unreachable") {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-4">
        <h1 className="font-display text-2xl font-bold tracking-tight">
          Cannot reach the server
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          {IS_LOCAL_API
            ? "The API is not responding. Start it and then reload."
            : "The API did not come back after a minute of trying. It may still be starting — wait a little and try again."}
        </p>
        {IS_LOCAL_API && (
          <pre className="mt-3 overflow-x-auto rounded-lg border border-line bg-slate p-3 font-mono text-[11px] leading-relaxed text-muted">
            cd backend{"\n"}python -m uvicorn app.main:app --reload --port 8000
          </pre>
        )}
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
      <Brand size="lg" />

      {/* Generous gap between the mark and the form: on a screen this empty,
          the space is what makes the wordmark read as a mark rather than as a
          label attached to the input below it. */}
      <form onSubmit={submit} className="mx-auto mt-14 w-full max-w-xs">
        <label className="block">
          <span className="mb-2 block text-center font-mono text-[10px] tracking-[0.16em] text-muted/70 uppercase">
            Password
          </span>
          <input
            type="password"
            autoFocus
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
            className="w-full rounded-xl border border-line bg-slate px-3.5 py-2.5 text-center text-[15px] tracking-[0.1em] text-paper focus:border-accent focus:outline-none disabled:opacity-50"
          />
        </label>

        {error && (
          <p className="mt-2.5 text-center text-[13px] text-wrong">{error}</p>
        )}

        <button
          type="submit"
          disabled={busy || !password}
          className="mt-3 w-full rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
        >
          {busy ? "Checking…" : "Sign in"}
        </button>

        <p className="mt-6 text-center text-[11px] text-muted/50">
          This instance is password protected.
        </p>
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
