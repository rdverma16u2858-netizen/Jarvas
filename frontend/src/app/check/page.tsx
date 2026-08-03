/**
 * Check my work — submit an attempt and find out where it went wrong.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY TWO SEPARATE BOXES AND NOT ONE
 *   Students routinely restate the problem at the top of their working. With
 *   one box, the reviewer cannot tell which lines it is being asked to mark,
 *   and will occasionally "correct" the problem statement itself. Two labelled
 *   fields remove the ambiguity at the point where it would otherwise be
 *   guessed at.
 *
 * WHY THE PATTERN PANEL IS HERE RATHER THAN ON A PROGRESS PAGE
 *   "You have made six sign errors in your last ten attempts" is most useful
 *   at the moment you are about to make a seventh. Phase 8 will build the
 *   fuller progress view; this is the slice that belongs next to the input.
 */

"use client";

import { PageNav } from "@/components/PageNav";
import { useCallback, useEffect, useRef, useState } from "react";

import { ReviewCard } from "@/components/ReviewCard";
import { ApiError, IS_LOCAL_API, NetworkError } from "@/lib/api";
import {
  ERROR_LABEL,
  getPatterns,
  reviewWorking,
  type MistakePatterns,
  type ReviewResponse,
} from "@/lib/review";

const EXAMPLE = {
  problem: "Evaluate the integral of x·e^x dx",
  working: `Let u = x, dv = e^x dx
du = dx, v = e^x
∫x e^x dx = x e^x + ∫e^x dx
= x e^x + e^x + C`,
};

export default function CheckPage() {
  const [problem, setProblem] = useState("");
  const [working, setWorking] = useState("");
  const [result, setResult] = useState<ReviewResponse | null>(null);
  const [patterns, setPatterns] = useState<MistakePatterns | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");

  const abortRef = useRef<AbortController | null>(null);

  const loadPatterns = useCallback(() => {
    getPatterns()
      .then(setPatterns)
      .catch(() => setPatterns(null));
  }, []);

  useEffect(loadPatterns, [loadPatterns]);

  // Both fields can arrive from /scan when the photograph showed the working
  // as well as the question. Filled in, never submitted — the student still
  // confirms the transcription before anything is marked.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const scannedProblem = params.get("problem");
    const scannedWorking = params.get("working");
    if (scannedProblem) setProblem(scannedProblem);
    if (scannedWorking) setWorking(scannedWorking);
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
  }, []);

  const submit = useCallback(async () => {
    if (busy || !problem.trim() || !working.trim()) return;

    setBusy(true);
    setError("");
    setHint("");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      setResult(
        await reviewWorking(problem, working, { signal: controller.signal }),
      );
      loadPatterns();
    } catch (caught) {
      if ((caught as Error)?.name === "AbortError") {
        // Cancelled on purpose.
      } else if (caught instanceof NetworkError) {
        setError("Cannot reach the backend.");
        setHint(
          IS_LOCAL_API
            ? "Start it with:  cd backend && uvicorn app.main:app --reload"
            : "The API server is not responding. It may be starting up — wait a moment and try again.",
        );
      } else if (caught instanceof ApiError) {
        setError(
          caught.status === 429
            ? "Rate limited by the model provider."
            : `API error ${caught.status}: ${caught.message}`,
        );
        if (caught.status === 429) {
          setHint(
            "The free tier allows a limited number of requests. Wait a moment, or set LLM_PROVIDER=mock in .env.",
          );
        }
      } else {
        setError(String(caught));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }, [busy, problem, working, loadPatterns]);

  const ready = problem.trim().length > 1 && working.trim().length > 0;

  return (
    <main className="mx-auto flex min-h-dvh max-w-3xl flex-col px-4 sm:px-6">
      <header className="pt-10 pb-6">
        <PageNav />
        <div className="flex items-center gap-3">
          <h1 className="font-display text-3xl font-bold tracking-tight">
            Check my work
          </h1>
        </div>
        <p className="mt-1.5 text-sm leading-relaxed text-muted">
          Paste your own working and find out where it turned. If the computer
          algebra system says your answer is right, you are told so — even when
          the reviewer disagrees.
        </p>
      </header>

      {/* ── your recurring mistakes ─────────────────────────────────── */}
      {patterns?.most_common_error && (
        <div className="mb-4 rounded-xl border border-unverified/30 bg-unverified/5 p-4">
          <p className="text-[13px] leading-relaxed text-muted">
            Across your last {patterns.reviews} checks, your most common mistake
            is{" "}
            <span className="font-medium text-paper">
              {ERROR_LABEL[patterns.most_common_error]}
            </span>
            {patterns.by_topic[0] && (
              <>
                {" "}— mostly in{" "}
                <span className="text-paper">
                  {patterns.by_topic[0].topic.replace(/_/g, " ")}
                </span>
              </>
            )}
            .
          </p>
        </div>
      )}

      {/* ── the input ───────────────────────────────────────────────── */}
      <section className="rounded-2xl border border-line bg-slate p-4 sm:p-5">
        <label className="block">
          <span className="mb-1.5 block font-mono text-[10px] tracking-[0.14em] text-muted uppercase">
            The problem
          </span>
          <textarea
            value={problem}
            onChange={(e) => setProblem(e.target.value)}
            rows={2}
            placeholder="What were you asked to solve?"
            disabled={busy}
            className="w-full resize-y rounded-xl border border-line bg-ink px-3.5 py-2.5 text-[14px] text-paper placeholder:text-muted/60 focus:border-accent focus:outline-none disabled:opacity-50"
          />
        </label>

        <label className="mt-3 block">
          <span className="mb-1.5 block font-mono text-[10px] tracking-[0.14em] text-muted uppercase">
            Your working — one step per line
          </span>
          <textarea
            value={working}
            onChange={(e) => setWorking(e.target.value)}
            rows={7}
            placeholder={"Let u = x, dv = e^x dx\ndu = dx, v = e^x\n= x e^x + e^x + C"}
            disabled={busy}
            className="w-full resize-y rounded-xl border border-line bg-ink px-3.5 py-2.5 font-mono text-[13px] leading-relaxed text-paper placeholder:text-muted/60 focus:border-accent focus:outline-none disabled:opacity-50"
          />
        </label>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            onClick={() => void submit()}
            disabled={busy || !ready}
            className="rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
          >
            {busy ? "Reading your working…" : "Check it"}
          </button>

          {busy && (
            <button
              onClick={cancel}
              className="rounded-lg border border-line px-3 py-2 text-[12px] text-muted transition-colors hover:border-wrong hover:text-wrong"
            >
              Cancel
            </button>
          )}

          {!busy && !result && (
            <button
              onClick={() => {
                setProblem(EXAMPLE.problem);
                setWorking(EXAMPLE.working);
              }}
              className="rounded-lg border border-line px-3 py-2 text-[12px] text-muted transition-colors hover:border-accent hover:text-accent"
            >
              Use an example
            </button>
          )}
        </div>

        {busy && (
          <div
            className="mt-3 flex items-center gap-3 text-sm text-muted"
            aria-live="polite"
          >
            <span className="relative flex h-2 w-2 shrink-0">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
            </span>
            Solving it independently, then comparing against your working.
          </div>
        )}
      </section>

      {error && (
        <div className="mt-4 rounded-xl border border-wrong/40 bg-wrong/5 p-4">
          <p className="text-sm text-wrong">{error}</p>
          {hint && (
            <pre className="mt-2.5 overflow-x-auto rounded-lg bg-ink p-3 font-mono text-[11px] leading-relaxed text-muted">
              {hint}
            </pre>
          )}
        </div>
      )}

      <div className="flex-1 py-6">
        {result ? (
          <ReviewCard result={result} />
        ) : (
          !busy &&
          !error && (
            <p className="rounded-2xl border border-dashed border-line p-6 text-center text-sm text-muted">
              Paste an attempt above and press Check it.
            </p>
          )
        )}
      </div>
    </main>
  );
}
