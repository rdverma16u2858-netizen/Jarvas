/**
 * Scan — photograph a problem and have it read.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * THE CONFIRM STEP IS THE FEATURE
 *   The transcription lands in an EDITABLE box, and nothing is solved until
 *   the student presses a button. That friction is deliberate and is the only
 *   protection against the failure this whole flow invites: a misread problem
 *   is still a well-formed problem, so an automatic pipeline would return a
 *   fluent, SymPy-verified, entirely confident solution to a question the
 *   student never asked — and every check the app has would pass.
 *
 *   Nothing downstream can catch that. Only the student's eye can, and only
 *   if the interface makes them look.
 *
 * WHY THE DOUBTS SIT ABOVE THE TEXT
 *   "The upper limit could be 1 or 7" is worth more than any confidence
 *   score, and it is worthless below the fold. It goes directly above the box
 *   it refers to.
 */

"use client";

import Link from "next/link";
import { PageNav } from "@/components/PageNav";
import { useCallback, useEffect, useRef, useState } from "react";

import { Math, MathText } from "@/components/MathText";
import { ApiError, NetworkError } from "@/lib/api";
import {
  LEGIBILITY_STYLE,
  extractFromImage,
  getOcrLimits,
  type Extraction,
  type OcrLimits,
} from "@/lib/ocr";

export default function ScanPage() {
  const [limits, setLimits] = useState<OcrLimits | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [hint, setHint] = useState("");
  const [result, setResult] = useState<Extraction | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const previewRef = useRef<string | null>(null);

  useEffect(() => {
    getOcrLimits().then(setLimits).catch(() => {});
  }, []);

  // Object URLs hold the whole image in memory until revoked. Without this a
  // student trying six photographs leaks all six.
  useEffect(() => {
    return () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    };
  }, []);

  const run = useCallback(
    async (file: File) => {
      if (busy) return;

      if (limits && file.size > limits.max_bytes) {
        setError(
          `That image is ${(file.size / 1024 / 1024).toFixed(1)} MB, over the ${
            limits.max_megabytes
          } MB limit. Crop it to the single problem you want read.`,
        );
        return;
      }

      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
      const url = URL.createObjectURL(file);
      previewRef.current = url;
      setPreview(url);

      setBusy(true);
      setError("");
      setResult(null);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const extraction = await extractFromImage(file, {
          hint,
          signal: controller.signal,
        });
        setResult(extraction);
        setProblem(extraction.plain || extraction.problem);
      } catch (caught) {
        if ((caught as Error)?.name === "AbortError") {
          // Cancelled on purpose.
        } else if (caught instanceof NetworkError) {
          setError("Cannot reach the backend.");
        } else if (caught instanceof ApiError) {
          setError(caught.message);
        } else {
          setError(String(caught));
        }
      } finally {
        setBusy(false);
        abortRef.current = null;
      }
    },
    [busy, hint, limits],
  );

  // Paste is how most people move a screenshot, and it is invisible unless
  // the page says so.
  useEffect(() => {
    function onPaste(event: ClipboardEvent) {
      const file = [...(event.clipboardData?.items ?? [])]
        .find((i) => i.type.startsWith("image/"))
        ?.getAsFile();
      if (file) void run(file);
    }
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [run]);

  const style = result ? LEGIBILITY_STYLE[result.legibility] : null;

  return (
    <main className="mx-auto flex min-h-dvh max-w-3xl flex-col px-4 sm:px-6">
      <header className="pt-10 pb-6">
        <PageNav />
        <div className="flex items-center gap-3">
          <h1 className="font-display text-3xl font-bold tracking-tight">Scan</h1>
        </div>
        <p className="mt-1.5 text-sm leading-relaxed text-muted">
          Photograph a problem and it will be read into text. Check the reading
          before solving — a misread problem still looks solvable, and a wrong
          transcription would get a confident answer to the wrong question.
        </p>
      </header>

      {/* ── the drop zone ────────────────────────────────────────────── */}
      <section
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void run(file);
        }}
        className={`rounded-2xl border-2 border-dashed p-6 text-center transition-colors ${
          dragging ? "border-accent bg-accent/5" : "border-line"
        }`}
      >
        {preview ? (
          <img
            src={preview}
            alt="The image you uploaded"
            className="mx-auto max-h-64 rounded-xl border border-line"
          />
        ) : (
          <p className="text-sm text-muted">
            Drop an image here, paste one, or
          </p>
        )}

        <input
          ref={inputRef}
          type="file"
          accept={limits?.allowed_types.join(",") ?? "image/*"}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void run(file);
            // Cleared so re-picking the same file fires change again.
            e.target.value = "";
          }}
        />

        <button
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          className="mt-3 rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
        >
          {busy ? "Reading…" : preview ? "Choose another" : "Choose an image"}
        </button>

        {limits && (
          <p className="mt-2.5 font-mono text-[10px] text-muted/70">
            {limits.allowed_types.map((t) => t.split("/")[1]).join(" · ")} · up to{" "}
            {limits.max_megabytes} MB
          </p>
        )}
      </section>

      <label className="mt-3 block">
        <span className="mb-1.5 block font-mono text-[10px] tracking-[0.14em] text-muted uppercase">
          Which problem? (optional)
        </span>
        <input
          type="text"
          value={hint}
          onChange={(e) => setHint(e.target.value)}
          placeholder="e.g. question 14b — useful when the page holds several"
          className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-[13px] text-paper placeholder:text-muted/60 focus:border-accent focus:outline-none"
        />
      </label>

      {busy && (
        <div
          className="mt-4 flex items-center gap-3 text-sm text-muted"
          aria-live="polite"
        >
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
          </span>
          Reading the mathematics in the image.
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-xl border border-wrong/40 bg-wrong/5 p-4">
          <p className="text-sm text-wrong">{error}</p>
        </div>
      )}

      {/* ── the reading, for confirmation ────────────────────────────── */}
      {result && style && (
        <section className="mt-4 overflow-hidden rounded-2xl border border-line bg-slate">
          <header className="px-5 py-4 sm:px-6">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full border px-3 py-1 text-xs font-medium ${style.chip}`}
              >
                {style.label}
              </span>
              <span className="font-mono text-[11px] text-muted">
                {result.topic.replace(/_/g, " ")} ·{" "}
                {(result.total_ms / 1000).toFixed(1)}s
              </span>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-muted">{style.blurb}</p>

            {result.notes && (
              <p className="mt-2 text-xs leading-relaxed text-unverified">
                {result.notes}
              </p>
            )}
          </header>

          {/* The specific doubts, directly above the box they refer to. */}
          {result.uncertain.length > 0 && (
            <div className="border-t border-line bg-unverified/5 px-5 py-4 sm:px-6">
              <h3 className="mb-2 font-mono text-[11px] tracking-[0.16em] text-unverified uppercase">
                Check these
              </h3>
              <ul className="space-y-1.5">
                {result.uncertain.map((doubt, i) => (
                  <li key={i} className="flex gap-2.5 text-[13px] leading-relaxed">
                    <span
                      className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-unverified"
                      aria-hidden
                    />
                    <span className="text-muted">{doubt}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {result.usable && (
            <>
              <div className="border-t border-line px-5 py-4 sm:px-6">
                <h3 className="mb-2 font-mono text-[11px] tracking-[0.16em] text-muted uppercase">
                  What it read — edit anything wrong
                </h3>
                <textarea
                  value={problem}
                  onChange={(e) => setProblem(e.target.value)}
                  rows={3}
                  className="w-full resize-y rounded-xl border border-line bg-ink px-3.5 py-2.5 text-[14px] text-paper focus:border-accent focus:outline-none"
                />

                {result.problem && (
                  <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-ink px-3 py-2.5">
                    <Math latex={result.problem} />
                  </div>
                )}
              </div>

              {result.contains_working && result.working && (
                <div className="border-t border-line px-5 py-4 sm:px-6">
                  <h3 className="mb-2 font-mono text-[11px] tracking-[0.16em] text-muted uppercase">
                    Your working, also in the image
                  </h3>
                  <pre className="overflow-x-auto rounded-lg bg-ink p-3 font-mono text-[12px] leading-relaxed text-muted">
                    {result.working}
                  </pre>
                </div>
              )}

              {/* Solving is a separate, deliberate press. */}
              <div className="flex flex-wrap gap-2 border-t border-line px-5 py-4 sm:px-6">
                <Link
                  href={`/?problem=${encodeURIComponent(problem)}`}
                  className="rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-paper transition-colors hover:bg-accent-soft"
                >
                  Solve this
                </Link>

                {result.contains_working && result.working && (
                  <Link
                    href={`/check?problem=${encodeURIComponent(problem)}&working=${encodeURIComponent(result.working)}`}
                    className="rounded-xl border border-line px-5 py-2.5 text-sm text-muted transition-colors hover:border-accent hover:text-accent"
                  >
                    Check my working instead
                  </Link>
                )}
              </div>
            </>
          )}
        </section>
      )}

      <div className="flex-1" />
    </main>
  );
}
