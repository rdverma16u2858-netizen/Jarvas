/**
 * Practice — generate questions on any topic, at any level, in any format.
 * ═══════════════════════════════════════════════════════════════════════
 *
 * WHY A SEPARATE ROUTE AND NOT A TAB IN THE CHAT
 *   Solving and practising are different postures. Chat is reactive — you
 *   arrive with a problem. Practice is deliberate — you choose a topic and a
 *   level and commit to a set. Collapsing them into one screen would mean the
 *   generation controls sit above the chat composer permanently, in the way
 *   of the thing people came for.
 *
 * WHY THE SET IS SHOWN BEFORE IT IS COMPLETE
 *   Generation is the slowest call in the product: ten questions is far more
 *   output than one solution, and each answer key is then verified. The
 *   request is not streamed — the schema has to validate as a whole — so the
 *   wait is covered by a stage message and the previous set stays on screen
 *   rather than being cleared into a blank page.
 *
 * ON THE REJECTED COUNT
 *   When SymPy contradicts a generated answer key the question is discarded,
 *   and the page says so. That number is the system working. Hiding it would
 *   make a set of five silently arrive as four.
 */

"use client";

import { PageNav } from "@/components/PageNav";
import { useCallback, useEffect, useRef, useState } from "react";

import { QuestionCard } from "@/components/QuestionCard";
import { ApiError, IS_LOCAL_API, NetworkError } from "@/lib/api";
import {
  bookmarkQuestion,
  generateQuestions,
  getVocabulary,
  humanise,
  listQuestions,
  recordAttempt,
  revealAnswer,
  type PracticeQuestion,
  type Vocabulary,
} from "@/lib/practice";

/** Sensible opening position: the topic most people arrive wanting. */
const DEFAULTS = {
  topic: "integral_calculus",
  difficulty: "jee_advanced",
  type: "multiple_choice",
  // Three verified questions are a useful first set without making the
  // student wait for five model completions and five symbolic checks.
  count: 3,
};

export default function PracticePage() {
  const [vocabulary, setVocabulary] = useState<Vocabulary | null>(null);
  const [topic, setTopic] = useState(DEFAULTS.topic);
  const [difficulty, setDifficulty] = useState(DEFAULTS.difficulty);
  const [type, setType] = useState(DEFAULTS.type);
  const [count, setCount] = useState(DEFAULTS.count);
  const [concepts, setConcepts] = useState("");

  const [questions, setQuestions] = useState<PracticeQuestion[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");

  const abortRef = useRef<AbortController | null>(null);

  // The vocabulary comes from the backend so the dropdowns cannot drift from
  // the Python enums that validate them.
  useEffect(() => {
    getVocabulary()
      .then(setVocabulary)
      .catch(() =>
        setError("Cannot reach the backend — start it and reload this page."),
      );
  }, []);

  // Accept a topic and level from the URL, so the progress page's
  // recommendation arrives here already applied.
  //
  // Read from window rather than `useSearchParams`, which would force this
  // page under a Suspense boundary purely to read two optional strings.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const wantedTopic = params.get("topic");
    const wantedDifficulty = params.get("difficulty");
    if (wantedTopic) setTopic(wantedTopic);
    if (wantedDifficulty) setDifficulty(wantedDifficulty);
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
  }, []);

  const generate = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    setHint("");
    setNotice("");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const result = await generateQuestions({
        topic,
        difficulty,
        type,
        count,
        concepts,
        signal: controller.signal,
      });

      setQuestions(result.questions);

      const parts = [`${result.questions.length} questions`];
      if (result.confirmed < result.questions.length) {
        parts.push(`${result.confirmed} answer keys verified`);
      }
      if (result.rejected > 0) {
        // Said plainly: a discarded question is the verifier doing its job,
        // and a set that quietly arrives short looks like a bug.
        parts.push(
          `${result.rejected} discarded — SymPy disagreed with the answer key`,
        );
      }
      setNotice(`${parts.join(" · ")} · ${(result.total_ms / 1000).toFixed(1)}s`);
    } catch (caught) {
      if ((caught as Error)?.name === "AbortError") {
        // Cancelled on purpose; nothing to report.
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
            : caught.status === 504
              ? "Question generation took too long."
            : `API error ${caught.status}: ${caught.message}`,
        );
        if (caught.status === 429) {
          setHint(
            "The free tier allows a limited number of requests. Wait a moment, or set LLM_PROVIDER=mock in .env.",
          );
        } else if (caught.status === 504) {
          setHint(
            "No questions were saved. Try again with one question or a less demanding level.",
          );
        }
      } else {
        setError(String(caught));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }, [busy, topic, difficulty, type, count, concepts]);

  const loadSaved = useCallback(async () => {
    setError("");
    try {
      const saved = await listQuestions({ bookmarkedOnly: true });
      setQuestions(saved);
      setNotice(
        saved.length ? `${saved.length} saved questions` : "Nothing saved yet.",
      );
    } catch {
      setError("Could not load your saved questions.");
    }
  }, []);

  const bookmark = useCallback(async (id: number, on: boolean) => {
    await bookmarkQuestion(id, on);
  }, []);

  return (
    <main className="mx-auto flex min-h-dvh max-w-3xl flex-col px-4 sm:px-6">
      <header className="pt-10 pb-6">
        <PageNav />
        <div className="flex items-center gap-3">
          <h1 className="font-display text-3xl font-bold tracking-tight">Practice</h1>
        </div>
        <p className="mt-1.5 text-sm leading-relaxed text-muted">
          Generated questions, with every answer key recomputed by a computer
          algebra system. Anything it contradicts is thrown away before you see
          it.
        </p>
      </header>

      {/* ── the controls ───────────────────────────────────────────── */}
      <section className="rounded-2xl border border-line bg-slate p-4 sm:p-5">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Topic">
            <Select
              value={topic}
              onChange={setTopic}
              options={vocabulary?.topics ?? [DEFAULTS.topic]}
            />
          </Field>
          <Field label="Difficulty">
            <Select
              value={difficulty}
              onChange={setDifficulty}
              options={vocabulary?.difficulties ?? [DEFAULTS.difficulty]}
            />
          </Field>
          <Field label="Format">
            <Select
              value={type}
              onChange={setType}
              options={vocabulary?.types ?? [DEFAULTS.type]}
            />
          </Field>
          <Field label={`How many (max ${vocabulary?.max_count ?? 20})`}>
            <input
              type="number"
              min={1}
              max={vocabulary?.max_count ?? 20}
              value={count}
              onChange={(e) => setCount(Number(e.target.value) || 1)}
              className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-[13px] text-paper focus:border-accent focus:outline-none"
            />
          </Field>
        </div>

        <Field label="Narrow it down (optional)">
          <input
            value={concepts}
            onChange={(e) => setConcepts(e.target.value)}
            placeholder="e.g. integration by parts, reduction formulas"
            className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-[13px] text-paper placeholder:text-muted/60 focus:border-accent focus:outline-none"
          />
        </Field>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            onClick={() => void generate()}
            disabled={busy}
            className="rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-paper transition-colors hover:bg-accent-soft disabled:opacity-40"
          >
            {busy ? "Writing questions…" : "Generate"}
          </button>

          {busy && (
            <button
              onClick={cancel}
              className="rounded-lg border border-line px-3 py-2 text-[12px] text-muted transition-colors hover:border-wrong hover:text-wrong"
            >
              Cancel
            </button>
          )}

          <button
            onClick={() => void loadSaved()}
            disabled={busy}
            className="rounded-lg border border-line px-3 py-2 text-[12px] text-muted transition-colors hover:border-accent hover:text-accent disabled:opacity-40"
          >
            ★ Saved
          </button>

          {notice && !busy && (
            <span className="font-mono text-[11px] text-muted">{notice}</span>
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
            Writing {count} {humanise(type)} questions, then verifying every
            answer.
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

      {/* ── the questions ──────────────────────────────────────────── */}
      <div className="flex-1 space-y-5 py-6">
        {questions.length === 0 && !busy && !error && (
          <p className="rounded-2xl border border-dashed border-line p-6 text-center text-sm text-muted">
            Pick a topic and press Generate.
          </p>
        )}

        {questions.map((question) => (
          <QuestionCard
            // Keyed by id so React rebuilds the card — and its picked/
            // submitted state — when a new set replaces an old one.
            key={question.id ?? `${question.number}-${question.prompt.slice(0, 24)}`}
            question={question}
            onReveal={revealAnswer}
            onAttempt={recordAttempt}
            onBookmark={bookmark}
          />
        ))}
      </div>
    </main>
  );
}

// ── small form pieces ─────────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="mt-3 block first:mt-0">
      <span className="mb-1.5 block font-mono text-[10px] tracking-[0.14em] text-muted uppercase">
        {label}
      </span>
      {children}
    </label>
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (value: string) => void;
  options: string[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-lg border border-line bg-ink px-3 py-2 text-[13px] text-paper focus:border-accent focus:outline-none"
    >
      {options.map((option) => (
        <option key={option} value={option}>
          {humanise(option)}
        </option>
      ))}
    </select>
  );
}
